"""故事圣经（Canon）领域：候选事实/时间线/情节线的落库与 Canon 决策。

候选记录按 (来源, 类型, 指纹) 幂等去重；Canon 决策仅能作用于未丢弃的
候选；运行决策按 (run, target, idempotency_key) 幂等。所有写操作必须
在已通过 CommitGuard 校验的上下文内进行。

Task 4C 正式更新边界：本模块提供 `confirm_canon_decisions` 作为章节/场景
Canon 确认的正式更新路由（只能由 Canon 分支提交节点调用），负责来源版本
校验、作用域校验、幂等与正式 `CanonFact` 生成；它绝不作为 Agent 或普通
正文节点的公共写入口。
"""

from __future__ import annotations

import hashlib
import json
from typing import TypeAlias, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    CanonDecisionRecord,
    CanonFact,
    Chapter,
    FactCandidate,
    PlotThread,
    PlotThreadUpdate,
    RunDecision,
    Scene,
    TimelineEvent,
    TimelineEventCandidate,
)
from ..errors import AppError
from .interfaces import CommandContext

_CandidateModel: TypeAlias = type[FactCandidate] | type[TimelineEventCandidate] | type[PlotThreadUpdate]

_CANDIDATE_MODELS: dict[str, _CandidateModel] = {
    "fact": FactCandidate,
    "timeline_event": TimelineEventCandidate,
    "plot_thread": PlotThreadUpdate,
}


def _candidate_fingerprint(content: dict) -> str:
    """对候选内容生成规范化指纹。

    将 dict 按键排序、去除多余空白后计算 SHA-256，保证同一内容的指纹稳定，
    用于幂等去重。
    """
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_identity(candidate: dict) -> str:
    """从候选的多个来源字段中提取唯一来源标识。

    候选必须恰有一个来源（source_revision_id / source_draft_artifact_id /
    source_change_set_id 之一），否则抛 COMMAND_CONTEXT_MISMATCH。
    """
    sources = [
        candidate.get("source_revision_id"),
        candidate.get("source_draft_artifact_id"),
        candidate.get("source_change_set_id"),
    ]
    non_null = [s for s in sources if s is not None]
    if len(non_null) != 1:
        raise AppError("COMMAND_CONTEXT_MISMATCH", "candidate must have exactly one source")
    return non_null[0]


def _scope_identity(candidate: dict) -> str:
    """根据候选的作用域生成作用域标识。

    优先级：scene_id > chapter_id > project_id。
    """
    scene_id = candidate.get("scene_id")
    if scene_id:
        return f"scene:{scene_id}"
    chapter_id = candidate.get("chapter_id")
    if chapter_id:
        return f"chapter:{chapter_id}"
    return f"project:{candidate.get('project_id')}"


def upsert_canon_candidates(
    session: Session,
    generation_run_id: str,
    candidates: list[dict],
    ctx: CommandContext,
) -> list[dict]:
    """按 (来源, 类型, 指纹) 幂等持久化候选记录。

    参数：generation_run_id 为生成运行 id；candidates 为候选列表；ctx 为
    命令上下文。
    返回：已持久化候选的投影字典列表（可能包含本次新创建或已存在的记录）。

    失败条件：ctx 中的 generation_run_id 与参数不一致时抛
    COMMAND_CONTEXT_MISMATCH；候选来源非恰好一个时抛
    COMMAND_CONTEXT_MISMATCH。

    幂等约束：已存在相同 (project, scope, source_identity, candidate_type,
    candidate_fingerprint) 的记录时直接返回既有记录，不重复插入。

    副作用：新增候选并 flush；须在已通过 CommitGuard 的事务内调用。
    """
    if ctx.get("generation_run_id") != generation_run_id:
        raise AppError("COMMAND_CONTEXT_MISMATCH", "candidate run id must match context")
    result: list[dict] = []
    for cand in candidates:
        cand_type = cand.get("candidate_type", "fact")
        fingerprint = cand.get("fingerprint") or _candidate_fingerprint(cand.get("content", {}))
        source_id = _source_identity(cand)
        scope = cand.get("scope", "scene")
        scope_identity = _scope_identity(cand)
        model_cls = _CANDIDATE_MODELS[cand_type]
        filters = [
            model_cls.project_id == cand["project_id"],
            model_cls.scope == scope,
            model_cls.source_identity == source_id,
            model_cls.candidate_type == cand_type,
            model_cls.candidate_fingerprint == fingerprint,
        ]
        existing = session.execute(select(model_cls).where(*filters)).scalars().first()
        if existing is not None:
            result.append(_to_dict(existing))
            continue
        row = model_cls(
            project_id=cand["project_id"],
            chapter_id=cand.get("chapter_id"),
            scene_id=cand.get("scene_id"),
            scope=scope,
            scope_identity=scope_identity,
            candidate_type=cand_type,
            candidate_fingerprint=fingerprint,
            status="pending",
            source_revision_id=cand.get("source_revision_id"),
            source_draft_artifact_id=cand.get("source_draft_artifact_id"),
            source_change_set_id=cand.get("source_change_set_id"),
            source_identity=source_id,
            content=cand.get("content", {}),
            local_key=cand.get("local_key"),
            generation_run_id=generation_run_id,
        )
        session.add(row)
        session.flush()
        result.append(_to_dict(row))
    return result


def _to_dict(row) -> dict:
    """将候选模型行投影为返回字典。"""
    return {
        "id": row.id,
        "project_id": row.project_id,
        "chapter_id": row.chapter_id,
        "scene_id": row.scene_id,
        "scope": row.scope,
        "scope_identity": row.scope_identity,
        "candidate_type": row.candidate_type,
        "candidate_fingerprint": row.candidate_fingerprint,
        "status": row.status,
        "source_identity": row.source_identity,
        "content": row.content,
        "local_key": row.local_key,
        "generation_run_id": row.generation_run_id,
    }


def apply_canon_decisions(
    session: Session,
    candidate_decisions: list[dict],
    ctx: CommandContext,
) -> list[CanonDecisionRecord]:
    """针对候选记录应用 Canon 决策，锁定候选并拒绝非 pending 的重复决策。

    参数：candidate_decisions 为决策列表；ctx 为命令上下文（actor_id 用于
    记录操作者）。
    返回：新建的 CanonDecisionRecord 列表。

    失败条件：
        - 候选不存在或状态为 discarded：SCENE_STATE_INCOMPATIBLE。
        - 候选已决策（accepted/rejected/deferred，非 pending）：同样抛
          SCENE_STATE_INCOMPATIBLE，防止并发决策互相覆盖（行锁 + 状态机）。

    状态机：候选只能从 pending 一次性迁移到 accepted|rejected|deferred|
    discarded；discarded 是终态，任何后续决策都被拒绝。

    副作用：按 `candidate_type` 在对应候选表上用 `FOR UPDATE` 锁定行，更新
    候选状态并新增、flush 决策记录；须在已通过 CommitGuard 的事务内调用。
    """
    records: list[CanonDecisionRecord] = []
    for decision in candidate_decisions:
        candidate_id = decision["candidate_id"]
        cand_type = decision.get("candidate_type", "fact")
        model_cls = _CANDIDATE_MODELS[cand_type]
        # 行锁：并发决策在同一候选上串行化，后到者在锁释放后读取新状态被拒。
        cand = session.execute(
            select(model_cls).where(model_cls.id == candidate_id).with_for_update()
        ).scalar_one_or_none()
        # 三个候选模型同构，统一按 FactCandidate 的形状访问公共字段。
        cand_row = cast(FactCandidate, cand) if cand is not None else None
        if cand_row is None or cand_row.status == "discarded":
            raise AppError("SCENE_STATE_INCOMPATIBLE", "candidate is discarded or missing")
        if cand_row.status != "pending":
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                f"candidate already decided as {cand_row.status}; concurrent or duplicate decision rejected",
            )
        cand_row.status = decision["decision"]
        record = CanonDecisionRecord(
            candidate_id=candidate_id,
            candidate_type=cand_type,
            local_key=decision.get("local_key"),
            candidate_snapshot=decision.get("candidate_snapshot", {}),
            source_ref=decision.get("source_ref", {}),
            decision=decision["decision"],
            actor_id=ctx["actor_id"],
        )
        session.add(record)
        session.flush()
        records.append(record)
    return records


def append_run_decision(
    session: Session,
    run_id: str,
    target: str,
    request_snapshot: dict,
    ctx: CommandContext,
) -> RunDecision:
    """追加一条不可变的运行决策；同一 (run, target, idempotency_key) 只产生一条。

    参数：run_id 为生成运行 id；target 为决策目标；request_snapshot 为请求
    快照；ctx 为命令上下文。

    失败条件：
        - agent/review 来源：ctx 的 generation_run_id 必须等于 run_id，否则
          COMMAND_CONTEXT_MISMATCH。
        - author 来源：必须携带指向该 run 的 api_command write_fence，且不得
          携带 generation_run_id，否则 COMMAND_CONTEXT_MISMATCH。

    幂等约束：已存在相同 (generation_run_id, target, idempotency_key) 的
    决策时直接返回既有记录，不重复追加。

    副作用：新增决策并 flush；须在已通过 CommitGuard 的事务内调用。
    """
    if ctx.get("source") in ("agent", "review"):
        if ctx.get("generation_run_id") != run_id:
            raise AppError("COMMAND_CONTEXT_MISMATCH", "run decision run id must match context")
    elif ctx.get("source") == "author":
        fence = ctx.get("write_fence")
        if ctx.get("generation_run_id") is not None or fence is None:
            raise AppError("COMMAND_CONTEXT_MISMATCH", "author run decision requires an api_command fence")
        if fence["generation_run_id"] != run_id:
            raise AppError("COMMAND_CONTEXT_MISMATCH", "run decision fence targets a different run")
    existing = session.execute(
        select(RunDecision).where(
            RunDecision.generation_run_id == run_id,
            RunDecision.target == target,
            RunDecision.idempotency_key == ctx["idempotency_key"],
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    decision = RunDecision(
        generation_run_id=run_id,
        target=target,
        request_snapshot=request_snapshot,
        idempotency_key=ctx["idempotency_key"],
        decision=ctx.get("author_decision") or "running",
    )
    session.add(decision)
    session.flush()
    return decision


def _validate_chapter_source(
    session: Session, candidate, chapter_id: str
) -> None:
    """校验章节级候选来源：章节必须同步、入口链有效且来源等于当前 accepted 指针。

    失败条件：章节不存在或 `chapter_sync_status != in_sync` 抛
    CHAPTER_OUT_OF_SYNC；`entry_handoff_status != in_sync` 抛
    CHAPTER_HANDOFF_CONFLICT；候选来源不等于当前 accepted 章节修订抛
    SCENE_STATE_INCOMPATIBLE。绝不读取“最新行”推断 accepted。
    """
    chapter = session.get(Chapter, chapter_id)
    if chapter is None or chapter.chapter_sync_status != "in_sync":
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter is not in sync; cannot confirm chapter canon")
    if chapter.entry_handoff_status != "in_sync":
        raise AppError("CHAPTER_HANDOFF_CONFLICT", "entry handoff is stale; cannot confirm chapter canon")
    if candidate.source_revision_id != chapter.accepted_chapter_revision_id:
        raise AppError(
            "SCENE_STATE_INCOMPATIBLE",
            "candidate source revision is not the current accepted chapter revision",
        )


def _validate_scene_source(session: Session, candidate, scene_id: str) -> None:
    """校验场景级候选来源：候选来源必须等于场景当前 accepted 指针。

    失败条件：候选来源不等于当前 accepted 场景修订抛 SCENE_STATE_INCOMPATIBLE。
    场景产生新接受版本后旧来源候选被拒绝（不自动迁移到新版本）。
    """
    scene = session.get(Scene, scene_id)
    if scene is None or candidate.source_revision_id != scene.accepted_scene_revision_id:
        raise AppError(
            "SCENE_STATE_INCOMPATIBLE",
            "candidate source revision is not the current accepted scene revision",
        )


def _materialize_official_canon(session: Session, candidate, cand_type: str):
    """把章节级已确认候选物化为对应的正式结构（事务内部私有步骤）。

    三类候选按 `candidate_type` 写入正确的正式结构：
        - fact           -> `CanonFact`（已确认属性/关系/事件/规则）
        - timeline_event -> `TimelineEvent`（故事内时间、参与实体）
        - plot_thread    -> `PlotThread`（伏笔/冲突线状态与计划回收位置）
    来源版本由候选与决策记录追溯；正式内容字段取自候选 `content`。
    该函数只允许在 `confirm_canon_decisions` 的同一事务内调用，绝不作为
    公共写入口。
    """
    content = candidate.content or {}
    claim = content.get("claim") or candidate.local_key or ""
    if cand_type == "timeline_event":
        event = TimelineEvent(
            project_id=candidate.project_id,
            chapter_id=candidate.chapter_id,
            event_text=claim,
            story_time=content.get("effective_story_time") or {"value": "", "precision": "unknown"},
            entities=content.get("entities") or [],
            status="active",
        )
        session.add(event)
        session.flush()
        return event
    if cand_type == "plot_thread":
        thread = PlotThread(
            project_id=candidate.project_id,
            chapter_id=candidate.chapter_id,
            thread_text=claim,
            state=content.get("state") or "open",
            planned_resolution=content.get("planned_resolution"),
            status="active",
        )
        session.add(thread)
        session.flush()
        return thread
    fact = CanonFact(
        project_id=candidate.project_id,
        entity_id=content.get("entity_id"),
        fact_text=claim,
        status="active",
    )
    session.add(fact)
    session.flush()
    return fact


def _decision_fingerprint(canon_scope: str, candidate_decisions: list[dict]) -> str:
    """生成 Canon 决策请求的规范化指纹（幂等重放判定依据）。

    指纹包含作用域与全部决策内容（候选 id、类型、决策、兼容别名），按键排序
    的稳定 JSON 序列化后计算 SHA-256。同一键同指纹才允许幂等重放；同键不同
    候选/类型/作用域/决策内容会产生不同指纹，返回 IDEMPOTENCY_KEY_REUSE。
    """
    normalized = [
        {
            "candidate_id": d.get("candidate_id"),
            "candidate_type": d.get("candidate_type"),
            "decision": d.get("decision"),
            "local_key": d.get("local_key"),
        }
        for d in sorted(candidate_decisions, key=lambda x: (x.get("candidate_id") or "", x.get("candidate_type") or ""))
    ]
    canonical = json.dumps(
        {"canon_scope": canon_scope, "candidate_decisions": normalized},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def confirm_canon_decisions(
    session: Session,
    generation_run_id: str,
    candidate_decisions: list[dict],
    ctx: CommandContext,
    *,
    canon_scope: str,
    chapter_id: str | None = None,
    scene_id: str | None = None,
) -> list[CanonDecisionRecord]:
    """Task 4C 正式 Canon 更新路由：作者逐条确认/拒绝/暂缓候选。

    参数：generation_run_id 为 Canon 运行 id；candidate_decisions 为决策列表
    （含持久 `candidate_id`、`candidate_type`、`decision=accepted|rejected|
    deferred` 与兼容别名 `local_key`）；ctx 为命令上下文；canon_scope 为
    chapter|scene；chapter_id / scene_id 分别对应章节级/场景级作用域目标。

    返回：本次新写入的 CanonDecisionRecord 列表；幂等命中时返回空列表。

    规则：
        - 幂等：先按 (run, target=canon, idempotency_key) 调用 append_run_decision；
          重复键直接返回，不重复决策或生成正式 Canon。
        - 作用域：canon_scope=scene 必须提供 scene_id，只接受 scope=scene 的
          候选，且绝不生成全局 CanonFact；canon_scope=chapter 必须提供
          chapter_id，只接受 scope=chapter 的候选，确认后生成 CanonFact。
        - 来源版本：章节级要求章节 in_sync + 入口链 in_sync 且候选来源等于
          当前 accepted 章节指针；场景级要求候选来源等于当前 accepted 场景指针。
        - 候选锁定与状态机由 apply_canon_decisions 保证（FOR UPDATE + 仅
          pending 可决策）。
        - 本函数只能由 Canon 分支提交节点调用，Agent/普通正文节点不得直接
          调用（正式写入必须经过 CommitGuard 校验后进入领域服务）。

    失败条件：作用域/来源/候选状态不满足时抛对应稳定错误码
    （CHAPTER_OUT_OF_SYNC / CHAPTER_HANDOFF_CONFLICT / SCENE_STATE_INCOMPATIBLE
    / COMMAND_CONTEXT_MISMATCH）。
    """
    if canon_scope == "scene" and (scene_id is None or chapter_id is not None):
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "scene canon requires scene_id and must not carry chapter_id",
        )
    if canon_scope == "chapter" and (chapter_id is None or scene_id is not None):
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "chapter canon requires chapter_id and must not carry scene_id",
        )
    # 幂等：同 (run, target=canon, idempotency_key) 且请求指纹相同才允许重放；
    # 同键不同候选/类型/作用域/决策内容返回 IDEMPOTENCY_KEY_REUSE，且在任何
    # 写入发生之前拒绝，不改变候选状态或正式 Canon。
    fingerprint = _decision_fingerprint(canon_scope, candidate_decisions)
    prior = session.execute(
        select(RunDecision).where(
            RunDecision.generation_run_id == generation_run_id,
            RunDecision.target == "canon",
            RunDecision.idempotency_key == ctx["idempotency_key"],
        )
    ).scalar_one_or_none()
    if prior is not None:
        prior_snapshot = prior.request_snapshot or {}
        if prior_snapshot.get("request_fingerprint") != fingerprint:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSE",
                "idempotency key reused with a different canon decision request",
            )
        return []
    # 先写统一不可变运行决策（审计 + 幂等键登记 + 请求指纹），随后逐条应用。
    append_run_decision(
        session,
        generation_run_id,
        "canon",
        {
            "canon_scope": canon_scope,
            "candidate_decisions": candidate_decisions,
            "request_fingerprint": fingerprint,
        },
        ctx,
    )
    new_records: list[CanonDecisionRecord] = []
    for decision in candidate_decisions:
        cand_type = decision.get("candidate_type", "fact")
        model_cls = _CANDIDATE_MODELS[cand_type]
        cand = session.execute(
            select(model_cls).where(model_cls.id == decision["candidate_id"]).with_for_update()
        ).scalar_one_or_none()
        # 三个候选模型同构，统一按 FactCandidate 的形状访问公共字段。
        cand_row = cast(FactCandidate, cand) if cand is not None else None
        if cand_row is None:
            raise AppError("SCENE_STATE_INCOMPATIBLE", "candidate is missing")
        # 作用域校验：候选 scope 必须与当前 Canon 运行作用域一致。
        if cand_row.scope != canon_scope:
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE",
                f"candidate scope {cand_row.scope} does not match canon_scope {canon_scope}",
            )
        if canon_scope == "chapter":
            if cand_row.scene_id is not None:
                raise AppError(
                    "SCENE_STATE_INCOMPATIBLE",
                    "chapter canon must not confirm a scene-scoped candidate",
                )
            _validate_chapter_source(session, cand_row, chapter_id or "")
        else:
            _validate_scene_source(session, cand_row, scene_id or "")
        records = apply_canon_decisions(
            session,
            [decision],
            ctx,
        )
        new_records.extend(records)
        if decision["decision"] == "accepted" and canon_scope == "chapter":
            # 章节级 confirm：同一事务内按候选类型物化为正式结构。
            _materialize_official_canon(session, cand_row, cand_type)
    return new_records


def validate_canon_candidate_sources(
    session: Session,
    candidates: list[dict],
    *,
    canon_scope: str,
    chapter_id: str | None = None,
    scene_id: str | None = None,
) -> None:
    """校验候选创建时的来源：只允许以当前已接受版本为候选来源。

    参数：candidates 为待持久化候选列表；canon_scope 为 chapter|scene；
    chapter_id / scene_id 为对应作用域目标。

    失败条件（均抛 AppError）：
        - 候选缺少 `source_revision_id`：COMMAND_CONTEXT_MISMATCH。
        - 章节级：章节不存在或候选来源不等于当前 accepted 章节指针：
          SCENE_STATE_INCOMPATIBLE（绝不按最新行推断 accepted）。
        - 场景级：场景不存在或候选来源不等于当前 accepted 场景指针：
          SCENE_STATE_INCOMPATIBLE。

    该函数在 Canon 分支候选持久化前调用，保证“只能使用 accepted 版本作为
    候选来源”；正式更新时 `confirm_canon_decisions` 还会再做一次来源校验。
    """
    for cand in candidates:
        source = cand.get("source_revision_id")
        if source is None:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "canon candidates must be sourced from an accepted revision",
            )
        if canon_scope == "chapter":
            if chapter_id is None:
                raise AppError("COMMAND_CONTEXT_MISMATCH", "chapter canon requires chapter_id")
            chapter = session.get(Chapter, chapter_id)
            if chapter is None or chapter.accepted_chapter_revision_id != source:
                raise AppError(
                    "SCENE_STATE_INCOMPATIBLE",
                    "candidate source is not the current accepted chapter revision",
                )
        else:
            if scene_id is None:
                raise AppError("COMMAND_CONTEXT_MISMATCH", "scene canon requires scene_id")
            scene = session.get(Scene, scene_id)
            if scene is None or scene.accepted_scene_revision_id != source:
                raise AppError(
                    "SCENE_STATE_INCOMPATIBLE",
                    "candidate source is not the current accepted scene revision",
                )
