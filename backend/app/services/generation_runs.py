"""生成运行服务：运行创建、快照、作者决策、暂停恢复与 SSE 事件重放。

Task 5B 边界：
- HTTP 请求只负责幂等 claim、写入运行记录、决策/事件/outbox，绝不在请求线程
  执行 LangGraph；Worker 通过 outbox 消费者/租约领取执行（Task 4A 提供）。
- 作者决策先幂等 claim，再取得 API command fence（`source=author`，
  `generation_run_id=None`），最后按 `expected_run_version` CAS 并写入
  `RunDecision`、`RunEvent` 与 outbox。
- `target=canon` 由通用运行入口拒绝（`CANON_NOT_ENABLED`），Canon 专用入口
  留给 Task 5C。
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.schemas import (
    DecisionRequest,
    ResumeRequest,
    RunCreateRequest,
    RunEventEnvelope,
)
from ..db.models import (
    Chapter,
    ChapterPlanRevision,
    ChapterPlanRevisionLink,
    GenerationRun,
    Scene,
    Volume,
)
from ..domain.chapters import (
    accept_chapter_plan_revision,
    commit_chapter_version,
    materialize_chapter_plan,
)
from ..domain.commit_guard import CommitGuard
from ..domain.drafts import commit_scene_draft
from ..domain.handoff import get_valid_entry
from ..domain.interfaces import CommandContext, RunWriteFence
from ..domain.lease import claim_api_command_fence
from ..domain.manuscript import commit_scene_change_set
from ..domain.story_bible import append_run_decision
from ..errors import AppError
from ..observability.sink import ObservabilitySink
from ..observability.trace import record_author_feedback
from ..observability.wiring import get_default_wiring
from ..runtime.outbox import PostgresRunOutbox
from ..runtime.run_events import PostgresRunEventStore

# 运行生命周期事件的稳定类型（Task 4A 事件表登记的事件类型）。
_EVENT_QUEUED = "run_queued"
_EVENT_RESUMED = "run_resumed"
_EVENT_ACCEPTED = "run_accepted"
_EVENT_CANCELLED = "run_cancelled"
_EVENT_WAITING_FEEDBACK = "run_waiting_feedback"


def _project_id_of(session: Session, chapter_id: str) -> str:
    """解析章节所属项目 id。"""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    volume = session.get(Volume, chapter.volume_id)
    if volume is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "volume not found")
    return volume.project_id


def _author_ctx(
    actor_id: str,
    manual_command_id: str,
    idempotency_key: str,
    expected_run_version: int | None,
    fence: RunWriteFence,
    author_decision: Literal["accept", "feedback", "cancel"] | None = None,
) -> CommandContext:
    """构造作者命令上下文：source=author、generation_run_id=None + API command fence。"""
    return {
        "lease_context": None,
        "write_fence": fence,
        "generation_run_id": None,
        "agent_run_id": None,
        "manual_command_id": manual_command_id,
        "source": "author",
        "parent_generation_run_id": None,
        "supersedes_run_id": None,
        "parent_plan_revision_id": None,
        "actor_id": actor_id,
        "preceding_chapter_id": None,
        "preceding_accepted_chapter_revision_id": None,
        "entry_handoff_id": None,
        "entry_source_chapter_revision_id": None,
        "entry_handoff_chain_hash": None,
        "base_scene_revision_id": None,
        "base_chapter_revision_id": None,
        "accepted_scene_revision_id": None,
        "accepted_chapter_revision_id": None,
        "plan_revision_id": None,
        "canon_scope": None,
        "decision_target": None,
        "context_source_refs": [],
        "author_decision": author_decision,
        "idempotency_key": idempotency_key,
        "expected_run_version": expected_run_version,
    }


def _cross_chapter_fields(body: RunCreateRequest) -> list[str | None]:
    """返回跨章节入口的五个 preceding/handoff 字段值列表。"""
    return [
        body.preceding_chapter_id,
        body.preceding_accepted_chapter_revision_id,
        body.entry_handoff_id,
        body.entry_source_chapter_revision_id,
        body.entry_handoff_chain_hash,
    ]


def _validate_cross_chapter_entry(
    session: Session,
    chapter: Chapter,
    body: RunCreateRequest,
) -> None:
    """严格校验跨章节入口（当前卷首章/紧邻前一章/来源 accepted 版本/交接匹配）。

    校验规则：
    - 当前卷首章：五个 preceding/handoff 字段必须全部为空。
    - 非首章：五个字段必须全部非空；`preceding_chapter_id` 必须是当前卷内按
      created_at 紧邻的上一章；来源章节版本必须是当前 accepted 版本；handoff
      来源与链哈希必须完全匹配（get_valid_entry）。

    失败条件（均抛 CHAPTER_HANDOFF_CONFLICT）：首章携带字段、非首章缺字段、
    前置章节不是紧邻上一章、来源章节版本不是当前 accepted、handoff 缺失或
    不匹配。
    """
    values = _cross_chapter_fields(body)
    all_empty = all(v is None for v in values)
    all_present = all(v is not None for v in values)
    # 按卷内 created_at 排序定位当前章的紧邻上一章；无则在卷首。
    prev = session.execute(
        select(Chapter)
        .where(
            Chapter.volume_id == chapter.volume_id,
            Chapter.created_at < chapter.created_at,
        )
        .order_by(Chapter.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if prev is None:
        if not all_empty:
            raise AppError(
                "CHAPTER_HANDOFF_CONFLICT",
                "first chapter must not carry preceding/handoff fields",
            )
        return
    if not all_present:
        raise AppError(
            "CHAPTER_HANDOFF_CONFLICT",
            "non-first chapter requires all preceding/handoff fields",
        )
    if prev.id != body.preceding_chapter_id:
        raise AppError(
            "CHAPTER_HANDOFF_CONFLICT",
            "preceding_chapter_id is not the immediately preceding chapter",
        )
    if prev.accepted_chapter_revision_id is None:
        raise AppError(
            "CHAPTER_HANDOFF_CONFLICT", "preceding chapter has no accepted revision"
        )
    if body.preceding_accepted_chapter_revision_id != prev.accepted_chapter_revision_id:
        raise AppError(
            "CHAPTER_HANDOFF_CONFLICT",
            "preceding chapter revision is not the current accepted version",
        )
    if body.entry_source_chapter_revision_id != prev.accepted_chapter_revision_id:
        raise AppError(
            "CHAPTER_HANDOFF_CONFLICT",
            "entry source revision does not match the preceding accepted revision",
        )
    entry = get_valid_entry(
        session,
        chapter.id,
        body.entry_handoff_id,
        body.entry_source_chapter_revision_id,
        body.entry_handoff_chain_hash,
    )
    if entry is None:
        raise AppError(
            "CHAPTER_HANDOFF_CONFLICT", "chapter handoff is missing or invalid"
        )


def _validate_run_create(
    session: Session,
    chapter: Chapter,
    scene: Scene | None,
    body: RunCreateRequest,
) -> None:
    """运行创建前的统一校验（plan 指针、Canon 字段、章节 handoff、基线）。

    失败条件（均抛稳定 AppError）：
    - 普通运行携带 Canon 专用字段（canon_scope/accepted_scene_revision_id）抛
      CANON_USE_DEDICATED_ENDPOINT。
    - 非「首次 chapter + new_chapter」缺少 plan_revision_id 抛
      PLAN_REVISION_CONFLICT；章节已有 accepted plan 时再以 new_chapter 无
      plan_revision_id 创建抛 PLAN_REVISION_CONFLICT。
    - plan_revision_id 不属于该章节或不是当前 accepted plan 抛
      PLAN_REVISION_CONFLICT。
    - 跨章节运行缺少有效 handoff 抛 CHAPTER_HANDOFF_CONFLICT。
    - 场景基线过期抛 SCENE_STALE；章节基线过期抛 CHAPTER_OUT_OF_SYNC。
    """
    # 普通运行拒绝 Canon 专用字段（Canon 走专用入口，Task 5C）。
    if body.canon_scope is not None or body.accepted_scene_revision_id is not None:
        raise AppError(
            "CANON_USE_DEDICATED_ENDPOINT",
            "canon fields are not allowed on the generic run endpoint",
        )
    # 首次 chapter + new_chapter 才允许没有 plan_revision_id；其余场景/章节运行
    # 必须携带并校验当前 accepted plan。
    if body.plan_revision_id is None:
        if not (body.run_scope == "chapter" and body.request_type == "new_chapter"):
            raise AppError(
                "PLAN_REVISION_CONFLICT",
                "run requires the current accepted plan revision",
            )
        link = session.execute(
            select(ChapterPlanRevisionLink).where(
                ChapterPlanRevisionLink.chapter_id == chapter.id
            )
        ).scalar_one_or_none()
        if link is not None:
            raise AppError(
                "PLAN_REVISION_CONFLICT",
                "chapter already has an accepted plan; plan_revision_id is required",
            )
    else:
        plan = session.get(ChapterPlanRevision, body.plan_revision_id)
        if plan is None or plan.chapter_id != chapter.id:
            raise AppError(
                "PLAN_REVISION_CONFLICT", "plan revision does not belong to the chapter"
            )
        link = session.execute(
            select(ChapterPlanRevisionLink).where(
                ChapterPlanRevisionLink.chapter_id == chapter.id
            )
        ).scalar_one_or_none()
        if link is None or link.plan_revision_id != body.plan_revision_id:
            raise AppError(
                "PLAN_REVISION_CONFLICT", "plan revision is not the current accepted plan"
            )
        if plan.status != "accepted":
            raise AppError("PLAN_REVISION_CONFLICT", "plan revision is not accepted")
    # 严格校验跨章节入口（首章/紧邻前一章/来源 accepted 版本/交接匹配）。
    _validate_cross_chapter_entry(session, chapter, body)
    # 场景基线：已有 accepted 版本时必须匹配；首次生成不允许携带基线。
    if scene is not None:
        if scene.accepted_scene_revision_id is not None:
            if body.base_scene_revision_id != scene.accepted_scene_revision_id:
                raise AppError("SCENE_STALE", "scene baseline is stale; refresh and retry")
        elif body.base_scene_revision_id is not None:
            raise AppError(
                "SCENE_STALE", "scene has no accepted revision; base must be null"
            )
    # 章节基线：提供时必须等于章节当前 accepted 指针。
    if body.base_chapter_revision_id is not None:
        if body.base_chapter_revision_id != chapter.accepted_chapter_revision_id:
            raise AppError("CHAPTER_OUT_OF_SYNC", "chapter baseline is out of sync")


def start_generation_run(
    session: Session,
    actor_id: str,
    target_id: str,
    body: RunCreateRequest,
    manual_command_id: str,
    idempotency_key: str,
) -> dict:
    """原子创建一次生成运行并返回 RunSnapshot（HTTP 请求不执行 LangGraph）。

    参数：actor_id 为服务端解析身份；target_id 为 URL 路径中的目标资源 id
    （run_scope=chapter 时为 chapter_id，run_scope=scene 时为 scene_id）；
    body 为运行创建请求；manual_command_id 为幂等 claim 生成的人工命令 id；
    idempotency_key 为命令幂等键。
    返回：RunSnapshot 字典。

    失败条件：target=canon 抛 CANON_NOT_ENABLED；资源不存在抛
    CONTEXT_SOURCE_UNAVAILABLE；创建前统一校验失败抛对应错误码
    （见 _validate_run_create）。
    """
    if body.decision_target == "canon":
        raise AppError(
            "CANON_NOT_ENABLED",
            "canon runs use a dedicated endpoint; not enabled in this stage",
        )
    run_id = str(uuid.uuid4())
    if body.run_scope == "chapter":
        chapter = session.get(Chapter, target_id)
        if chapter is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
        project_id = _project_id_of(session, target_id)
        chapter_id = target_id
        scene = None
        scene_id = None
    else:
        scene = session.get(Scene, target_id)
        if scene is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene not found")
        chapter = session.get(Chapter, scene.chapter_id)
        if chapter is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
        chapter_id = scene.chapter_id
        project_id = _project_id_of(session, chapter_id)
        scene_id = scene.id
    _validate_run_create(session, chapter, scene, body)

    run = GenerationRun(
        id=run_id,
        project_id=project_id,
        chapter_id=chapter_id,
        scene_id=scene_id,
        plan_revision_id=body.plan_revision_id,
        request_type=body.request_type,
        decision_target=body.decision_target,
        status="queued",
        run_version=1,
        write_fencing_token=0,
        normalized_input=_normalize_run_input(body),
    )
    session.add(run)
    session.flush()
    # 运行创建事件 + outbox 入队（同一事务）。
    store = PostgresRunEventStore(session)
    store.emit(
        run_id,
        _EVENT_QUEUED,
        {"run_scope": body.run_scope, "request_type": body.request_type},
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


def get_run(session: Session, run_id: str) -> GenerationRun:
    """读取运行行；不存在抛 RUN_STATE_CONFLICT。"""
    run = session.get(GenerationRun, run_id)
    if run is None:
        raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
    return run


def _normalize_run_input(body: RunCreateRequest) -> dict:
    """构造不可变规范化运行输入信封（Worker 仅凭 run_id 重建输入）。

    只保留首次请求的规范化字段（chapter_intent、author_feedback、
    plan_revision_id、handoff 字段、场景/章节基线、scene_base_revision_ids）。
    该信封在创建时持久化且不可修改；重试绝不重新读取客户端输入。
    """
    return {
        "run_scope": body.run_scope,
        "request_type": body.request_type,
        "decision_target": body.decision_target,
        "plan_revision_id": body.plan_revision_id,
        "preceding_chapter_id": body.preceding_chapter_id,
        "preceding_accepted_chapter_revision_id": body.preceding_accepted_chapter_revision_id,
        "entry_handoff_id": body.entry_handoff_id,
        "entry_source_chapter_revision_id": body.entry_source_chapter_revision_id,
        "entry_handoff_chain_hash": body.entry_handoff_chain_hash,
        "base_scene_revision_id": body.base_scene_revision_id,
        "base_chapter_revision_id": body.base_chapter_revision_id,
        "scene_base_revision_ids": body.scene_base_revision_ids,
        "chapter_intent": body.chapter_intent,
        "author_feedback": body.author_feedback,
    }


def get_run_input_envelope(session: Session, run_id: str) -> dict:
    """读取唯一不可变运行输入信封（由 run_id 定位，不依赖客户端请求）。

    返回：规范化输入字典。运行不存在时由 get_run 抛 RUN_STATE_CONFLICT；
    运行无持久化输入时抛 RUN_INPUT_UNAVAILABLE。
    """
    run = get_run(session, run_id)
    if run.normalized_input is None:
        raise AppError("RUN_INPUT_UNAVAILABLE", "run has no persisted normalized input")
    return run.normalized_input


def run_snapshot(session: Session, run_id: str) -> dict:
    """构造 RunSnapshot 字典（不把中间事件当作 accepted 版本）。

    返回真实的 `pending_node`/`pause_reason`/`clarification_questions`/
    `last_error_code`：这些字段由 Worker 在写入 checkpoint 或暂停时持久化到
    `GenerationRun` 行，绝不固定返回空值。
    """
    run = get_run(session, run_id)
    store = PostgresRunEventStore(session)
    run_scope = "scene" if run.scene_id else "chapter"
    return {
        "run_id": run.id,
        "thread_id": run.id,
        "project_id": run.project_id,
        "target_id": run.scene_id or run.chapter_id or "",
        "run_scope": run_scope,
        "request_type": run.request_type or "continue",
        "status": run.status,
        "run_version": run.run_version,
        "current_scene_id": run.scene_id,
        "current_node": run.last_durable_node,
        "pending_node": run.pending_node,
        "pause_reason": run.pause_reason,
        "clarification_questions": run.clarification_questions or [],
        "last_error_code": run.last_error_code,
        "last_event_sequence": store.max_sequence(run_id),
        "created_at": run.created_at.isoformat(),
        "updated_at": (run.updated_at or run.created_at).isoformat(),
    }


def _apply_accept_action(
    session: Session,
    run: GenerationRun,
    body: DecisionRequest,
    ctx: CommandContext,
) -> None:
    """按决策目标执行 accept 的领域物化动作。

    - target=plan：CAS 接受计划版本并物化场景映射。
    - target=scene：物化草稿（draft_artifact_id）或 ChangeSet（change_set_id）。
    - target=chapter：物化 staged 章节版本为 accepted。
    """
    if body.target == "plan":
        if not body.plan_revision_id:
            raise AppError("PLAN_NOT_ACCEPTED", "plan accept requires plan_revision_id")
        plan = accept_chapter_plan_revision(
            session,
            run.chapter_id or "",
            body.plan_revision_id,
            body.expected_current_plan_revision_id or "",
            body.expected_plan_version or 1,
            ctx,
        )
        scene_specs = (plan.chapter_contract or {}).get("scenes") or []
        materialize_chapter_plan(
            session, run.chapter_id or "", plan.id, scene_specs, ctx
        )
        return
    if body.target == "scene":
        scene_id = run.scene_id or ""
        if body.draft_artifact_id:
            commit_scene_draft(session, body.draft_artifact_id, ctx)
        elif body.change_set_id:
            commit_scene_change_set(session, scene_id, body.change_set_id, ctx)
        else:
            raise AppError("SCENE_NOT_ACCEPTED", "scene accept requires a draft or change set")
        return
    if body.target == "chapter":
        if not body.chapter_revision_id:
            raise AppError("CHAPTER_OUT_OF_SYNC", "chapter accept requires chapter_revision_id")
        # Task 5C：chapter_revision.accepted outbox 事件在权威的
        # commit_chapter_version 事务边界内入队（API/Worker/领域服务所有章节接受
        # 路径统一从该函数入队一次），绝不在此直接调用 CanonAgent。
        commit_chapter_version(session, body.chapter_revision_id, ctx)
        return


def submit_run_decision(
    session: Session,
    actor_id: str,
    run_id: str,
    body: DecisionRequest,
    manual_command_id: str,
    sink: ObservabilitySink | None = None,
) -> dict:
    """作者对运行提交决策：claim 后取 API command fence，CAS 版本并写入记录。

    参数：actor_id 为服务端解析身份；run_id 为目标运行；body 为决策请求；
    manual_command_id 为幂等 claim 生成的人工命令 id；sink 为观测 sink（缺省
    使用进程级生产 wiring 的 sink，无真实 LangSmith API Key 时自动降级本地）。
    副作用：decision=feedback 时调用 record_author_feedback 只存反馈哈希，
    正文不落库；sink 调用 fail-open，失败不影响决策事务，也不导致命令重复
    执行（命令幂等由 execute_command 保证）。
    返回：DecisionResponse 字典（run 快照、decision_id、command_id）。

    失败条件：target=canon 抛 CANON_NOT_ENABLED；运行不存在或状态不允许决策抛
    RUN_STATE_CONFLICT；expected_run_version 不匹配抛 RUN_STATE_CONFLICT（CAS）；
    旧 fence 抛 RUN_LEASE_LOST。
    """
    if body.target == "canon":
        raise AppError(
            "CANON_NOT_ENABLED",
            "canon decisions use a dedicated route; not enabled in this stage",
        )
    run = session.execute(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
    if run.run_version != body.expected_run_version:
        raise AppError("RUN_STATE_CONFLICT", "run version CAS mismatch")
    # 既有状态契约：paused 只能走 resume；终态不再接受决策。
    if run.status == "paused":
        raise AppError("RUN_STATE_CONFLICT", "paused runs can only be resumed")
    if run.status in ("accepted", "cancelled", "failed", "superseded"):
        raise AppError("RUN_STATE_CONFLICT", "run is in a terminal state")
    # 决策类型各自定义允许状态：
    # - accept/feedback 只能在匹配的等待状态执行（waiting_feedback 二者均可；
    #   pending_clarification 仅 feedback）；queued/running 不得直接 accept。
    # - cancel 单独定义允许状态（queued/running/waiting_feedback/pending_clarification）。
    if body.decision == "accept":
        if run.status != "waiting_feedback":
            raise AppError(
                "RUN_STATE_CONFLICT",
                "accept requires a run waiting for feedback",
            )
    elif body.decision == "feedback":
        if run.status not in ("waiting_feedback", "pending_clarification"):
            raise AppError(
                "RUN_STATE_CONFLICT",
                "feedback requires a waiting_feedback or pending_clarification run",
            )
    else:  # cancel
        if run.status not in (
            "queued",
            "running",
            "waiting_feedback",
            "pending_clarification",
        ):
            raise AppError(
                "RUN_STATE_CONFLICT",
                "cancel is not allowed in this run state",
            )
    # 作者决策：先取得 API command fence（source=author，不使用 Worker 租约）。
    fence = claim_api_command_fence(
        session, run_id, manual_command_id, body.expected_run_version
    )
    CommitGuard(session).validate(
        "apply_run_decision",
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
        author_decision=body.decision,
    )
    if body.decision == "accept":
        _apply_accept_action(session, run, body, ctx)
        run.status = "accepted"
        event_type = _EVENT_ACCEPTED
    elif body.decision == "cancel":
        run.status = "cancelled"
        event_type = _EVENT_CANCELLED
    else:  # feedback
        run.status = "waiting_feedback"
        run.decision_target = body.target
        event_type = _EVENT_WAITING_FEEDBACK
        # 作者反馈只存哈希（fail-open：sink 失败不影响决策事务与命令幂等）。
        record_author_feedback(
            sink if sink is not None else get_default_wiring().sink,
            generation_run_id=run_id,
            target=body.target,
            decision="feedback",
            content=body.text or "",
        )
    run.run_version += 1
    run.decision_target = body.target
    session.flush()
    decision = append_run_decision(
        session,
        run_id,
        body.target,
        {
            "decision": body.decision,
            "target": body.target,
            "text": body.text,
        },
        ctx,
    )
    store = PostgresRunEventStore(session)
    store.emit(
        run_id,
        event_type,
        {"target": body.target, "decision": body.decision, "run_version": run.run_version},
        fencing_token=fence["fencing_token"],
        producer_command_id=manual_command_id,
    )
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "run_decision",
            "resource_id": run_id,
            "payload_schema": "run-event.v1",
            "payload": {"event_type": event_type, "run_id": run_id, "target": body.target},
            "producer_command_id": manual_command_id,
            "generation_run_id": run_id,
        },
        fencing_token=fence["fencing_token"],
    )
    return {
        "run": run_snapshot(session, run_id),
        "decision_id": decision.id,
        "command_id": manual_command_id,
    }


def resume_paused_run(
    session: Session,
    actor_id: str,
    run_id: str,
    body: ResumeRequest,
    manual_command_id: str,
) -> dict:
    """恢复 paused 运行：校验暂停原因与运行版本后从原 checkpoint 继续。

    参数：actor_id 为服务端解析身份；run_id 为目标运行；body 为恢复请求；
    manual_command_id 为幂等 claim 生成的人工命令 id。
    返回：DecisionResponse 字典。

    失败条件：运行非 paused 抛 RUN_STATE_CONFLICT；expected_run_version 不匹配
    抛 RUN_STATE_CONFLICT（CAS）；expected_pause_reason 不匹配抛
    RUN_STATE_CONFLICT；旧 fence 抛 RUN_LEASE_LOST。
    """
    run = session.execute(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
    if run.status != "paused":
        raise AppError("RUN_STATE_CONFLICT", "only paused runs can be resumed")
    if run.run_version != body.expected_run_version:
        raise AppError("RUN_STATE_CONFLICT", "run version CAS mismatch")
    fence = claim_api_command_fence(
        session, run_id, manual_command_id, body.expected_run_version
    )
    CommitGuard(session).validate(
        "resume_paused_run",
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
        author_decision=None,
    )
    run.run_version += 1
    run.status = "running"
    session.flush()
    decision = append_run_decision(
        session,
        run_id,
        "run",
        {"decision": "resume", "pause_reason": body.expected_pause_reason},
        ctx,
    )
    store = PostgresRunEventStore(session)
    store.emit(
        run_id,
        _EVENT_RESUMED,
        {"run_version": run.run_version, "pause_reason": body.expected_pause_reason},
        fencing_token=fence["fencing_token"],
        producer_command_id=manual_command_id,
    )
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "run_resume",
            "resource_id": run_id,
            "payload_schema": "run-event.v1",
            "payload": {"event_type": _EVENT_RESUMED, "run_id": run_id},
            "producer_command_id": manual_command_id,
            "generation_run_id": run_id,
        },
        fencing_token=fence["fencing_token"],
    )
    return {
        "run": run_snapshot(session, run_id),
        "decision_id": decision.id,
        "command_id": manual_command_id,
    }


def replay_run_events(
    session: Session, run_id: str, after_sequence: int = 0, limit: int | None = None
) -> list[dict]:
    """按 `Last-Event-ID` 重放运行事件（升序，从下一序号开始）。

    参数：run_id 为目标运行；after_sequence 为客户端最后确认序号；
    limit 为可选上限。
    返回：RunEventEnvelope 字典列表（payload 已脱敏）。
    """
    get_run(session, run_id)
    store = PostgresRunEventStore(session)
    events = store.list_events(run_id, after_sequence=after_sequence, limit=limit)
    return [_event_envelope(e) for e in events]


def _event_envelope(event: Any) -> dict:
    """把 RunEvent 行投影为 RunEventEnvelope 字典。"""
    return RunEventEnvelope(
        id=f"{event.generation_run_id}:{event.sequence}",
        sequence=event.sequence,
        type=event.event_type,
        run_id=event.generation_run_id,
        created_at=event.created_at.isoformat(),
        payload=event.payload or {},
    ).model_dump()


# 供运行 API / outbox 消费者复用的导出（Task 5B 入口）。
__all__ = [
    "start_generation_run",
    "get_run",
    "run_snapshot",
    "submit_run_decision",
    "resume_paused_run",
    "replay_run_events",
]
