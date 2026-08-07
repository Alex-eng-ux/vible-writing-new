"""Canon 专用运行服务：Canon 运行创建、逐条决策与 chapter_revision.accepted 消费者。

Task 5C 边界：
- Canon 运行创建（章节/场景专用入口）生成独立 `generation_run_id`，通过 outbox
  入队，HTTP 请求绝不执行 LangGraph/CanonAgent。
- 章节 Canon 只能使用当前 accepted 且同步的章节版本；场景 Canon 只能使用当前
  accepted 场景版本。
- `chapter_revision.accepted` 幂等 outbox 消费者按
  `(chapter_id, accepted_chapter_revision_id)` 去重创建章节 Canon 运行；章节
  提交事务内只入队事件，绝不直接调用 CanonAgent。
- `submit_canon_decisions` 按持久 `candidate_id` 逐条校验 `confirm|reject|defer`，
  使用作者 `manual_command_id`、API command fence 与 `expected_run_version`，并经
  Task 4C 的 `confirm_canon_decisions` 正式更新路由（场景级不更新全局 Canon）。
- 普通运行入口仍拒绝 `target=canon`；Canon 不得调用 WritingAgent。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..api.schemas import CanonDecisionRequest, CanonRunCreateRequest
from ..db.models import (
    Chapter,
    ChapterHandoff,
    FactCandidate,
    GenerationRun,
    PlotThreadUpdate,
    Scene,
    TimelineEventCandidate,
)
from ..domain.commit_guard import CommitGuard
from ..domain.handoff import get_valid_entry
from ..domain.interfaces import CommandContext, RunWriteFence
from ..domain.lease import claim_api_command_fence
from ..domain.story_bible import confirm_canon_decisions
from ..errors import AppError
from ..runtime.outbox import PostgresRunOutbox
from ..runtime.run_events import PostgresRunEventStore
from .generation_runs import _author_ctx, _project_id_of, run_snapshot

# Canon 决策别名映射：HTTP 使用 confirm|reject|defer，领域使用
# accepted|rejected|deferred（Task 4C 正式决策状态机）。
_DECISION_MAP = {"confirm": "accepted", "reject": "rejected", "defer": "deferred"}

# 候选类型到模型映射（用于内容校验）。
# 三个候选模型结构同构（均含 generation_run_id/scope/status/content），
# 统一按 FactCandidate 的形状访问公共字段。
_CANDIDATE_MODELS = {
    "fact": FactCandidate,
    "timeline_event": TimelineEventCandidate,
    "plot_thread": PlotThreadUpdate,
}

# 合法叙事认识状态（与 CanonAgent 输出契约一致）。
_VALID_NARRATIVE_KNOWLEDGE = {
    "objective",
    "character_belief",
    "rumor",
    "lie",
    "dream",
    "metaphor",
    "unknown",
}

_EVENT_QUEUED = "run_queued"
_EVENT_ACCEPTED = "run_accepted"
_EVENT_CANCELLED = "run_cancelled"

# 这些状态仍可能继续产生候选；同一章节 accepted 修订的手动/自动请求必须复用它。
_ACTIVE_CANON_RUN_STATUSES = {
    "queued",
    "running",
    "waiting_feedback",
    "pending_clarification",
    "paused",
}


def _validate_candidate_content(session: Session, decisions: list[dict]) -> None:
    """按候选类型严格校验候选内容（来源段落引用/故事内有效时间/叙事认识状态）。

    内容在 CanonAgent 提取时已校验并持久化；此处为决策提交前的防御性再校验，
    防止绕过提取直接确认内容不完整的候选。每类候选都必须带：
    - 来源段落引用 `paragraph_ref`（非空）；
    - 故事内有效时间 `effective_story_time.value`（非空）；
    - 叙事认识状态 `narrative_knowledge`（合法枚举）。
    timeline_event 额外要求有效故事时间；plot_thread 额外要求状态或计划回收位置。
    失败条件均抛 SCENE_STATE_INCOMPATIBLE。
    """
    for decision in decisions:
        cand_type = decision.get("candidate_type", "fact")
        model_cls = _CANDIDATE_MODELS.get(cand_type)
        if model_cls is None:
            raise AppError("SCENE_STATE_INCOMPATIBLE", f"unknown candidate type {cand_type}")
        cand = session.get(model_cls, decision["candidate_id"])
        # 三个候选模型同构，统一按 FactCandidate 的形状访问公共字段 content。
        cand_row = cast(FactCandidate, cand) if cand is not None else None
        if cand_row is None:
            raise AppError("SCENE_STATE_INCOMPATIBLE", "candidate is missing")
        content = cand_row.content or {}
        # 来源段落引用：必须定位到具体段落。
        if not content.get("paragraph_ref"):
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                f"{cand_type} candidate requires source paragraph reference",
            )
        # 故事内有效时间：必须带非空 value。
        story_time = content.get("effective_story_time") or {}
        if not story_time.get("value"):
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                f"{cand_type} candidate requires effective_story_time",
            )
        # 叙事认识状态：必须是合法枚举。
        if content.get("narrative_knowledge") not in _VALID_NARRATIVE_KNOWLEDGE:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                f"{cand_type} candidate requires valid narrative_knowledge",
            )
        if cand_type == "plot_thread":
            # 伏笔状态：必须带状态或计划回收位置。
            if not content.get("state") and not content.get("planned_resolution"):
                raise AppError(
                    "SCENE_STATE_INCOMPATIBLE",
                    "plot_thread candidate requires state or planned_resolution",
                )


def _validate_candidate_bindings(
    session: Session,
    run: GenerationRun,
    decisions: list[dict],
    canon_scope: str,
) -> None:
    """决策前强制校验每个候选与当前 Canon 运行的绑定关系。

    校验项（任一失败均抛 AppError，且发生在任何写入之前，保证失败不修改
    候选状态、正式 Canon、运行版本或写入成功事件）：
    - 候选 ID 不得重复（COMMAND_CONTEXT_MISMATCH）；
    - 候选必须存在（SCENE_STATE_INCOMPATIBLE）；
    - 请求声明的 candidate_type 必须与候选实际类型一致；
    - 候选必须属于当前 Canon 运行（generation_run_id == run.id）；
    - 候选作用域必须等于运行作用域（canon_scope）；
    - 候选来源版本必须等于运行消费的 accepted 来源版本；
    - 候选目标章节/场景必须等于运行的目标章节/场景。

    该校验在 confirm_canon_decisions 之前执行；即使领域函数在逐条应用时
    还有自己的来源/作用域校验，此处先完成整体绑定检查，避免在幂等记录
    写入后才因单个候选绑定错误而失败。
    """
    seen: set[str] = set()
    for decision in decisions:
        candidate_id = decision["candidate_id"]
        if candidate_id in seen:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH", f"duplicate candidate id {candidate_id}"
            )
        seen.add(candidate_id)
        cand_type = decision.get("candidate_type", "fact")
        model_cls = _CANDIDATE_MODELS.get(cand_type)
        if model_cls is None:
            raise AppError("SCENE_STATE_INCOMPATIBLE", f"unknown candidate type {cand_type}")
        cand = session.get(model_cls, candidate_id)
        # 三个候选模型同构，统一按 FactCandidate 的形状访问公共字段。
        cand_row = cast(FactCandidate, cand) if cand is not None else None
        if cand_row is None:
            raise AppError("SCENE_STATE_INCOMPATIBLE", "candidate is missing")
        if cand_row.generation_run_id != run.id:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                "candidate does not belong to this canon run",
            )
        if cand_row.candidate_type != cand_type:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                f"candidate_type mismatch: candidate is {cand_row.candidate_type}, "
                f"request says {cand_type}",
            )
        if cand_row.scope != canon_scope:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                f"candidate scope {cand_row.scope} does not match canon_scope {canon_scope}",
            )
        if cand_row.source_revision_id != run.canon_source_revision_id:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                "candidate source revision does not match the canon run source",
            )
        if canon_scope == "chapter":
            if cand_row.scene_id is not None or cand_row.chapter_id != run.chapter_id:
                raise AppError(
                    "SCENE_STATE_INCOMPATIBLE",
                    "candidate target chapter does not match the canon run",
                )
        elif cand_row.scene_id != run.scene_id:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                "candidate target scene does not match the canon run",
            )


def _canon_run_input(body: CanonRunCreateRequest) -> dict:
    """构造 Canon 运行的不可变规范化输入信封。"""
    return {
        "canon_scope": body.canon_scope,
        "accepted_chapter_revision_id": body.accepted_chapter_revision_id,
        "accepted_scene_revision_id": body.accepted_scene_revision_id,
        "chapter_intent": body.chapter_intent,
        "author_feedback": body.author_feedback,
        "scene_base_revision_ids": body.scene_base_revision_ids,
    }


def start_canon_run(
    session: Session,
    actor_id: str,
    run_scope: str,
    target_id: str,
    body: CanonRunCreateRequest,
    manual_command_id: str,
    idempotency_key: str,
) -> dict:
    """原子创建一次 Canon 运行并返回 RunSnapshot（HTTP 不执行 CanonAgent）。

    参数：actor_id 为服务端解析身份；run_scope 为 chapter|scene；target_id 为
    URL 路径中的目标资源 id（章节/场景）；body 为 Canon 创建请求；
    manual_command_id 为幂等 claim 生成的人工命令 id；idempotency_key 为命令
    幂等键。
    返回：RunSnapshot 字典。

    失败条件：canon_scope 与 run_scope 不一致抛 COMMAND_CONTEXT_MISMATCH；
    资源不存在抛 CONTEXT_SOURCE_UNAVAILABLE；来源版本不是当前 accepted 抛
    SCENE_STATE_INCOMPATIBLE；章节未同步抛 CHAPTER_OUT_OF_SYNC。
    """
    if (run_scope == "chapter" and body.canon_scope != "chapter") or (
        run_scope == "scene" and body.canon_scope != "scene"
    ):
        raise AppError("COMMAND_CONTEXT_MISMATCH", "canon_scope must match run_scope")
    if run_scope == "chapter":
        chapter = session.get(Chapter, target_id)
        if chapter is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
        if body.accepted_chapter_revision_id is None:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "chapter canon requires accepted_chapter_revision_id",
            )
        if body.accepted_chapter_revision_id != chapter.accepted_chapter_revision_id:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                "chapter revision is not the current accepted version",
            )
        if chapter.chapter_sync_status != "in_sync":
            raise AppError(
                "CHAPTER_OUT_OF_SYNC",
                "chapter is not in sync; cannot create chapter canon run",
            )
        # 入口 handoff 校验：entry_handoff_status 必须符合计划规定的有效状态
        # （首章为 None 或 in_sync；in_sync 表示入口链仍与上游 accepted 一致）。
        if chapter.entry_handoff_status == "stale":
            raise AppError(
                "CHAPTER_HANDOFF_CONFLICT",
                "chapter entry handoff is stale; cannot create chapter canon run",
            )
        # 入口 handoff 与来源版本仍匹配：若章节存在 active 入口 handoff，校验
        # 其来源章节修订仍为 accepted 且等于来源章节当前 accepted 指针。
        if chapter.entry_handoff_status == "in_sync":
            entry = session.execute(
                select(ChapterHandoff).where(
                    ChapterHandoff.chapter_id == chapter.id,
                    ChapterHandoff.status == "active",
                )
            ).scalars().first()
            if entry is not None:
                valid = get_valid_entry(
                    session,
                    chapter.id,
                    entry.id,
                    entry.source_chapter_revision_id,
                    entry.chain_hash,
                )
                if valid is None:
                    raise AppError(
                        "CHAPTER_HANDOFF_CONFLICT",
                        "chapter entry handoff source version no longer matches",
                    )
        project_id = _project_id_of(session, chapter.id)
        chapter_id = chapter.id
        scene_id = None
        source_rev = body.accepted_chapter_revision_id
    else:
        scene = session.get(Scene, target_id)
        if scene is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene not found")
        if body.accepted_scene_revision_id is None:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "scene canon requires accepted_scene_revision_id",
            )
        if body.accepted_scene_revision_id != scene.accepted_scene_revision_id:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                "scene revision is not the current accepted version",
            )
        chapter = session.get(Chapter, scene.chapter_id)
        if chapter is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
        project_id = _project_id_of(session, chapter.id)
        chapter_id = chapter.id
        scene_id = scene.id
        source_rev = body.accepted_scene_revision_id

    if run_scope == "chapter":
        # 与 accepted outbox 消费者共享事务锁，避免手动入口和自动入口并发时
        # 在“查询后插入”之间产生两个相同来源的 Canon 运行。
        lock_key = f"canon-auto:{chapter_id}:{source_rev}"
        session.execute(
            text("SELECT pg_advisory_xact_lock((:lock_id)::bigint)"),
            {"lock_id": _advisory_lock_id(lock_key)},
        )
        existing = session.execute(
            select(GenerationRun).where(
                GenerationRun.chapter_id == chapter_id,
                GenerationRun.scene_id.is_(None),
                GenerationRun.decision_target == "canon",
                GenerationRun.canon_source_revision_id == source_rev,
                GenerationRun.status.in_(_ACTIVE_CANON_RUN_STATUSES),
            )
        ).scalars().first()
        if existing is not None:
            return run_snapshot(session, existing.id)

    run_id = str(uuid.uuid4())
    run = GenerationRun(
        id=run_id,
        project_id=project_id,
        chapter_id=chapter_id,
        scene_id=scene_id,
        request_type="review",
        decision_target="canon",
        status="queued",
        run_version=1,
        write_fencing_token=0,
        canon_source_revision_id=source_rev,
        normalized_input=_canon_run_input(body),
    )
    session.add(run)
    session.flush()
    # 运行创建事件 + outbox 入队（同一事务）；HTTP 不执行 CanonAgent。
    store = PostgresRunEventStore(session)
    store.emit(
        run_id,
        _EVENT_QUEUED,
        {"run_scope": run_scope, "request_type": "review"},
        fencing_token=0,
        producer_command_id=manual_command_id,
    )
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "run",
            "resource_id": run_id,
            "payload_schema": "run-event.v1",
            "payload": {"event_type": _EVENT_QUEUED, "run_id": run_id},
            "producer_command_id": manual_command_id,
            "generation_run_id": run_id,
        },
        fencing_token=0,
    )
    return run_snapshot(session, run_id)


def submit_canon_decisions(
    session: Session,
    actor_id: str,
    run_id: str,
    body: CanonDecisionRequest,
    manual_command_id: str,
) -> dict:
    """作者对 Canon 运行提交决策：confirm|reject|defer 逐条确认，或 cancel 取消。

    参数：actor_id 为服务端解析身份；run_id 为 Canon 运行 id；body 为 Canon
    决策请求；manual_command_id 为幂等 claim 生成的人工命令 id。
    返回：DecisionResponse 字典（run 快照、decision_id、command_id）。

    `decision="cancel"` 时：
    - `cancel_scope="confirm"`：取消本次确认，candidate_decisions 必须为空，
      未决候选保留 pending/deferred，不生成正式 Canon。
    - `cancel_scope="run"`：取消整个运行，candidate_decisions 必须为空，
      运行转 cancelled，未决候选原子转 discarded。
    取消与确认一样使用 manual_command_id、API command fence、CAS 与幂等 claim。

    失败条件：运行不是 canon 抛 RUN_STATE_CONFLICT；expected_run_version 不
    匹配抛 RUN_STATE_CONFLICT（CAS）；旧 fence 抛 RUN_LEASE_LOST；候选来源/
    作用域/状态不满足抛对应稳定错误码（由 confirm_canon_decisions 抛出）。
    """
    run = session.execute(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
    if run.decision_target != "canon":
        raise AppError("RUN_STATE_CONFLICT", "not a canon run")
    if run.run_version != body.expected_run_version:
        raise AppError("RUN_STATE_CONFLICT", "run version CAS mismatch")
    if run.status == "paused":
        raise AppError("RUN_STATE_CONFLICT", "paused runs can only be resumed")
    if run.status in ("accepted", "cancelled", "failed", "superseded"):
        raise AppError("RUN_STATE_CONFLICT", "run is in a terminal state")
    # Canon 决策只能在等待作者反馈（候选已就绪）时执行。
    if run.status != "waiting_feedback":
        raise AppError(
            "RUN_STATE_CONFLICT", "canon decisions require a waiting_feedback run"
        )
    # 作者决策：先取得 API command fence（source=author，不使用 Worker 租约）。
    fence = claim_api_command_fence(
        session, run_id, manual_command_id, body.expected_run_version
    )
    CommitGuard(session).validate(
        "apply_canon_decision",
        actor_id,
        None,
        body.idempotency_key,
        [],
        generation_run_id=None,
        manual_command_id=manual_command_id,
        expected_run_version=body.expected_run_version,
        lease_context=None,
        write_fence=fence,
    )
    ctx = _author_ctx(
        actor_id,
        manual_command_id,
        body.idempotency_key,
        body.expected_run_version,
        fence,
        author_decision="cancel" if body.decision == "cancel" else "accept",
    )
    if body.decision == "cancel":
        return _apply_canon_cancel(session, run, body, ctx, fence, manual_command_id)
    # 确认/拒绝/暂缓必须至少包含一条候选；只有 cancel 允许空候选列表。
    if not body.candidate_decisions:
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "confirm/reject/defer requires at least one candidate decision",
        )
    # 确认/拒绝/暂缓：校验并映射逐条决策
    # confirm|reject|defer -> accepted|rejected|deferred。
    decisions = [
        {
            "candidate_id": d.candidate_id,
            "candidate_type": d.candidate_type,
            "decision": _DECISION_MAP[d.decision],
            "local_key": d.local_key,
        }
        for d in body.candidate_decisions
    ]
    # 决策前强制校验候选与运行的绑定（归属/类型/作用域/来源/目标/去重），
    # 再校验候选内容字段；两者都在 confirm_canon_decisions 写入之前完成，
    # 校验失败不会修改候选状态、正式 Canon、运行版本或写入成功事件。
    _validate_candidate_bindings(session, run, decisions, body.canon_scope)
    # 防御性校验候选内容字段（来源版本/作用域/类型由 confirm_canon_decisions 校验）。
    _validate_candidate_content(session, decisions)
    if body.canon_scope == "chapter":
        chapter_id = run.chapter_id
        scene_id = None
    else:
        chapter_id = None
        scene_id = run.scene_id
    records = confirm_canon_decisions(
        session,
        run_id,
        decisions,
        ctx,
        canon_scope=body.canon_scope,
        chapter_id=chapter_id,
        scene_id=scene_id,
    )
    run.run_version += 1
    run.status = "accepted"
    session.flush()
    store = PostgresRunEventStore(session)
    store.emit(
        run_id,
        _EVENT_ACCEPTED,
        {"target": "canon", "decision": "accept", "run_version": run.run_version},
        fencing_token=fence["fencing_token"],
        producer_command_id=manual_command_id,
    )
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "run_decision",
            "resource_id": run_id,
            "payload_schema": "run-event.v1",
            "payload": {"event_type": _EVENT_ACCEPTED, "run_id": run_id, "target": "canon"},
            "producer_command_id": manual_command_id,
            "generation_run_id": run_id,
        },
        fencing_token=fence["fencing_token"],
    )
    return {
        "run": run_snapshot(session, run_id),
        "decision_id": records[0].id if records else "",
        "command_id": manual_command_id,
    }


def _apply_canon_cancel(
    session: Session,
    run: GenerationRun,
    body: CanonDecisionRequest,
    ctx: CommandContext,
    fence: RunWriteFence,
    manual_command_id: str,
) -> dict:
    """取消本次确认或取消整个 Canon 运行。

    - cancel_scope="confirm"：结束当前确认流程，未决候选保留 pending/deferred，
      绝不生成正式 Canon；运行回到 queued（可后续再次确认）。
    - cancel_scope="run"：运行转 cancelled，未决候选原子转 discarded。
    取消请求不得携带 candidate_decisions（否则 COMMAND_CONTEXT_MISMATCH）。
    """
    if body.candidate_decisions:
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "cancel request must not carry candidate_decisions",
        )
    if body.cancel_scope == "run":
        # 取消整个运行：未决候选原子转 discarded。
        _discard_pending_candidates(session, run, body.canon_scope)
        run.status = "cancelled"
        event_type = _EVENT_CANCELLED
    else:
        # 取消本次确认：保留未决候选（pending/deferred），不生成正式 Canon；
        # 运行回到 queued，等待后续再次进入确认。
        run.status = "queued"
        event_type = _EVENT_CANCELLED
    run.run_version += 1
    session.flush()
    store = PostgresRunEventStore(session)
    store.emit(
        run.id,
        event_type,
        {
            "target": "canon",
            "decision": "cancel",
            "cancel_scope": body.cancel_scope or "confirm",
            "run_version": run.run_version,
        },
        fencing_token=fence["fencing_token"],
        producer_command_id=manual_command_id,
    )
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "run_decision",
            "resource_id": run.id,
            "payload_schema": "run-event.v1",
            "payload": {
                "event_type": event_type,
                "run_id": run.id,
                "target": "canon",
                "cancel_scope": body.cancel_scope or "confirm",
            },
            "producer_command_id": manual_command_id,
            "generation_run_id": run.id,
        },
        fencing_token=fence["fencing_token"],
    )
    return {
        "run": run_snapshot(session, run.id),
        "decision_id": "",
        "command_id": manual_command_id,
    }


def _discard_pending_candidates(
    session: Session, run: GenerationRun, canon_scope: str
) -> None:
    """取消整个运行时，把该 Canon 运行所属的未决候选原子转为 discarded。

    只处理 scope 与运行作用域一致且状态仍为 pending 的候选；已决策（accepted/
    rejected/deferred）的候选保持不变。绝不调用 CanonAgent。
    """
    for model_cls in _CANDIDATE_MODELS.values():
        candidate_model = cast(type[FactCandidate], model_cls)
        rows = session.execute(
            select(candidate_model).where(
                candidate_model.generation_run_id == run.id,
                candidate_model.scope == canon_scope,
                candidate_model.status == "pending",
            )
        ).scalars().all()
        for row in rows:
            cand_row = cast(FactCandidate, row)
            cand_row.status = "discarded"
    session.flush()


def enqueue_chapter_accepted(
    session: Session,
    chapter_id: str,
    accepted_revision_id: str,
    producer_command_id: str,
) -> None:
    """入队 `chapter_revision.accepted` outbox 事件（章节提交事务内调用）。

    只入队事件，绝不直接调用 CanonAgent；Canon 消费者按
    (chapter_id, accepted_chapter_revision_id) 幂等创建章节 Canon 运行。
    """
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "chapter_revision",
            "resource_id": accepted_revision_id,
            "payload_schema": "canon-auto.v1",
            "payload": {
                "event_type": "chapter_revision.accepted",
                "chapter_id": chapter_id,
                "accepted_chapter_revision_id": accepted_revision_id,
            },
            "producer_command_id": producer_command_id,
            "generation_run_id": None,
        },
        fencing_token=0,
    )


def _advisory_lock_id(key: str) -> int:
    """把消费者互斥键哈希为 PostgreSQL advisory lock 的 64 位有符号整数键。

    同一 (chapter_id, accepted_chapter_revision_id) 的两个并发消费者得到相同
    锁键，事务级 advisory lock 保证它们串行化，后到者重查见已有运行即幂等返回。
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def handle_chapter_accepted_outbox(session: Session, payload: dict) -> None:
    """幂等处理 `chapter_revision.accepted`：按 (chapter_id, accepted 版本) 去重创建章节 Canon 运行。

    参数：payload 为 outbox 消息内容（chapter_id、accepted_chapter_revision_id）。
    安全性：两个消费者并发消费同一事件时，先用事务级 advisory lock 按
    (chapter_id, accepted_chapter_revision_id) 串行化（等价的原子 claim），
    后到者在锁释放后重查见已有运行即幂等返回；避免先 SELECT 后 INSERT 的
    TOCTOU 竞态产生重复 Canon 运行。手动 /canon-runs 入口不受该锁约束。
    绝不调用 CanonAgent。
    """
    chapter_id = payload["chapter_id"]
    accepted_revision_id = payload["accepted_chapter_revision_id"]
    # 原子 claim：按消费者互斥键取事务级 advisory lock（事务提交/回滚自动释放）。
    lock_key = f"canon-auto:{chapter_id}:{accepted_revision_id}"
    session.execute(
        text("SELECT pg_advisory_xact_lock((:lock_id)::bigint)"),
        {"lock_id": _advisory_lock_id(lock_key)},
    )
    existing = session.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter_id,
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == accepted_revision_id,
        )
    ).scalars().first()
    if existing is not None:
        return
    body = CanonRunCreateRequest(
        canon_scope="chapter",
        accepted_chapter_revision_id=accepted_revision_id,
    )
    start_canon_run(
        session,
        "canon-consumer",
        "chapter",
        chapter_id,
        body,
        manual_command_id=str(uuid.uuid4()),
        idempotency_key=f"canon-auto:{chapter_id}:{accepted_revision_id}",
    )


__all__ = [
    "start_canon_run",
    "submit_canon_decisions",
    "enqueue_chapter_accepted",
    "handle_chapter_accepted_outbox",
]
