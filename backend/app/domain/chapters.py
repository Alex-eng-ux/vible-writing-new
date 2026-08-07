from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Chapter,
    ChapterPlanDiscussionMessage,
    ChapterPlanProposal,
    ChapterPlanQuestion,
    ChapterPlanRevision,
    ChapterPlanRevisionLink,
    ChapterPlanSceneLink,
    ChapterRevision,
    ChapterRevisionScene,
    FactCandidate,
    GenerationRun,
    PlotThreadUpdate,
    Scene,
    SceneRevision,
    TimelineEventCandidate,
)
from ..errors import AppError
from .interfaces import CommandContext, ResourceCommandContext


def create_chapter(
    session: Session,
    volume_id: str,
    title: str,
    pov: str,
    chapter_intent: dict,
    ctx: ResourceCommandContext,
) -> Chapter:
    chapter = Chapter(
        volume_id=volume_id,
        title=title,
        pov=pov,
        chapter_intent=chapter_intent,
        chapter_sync_status=None,
    )
    session.add(chapter)
    session.flush()
    return chapter


def create_scene(
    session: Session,
    chapter_id: str,
    title: str,
    scene_brief: dict,
    ctx: ResourceCommandContext,
) -> Scene:
    scene = Scene(chapter_id=chapter_id, title=title, scene_brief=scene_brief)
    session.add(scene)
    session.flush()
    return scene


def create_chapter_plan_revision(
    session: Session,
    chapter_id: str,
    parent_plan_revision_id: str | None,
    chapter_contract: dict,
    reason: str,
    ctx: CommandContext,
) -> ChapterPlanRevision:
    plan = ChapterPlanRevision(
        chapter_id=chapter_id,
        parent_plan_revision_id=parent_plan_revision_id,
        chapter_contract=chapter_contract,
        reason=reason,
        status="pending",
        plan_version=1,
    )
    session.add(plan)
    session.flush()
    return plan


def persist_chapter_plan_candidate(
    session: Session,
    chapter_id: str,
    *,
    source_run_id: str,
    planning_lineage_id: str,
    chapter_contract: dict,
    scene_briefs: list[dict],
    reason: str,
    contract_field_provenance: dict | None = None,
    unresolved_assumptions: list[str] | None = None,
    ctx: CommandContext,
) -> ChapterPlanRevision:
    """幂等保存 Planner 候选；候选始终保持 pending，不能绕过作者接受。"""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    existing = session.execute(
        select(ChapterPlanRevision)
        .where(ChapterPlanRevision.source_run_id == source_run_id)
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        if existing.chapter_id != chapter_id:
            raise AppError("PLAN_REVISION_CONFLICT", "source run belongs to another chapter")
        return existing
    previous = session.execute(
        select(ChapterPlanRevision)
        .where(ChapterPlanRevision.planning_lineage_id == planning_lineage_id)
        .order_by(ChapterPlanRevision.candidate_version.desc())
        .limit(1)
    ).scalar_one_or_none()
    candidate_version = (previous.candidate_version + 1) if previous else 1
    normalized_briefs = list(scene_briefs)
    contract = dict(chapter_contract or {})
    contract["scenes"] = normalized_briefs
    plan = ChapterPlanRevision(
        chapter_id=chapter_id,
        parent_plan_revision_id=previous.id if previous else None,
        chapter_contract=contract,
        reason=reason,
        status="pending",
        plan_version=1,
        candidate_version=candidate_version,
        planning_lineage_id=planning_lineage_id,
        source_run_id=source_run_id,
        contract_field_provenance=contract_field_provenance or {},
        scene_briefs=normalized_briefs,
        unresolved_assumptions=unresolved_assumptions or [],
        idempotency_key=ctx.get("idempotency_key"),
    )
    session.add(plan)
    session.flush()
    return plan


def append_plan_discussion_message(
    session: Session,
    chapter_id: str,
    planning_lineage_id: str,
    *,
    role: str,
    kind: str,
    text: str,
    agent: str | None = None,
    source_run_id: str | None = None,
    parent_run_id: str | None = None,
    supersedes_run_id: str | None = None,
    checkpoint_id: str | None = None,
) -> ChapterPlanDiscussionMessage:
    """按规划血缘分配单调消息序号并持久化正文。"""
    last = session.execute(
        select(ChapterPlanDiscussionMessage)
        .where(ChapterPlanDiscussionMessage.planning_lineage_id == planning_lineage_id)
        .order_by(ChapterPlanDiscussionMessage.message_sequence.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    message = ChapterPlanDiscussionMessage(
        chapter_id=chapter_id,
        planning_lineage_id=planning_lineage_id,
        message_sequence=(last.message_sequence + 1) if last else 1,
        role=role,
        agent=agent,
        kind=kind,
        text=text,
        source_run_id=source_run_id,
        parent_run_id=parent_run_id,
        supersedes_run_id=supersedes_run_id,
        checkpoint_id=checkpoint_id,
    )
    session.add(message)
    session.flush()
    return message


def upsert_plan_questions(
    session: Session,
    chapter_id: str,
    planning_lineage_id: str,
    questions: list[dict],
    source_run_id: str | None = None,
) -> list[ChapterPlanQuestion]:
    """保存 Planner 问题；有稳定 question_id 时反馈重放复用原记录。"""
    result: list[ChapterPlanQuestion] = []
    for item in questions:
        qid = item.get("question_id")
        question = session.get(ChapterPlanQuestion, qid) if qid else None
        if question is None and source_run_id:
            question = session.execute(
                select(ChapterPlanQuestion)
                .where(
                    ChapterPlanQuestion.planning_lineage_id == planning_lineage_id,
                    ChapterPlanQuestion.source_run_id == source_run_id,
                    ChapterPlanQuestion.text == item.get("text", item.get("question", "")),
                )
                .limit(1)
            ).scalar_one_or_none()
        if question is None:
            question = ChapterPlanQuestion(
                question_id=qid or None,
                chapter_id=chapter_id,
                planning_lineage_id=planning_lineage_id,
                text=item.get("text", item.get("question", "")),
                impact=item.get("impact", ""),
                status=item.get("status", "pending"),
                source_run_id=source_run_id,
            )
            session.add(question)
        else:
            if question.planning_lineage_id != planning_lineage_id:
                raise AppError("PLAN_REVISION_CONFLICT", "question belongs to another planning lineage")
        result.append(question)
    session.flush()
    return result


def upsert_plan_proposals(
    session: Session,
    chapter_id: str,
    planning_lineage_id: str,
    proposals: list[dict],
    source_run_id: str | None = None,
) -> list[ChapterPlanProposal]:
    """保存 Planner 建议及稳定 proposal_id。"""
    result: list[ChapterPlanProposal] = []
    for item in proposals:
        pid = item.get("proposal_id")
        proposal = session.get(ChapterPlanProposal, pid) if pid else None
        if proposal is None and source_run_id:
            proposal = session.execute(
                select(ChapterPlanProposal)
                .where(
                    ChapterPlanProposal.planning_lineage_id == planning_lineage_id,
                    ChapterPlanProposal.source_run_id == source_run_id,
                    ChapterPlanProposal.field_path == item.get("field_path", ""),
                )
                .limit(1)
            ).scalar_one_or_none()
        if proposal is None:
            proposal = ChapterPlanProposal(
                proposal_id=pid or None,
                chapter_id=chapter_id,
                planning_lineage_id=planning_lineage_id,
                field_path=item.get("field_path", ""),
                value=item.get("value", {}),
                source=item.get("source", "ai"),
                status=item.get("status", "pending"),
                rationale=item.get("rationale", ""),
                source_run_id=source_run_id,
            )
            session.add(proposal)
        elif proposal.planning_lineage_id != planning_lineage_id:
            raise AppError("PLAN_REVISION_CONFLICT", "proposal belongs to another planning lineage")
        result.append(proposal)
    session.flush()
    return result


def accept_chapter_plan_revision(
    session: Session,
    chapter_id: str,
    plan_revision_id: str,
    expected_current_plan_revision_id: str | None,
    expected_plan_version: int,
    ctx: CommandContext,
) -> ChapterPlanRevision:
    """CAS-accept a plan revision; the current pointer must match expectations."""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    plan = session.execute(
        select(ChapterPlanRevision)
        .where(ChapterPlanRevision.id == plan_revision_id)
        .with_for_update()
    ).scalar_one_or_none()
    if plan is None or plan.chapter_id != chapter_id:
        raise AppError("PLAN_REVISION_CONFLICT", "plan revision does not belong to the chapter")
    link = session.execute(
        select(ChapterPlanRevisionLink)
        .where(ChapterPlanRevisionLink.chapter_id == chapter_id)
        .with_for_update()
    ).scalar_one_or_none()
    if plan.status == "accepted":
        if link is None or link.plan_revision_id != plan.id:
            raise AppError("PLAN_REVISION_CONFLICT", "accepted plan pointer is inconsistent")
        if expected_current_plan_revision_id != link.plan_revision_id:
            raise AppError("PLAN_REVISION_CONFLICT", "current plan revision pointer mismatch")
        if expected_plan_version != link.plan_version:
            raise AppError("PLAN_REVISION_CONFLICT", "expected plan version mismatch")
        return plan
    if link is None:
        if expected_current_plan_revision_id is not None:
            raise AppError("PLAN_REVISION_CONFLICT", "no current plan revision exists")
        if expected_plan_version != 1:
            raise AppError("PLAN_REVISION_CONFLICT", "expected plan version mismatch")
    else:
        if link.plan_revision_id != expected_current_plan_revision_id:
            raise AppError("PLAN_REVISION_CONFLICT", "current plan revision pointer mismatch")
        if link.plan_version != expected_plan_version:
            raise AppError("PLAN_REVISION_CONFLICT", "expected plan version mismatch")
        plan.plan_version = link.plan_version + 1

    provenance = plan.contract_field_provenance or {}
    unresolved = [
        path for path, value in provenance.items()
        if isinstance(value, dict) and value.get("status") in {"ai_suggested", "unresolved"}
    ]
    for index, brief in enumerate(plan.scene_briefs or []):
        for path, value in (brief.get("field_provenance") or {}).items():
            if isinstance(value, dict) and value.get("status") in {"ai_suggested", "unresolved"}:
                unresolved.append(f"scene_briefs[{index}].{path}")
    assumptions = [
        str(item).strip()
        for item in (plan.unresolved_assumptions or [])
        if str(item).strip()
    ]
    if assumptions:
        # 未解决假设代表 Planner 明确声明的信息缺口；即使候选 JSON 看起来完整，
        # 也必须等作者确认或明确排除后才能推进 accepted pointer。
        raise AppError(
            "PLAN_NOT_ACCEPTED",
            "candidate contains unresolved assumptions",
            details={"assumptions": assumptions},
        )
    if unresolved:
        raise AppError(
            "PLAN_NOT_ACCEPTED",
            "candidate contains unconfirmed fields",
            details={"field_paths": unresolved},
        )
    plan.status = "accepted"
    if link is None:
        session.add(
            ChapterPlanRevisionLink(
                chapter_id=chapter_id,
                plan_revision_id=plan.id,
                plan_version=plan.plan_version,
            )
        )
    else:
        link.plan_revision_id = plan.id
        link.plan_version = plan.plan_version
    # 首次建立计划时，尚无上游 handoff 的章节可视为同步；已有 stale/out_of_sync
    # 状态必须保留，后续聚合/接受仍会拒绝未解决的上游冲突。
    if chapter.chapter_sync_status is None:
        chapter.chapter_sync_status = "in_sync"
    session.flush()
    # 接受命令在同一事务内固定场景映射并准备 outbox。
    # 兼容初始化计划可能只有 chapter_contract.scenes，因此这里保留该回退。
    scene_specs = plan.scene_briefs or (plan.chapter_contract or {}).get("scenes") or []
    if scene_specs:
        materialize_chapter_plan(session, chapter_id, plan.id, scene_specs, ctx)
    return plan


def materialize_chapter_plan(
    session: Session,
    chapter_id: str,
    plan_revision_id: str,
    scene_specs: list[dict],
    ctx: CommandContext,
) -> dict[str, str]:
    """Map stable client_key -> scene_id one-to-one and create scenes atomically."""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise AppError("PLAN_NOT_ACCEPTED", "chapter does not exist")
    plan = session.get(ChapterPlanRevision, plan_revision_id)
    if plan is None or plan.chapter_id != chapter_id or plan.status != "accepted":
        raise AppError("PLAN_NOT_ACCEPTED", "plan must be accepted before materialization")

    mapping: dict[str, str] = {}
    existing_links = {
        row.client_key: row
        for row in session.execute(
            select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan_revision_id)
        ).scalars()
    }
    for sort_order, spec in enumerate(scene_specs):
        client_key = spec.get("client_key")
        if not client_key:
            raise AppError("PLAN_REVISION_CONFLICT", "scene spec requires a client_key")
        if client_key in mapping:
            raise AppError("PLAN_REVISION_CONFLICT", "duplicate client_key in scene specs")
        if client_key in existing_links:
            mapping[client_key] = existing_links[client_key].scene_id
            continue
        scene_id = spec.get("scene_id")
        scene = session.get(Scene, scene_id) if scene_id else None
        if scene is None:
            scene = Scene(
                chapter_id=chapter_id,
                title=spec.get("title", client_key),
                scene_brief=spec.get("scene_brief", spec.get("brief", {})),
            )
            session.add(scene)
            session.flush()
        elif scene.chapter_id != chapter_id:
            raise AppError("PLAN_REVISION_CONFLICT", "scene does not belong to chapter")
        mapping[client_key] = scene.id
        session.add(
            ChapterPlanSceneLink(
                chapter_id=chapter_id,
                plan_revision_id=plan_revision_id,
                client_key=client_key,
                scene_id=scene.id,
                sort_order=sort_order,
            )
        )
    # accepted pointer 与场景映射在同一事务中发出，消费者只按固定映射入队。
    from ..runtime.outbox import PostgresRunOutbox

    producer = ctx.get("manual_command_id") or ctx.get("idempotency_key") or "chapter-plan-accept"
    run_id = ctx.get("generation_run_id")
    if run_id is not None and session.get(GenerationRun, run_id) is None:
        run_id = None
    write_fence = ctx.get("write_fence")
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "chapter_plan",
            "resource_id": plan_revision_id,
            "payload_schema": "chapter-plan.v1",
            "payload": {
                "event_type": "chapter_plan.accepted",
                "chapter_id": chapter_id,
                "plan_revision_id": plan_revision_id,
                "scene_mapping": mapping,
            },
            "producer_command_id": producer,
            "generation_run_id": run_id,
        },
        fencing_token=write_fence["fencing_token"] if write_fence else 0,
    )
    return mapping


def chapter_workflow_read(session: Session, chapter_id: str) -> dict:
    """组合读取章节规划、讨论、固定场景映射和活动运行状态。"""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    accepted_link = session.execute(
        select(ChapterPlanRevisionLink).where(ChapterPlanRevisionLink.chapter_id == chapter_id)
    ).scalar_one_or_none()
    accepted = session.get(ChapterPlanRevision, accepted_link.plan_revision_id) if accepted_link else None
    blocking: list[str] = []
    if accepted_link is not None:
        if accepted is None:
            blocking.append(f"accepted_plan_missing:{accepted_link.plan_revision_id}")
        elif accepted.status != "accepted":
            blocking.append(f"accepted_plan_not_accepted:{accepted.id}")
    candidate = session.execute(
        select(ChapterPlanRevision)
        .where(ChapterPlanRevision.chapter_id == chapter_id, ChapterPlanRevision.status == "pending")
        .order_by(ChapterPlanRevision.candidate_version.desc(), ChapterPlanRevision.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    lineage = (candidate or accepted)
    lineage_id = lineage.planning_lineage_id if lineage and lineage.planning_lineage_id else chapter_id

    messages = session.execute(
        select(ChapterPlanDiscussionMessage)
        .where(ChapterPlanDiscussionMessage.planning_lineage_id == lineage_id)
        .order_by(ChapterPlanDiscussionMessage.message_sequence)
    ).scalars().all()
    questions = session.execute(
        select(ChapterPlanQuestion)
        .where(ChapterPlanQuestion.planning_lineage_id == lineage_id, ChapterPlanQuestion.status == "pending")
        .order_by(ChapterPlanQuestion.created_at)
    ).scalars().all()
    proposals = session.execute(
        select(ChapterPlanProposal)
        .where(ChapterPlanProposal.planning_lineage_id == lineage_id)
        .order_by(ChapterPlanProposal.created_at)
    ).scalars().all()
    all_runs = session.execute(
        select(GenerationRun)
        .where(GenerationRun.chapter_id == chapter_id)
        .where(GenerationRun.status.not_in(("accepted", "cancelled", "failed", "superseded")))
        .order_by(GenerationRun.created_at.desc())
    ).scalars().all()
    # 场景运行必须属于当前 accepted plan；旧计划运行仍可审计，但不能冒充当前活动运行。
    runs = [
        run
        for run in all_runs
        if run.scene_id is None
        or (accepted is not None and run.plan_revision_id == accepted.id)
    ]
    if len(runs) > 1:
        blocking.append("multiple_active_runs")
    active = runs[0] if len(runs) == 1 else None
    canon_run = None
    if chapter.accepted_chapter_revision_id:
        canon_run = session.execute(
            select(GenerationRun)
            .where(
                GenerationRun.chapter_id == chapter_id,
                GenerationRun.scene_id.is_(None),
                GenerationRun.decision_target == "canon",
                GenerationRun.canon_source_revision_id == chapter.accepted_chapter_revision_id,
            )
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    run_snapshot = None
    if active is not None:
        run_snapshot = {
            "run_id": active.id,
            "thread_id": active.id,
            "project_id": active.project_id,
            "target_id": active.scene_id or active.chapter_id or "",
            "run_scope": "scene" if active.scene_id else "chapter",
            "request_type": active.request_type or "continue",
            "plan_revision_id": active.plan_revision_id,
            "base_scene_revision_id": (active.normalized_input or {}).get("base_scene_revision_id"),
            "status": active.status,
            "run_version": active.run_version,
            "current_scene_id": active.scene_id,
            "current_node": active.last_durable_node,
            "pending_node": active.pending_node,
            "pause_reason": active.pause_reason,
            "clarification_questions": active.clarification_questions or [],
            "last_error_code": active.last_error_code,
            "decision_target": active.decision_target,
        }
    links: list[ChapterPlanSceneLink] = []
    if accepted is not None:
        links = list(session.execute(
            select(ChapterPlanSceneLink)
            .where(ChapterPlanSceneLink.plan_revision_id == accepted.id)
            .order_by(ChapterPlanSceneLink.sort_order)
        ).scalars().all())
        linked_keys = {link.client_key for link in links}
        for brief in accepted.scene_briefs or []:
            client_key = brief.get("client_key")
            if client_key and client_key not in linked_keys:
                blocking.append(f"scene_mapping_missing:{client_key}")
    scene_rows = {s.id: s for s in session.execute(select(Scene).where(Scene.chapter_id == chapter_id)).scalars().all()}
    scene_views = []
    for link_index, link in enumerate(links):
        scene = scene_rows.get(link.scene_id)
        if scene is None:
            blocking.append(f"scene_missing:{link.scene_id}")
            continue
        scene_run = session.execute(
            select(GenerationRun)
            .where(GenerationRun.scene_id == scene.id)
            .where(GenerationRun.plan_revision_id == (accepted.id if accepted else None))
            .where(GenerationRun.status.not_in(("accepted", "cancelled", "failed", "superseded")))
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        scene_blocking: list[str] = []
        if link_index > 0:
            previous_links = links[:link_index]
            if any(scene_rows.get(prev.scene_id) is None or scene_rows[prev.scene_id].accepted_scene_revision_id is None for prev in previous_links):
                scene_blocking.append("previous_scene_not_accepted")
        if scene_run is not None and accepted is not None and scene_run.plan_revision_id != accepted.id:
            scene_blocking.append("scene_plan_mismatch")
        scene_views.append(
            {
                "scene_id": scene.id,
                "order": link.sort_order,
                "title": scene.title,
                "status": (scene_run.status if scene_run else ("accepted" if scene.accepted_scene_revision_id else "planned")),
                "accepted_revision_id": scene.accepted_scene_revision_id,
                "current_run_id": scene_run.id if scene_run else None,
                "blocking_reasons": scene_blocking,
            }
        )
    if blocking:
        phase = "blocked"
    elif active is not None and active.decision_target == "plan":
        phase = "plan_feedback" if active.status in ("waiting_feedback", "pending_clarification") else "planning"
    elif active is not None and active.decision_target == "chapter":
        # 章节已有旧 accepted 版本时，新的审校运行仍优先决定工作台阶段，
        # 避免按旧版本把进行中的审校误报为 completed。
        phase = "chapter_feedback" if active.status in ("waiting_feedback", "pending_clarification") else "chapter_review"
    elif active is not None and active.decision_target == "canon":
        phase = "canon_feedback" if active.status in ("waiting_feedback", "pending_clarification") else "chapter_review"
    elif accepted is None:
        phase = "intent_required" if not (chapter.chapter_intent or {}).get("text", "").strip() else "planning"
    elif any(v["status"] in {"waiting_feedback", "pending_clarification"} for v in scene_views):
        phase = "scene_feedback"
    elif scene_views and any(v["status"] != "accepted" for v in scene_views):
        phase = "scene_generation"
    else:
        phase = "completed" if chapter.accepted_chapter_revision_id else "chapter_review"
    pending_decision: dict[str, Any] = {
        "target": None,
        "kind": None,
        "run_id": None,
        "expected_run_version": None,
    }
    if active is not None:
        target = active.decision_target
        pending_decision.update(
            {
                "target": target,
                "run_id": active.id,
                "expected_run_version": active.run_version,
                "kind": "accept_plan" if target == "plan" and active.status == "waiting_feedback" else (
                    "answer_planner"
                    if target == "plan"
                    else "answer_scene"
                    if target == "scene"
                    else "canon_feedback"
                    if target == "canon"
                    else "accept_chapter"
                    if target == "chapter" and active.status == "waiting_feedback"
                    else "chapter_feedback"
                ),
            }
        )
    elif candidate is not None:
        pending_decision["kind"] = "accept_plan"
        pending_decision["target"] = "plan"
    revision_rows = session.execute(
        select(ChapterRevision)
        .where(ChapterRevision.chapter_id == chapter_id)
        .order_by(ChapterRevision.created_at.desc())
    ).scalars().all()
    staged_revision = next((row for row in revision_rows if row.status == "staged"), None)
    revision_history: list[dict[str, Any]] = []
    for row in revision_rows:
        revision_history.append(
            {
                "id": row.id,
                "parent_revision_id": row.parent_revision_id,
                "status": row.status,
                "reason": row.reason,
                "created_at": row.created_at.isoformat(),
                "scene_versions": [
                    {
                        "scene_id": link.scene_id,
                        "scene_revision_id": link.scene_revision_id,
                        "sort_order": link.sort_order,
                    }
                    for link in session.execute(
                        select(ChapterRevisionScene)
                        .where(ChapterRevisionScene.chapter_revision_id == row.id)
                        .order_by(ChapterRevisionScene.sort_order)
                    ).scalars()
                ],
                "review_issues": row.review_issues or [],
                "review_summary": row.review_summary or {},
                "is_current_accepted": row.id == chapter.accepted_chapter_revision_id,
            }
        )
    canon_view = {
        "run_id": canon_run.id if canon_run else None,
        "status": canon_run.status if canon_run else None,
        "source_revision_id": canon_run.canon_source_revision_id if canon_run else None,
        "pending_candidate_count": 0,
    }
    if canon_run is not None:
        canon_view["pending_candidate_count"] = sum(
            session.query(model)
            .where(
                model.generation_run_id == canon_run.id,
                model.status == "pending",
            )
            .count()
            for model in (FactCandidate, TimelineEventCandidate, PlotThreadUpdate)
        )
    display_plan = candidate or accepted
    return {
        "chapter_id": chapter_id,
        "phase": phase,
        "chapter_status": chapter.chapter_sync_status or "draft",
        "pending_decision": pending_decision,
        "intent": {
            "text": (
                ((active.normalized_input or {}).get("chapter_intent") or {}).get("text", "")
                if active is not None and isinstance((active.normalized_input or {}).get("chapter_intent"), dict)
                else (chapter.chapter_intent or {}).get("text", "")
            ),
            "optional_fields": (
                (active.normalized_input or {}).get("chapter_intent")
                if active is not None and isinstance((active.normalized_input or {}).get("chapter_intent"), dict)
                else chapter.chapter_intent or {}
            ),
            "unresolved_questions": [q.text for q in questions],
        },
        "plan_discussion": {
            "messages": [
                {
                    "message_id": m.message_id,
                    "message_sequence": m.message_sequence,
                    "role": m.role,
                    "agent": m.agent,
                    "kind": m.kind,
                    "text": m.text,
                    "created_at": m.created_at.isoformat(),
                    "source_run_id": m.source_run_id,
                    "parent_run_id": m.parent_run_id,
                    "supersedes_run_id": m.supersedes_run_id,
                    "checkpoint_id": m.checkpoint_id,
                }
                for m in messages
            ],
            "pending_questions": [
                {"question_id": q.question_id, "text": q.text, "impact": q.impact} for q in questions
            ],
            "pending_proposals": [
                {
                    "proposal_id": p.proposal_id,
                    "field_path": p.field_path,
                    "value": p.value,
                    "source": p.source,
                    "status": p.status,
                    "rationale": p.rationale,
                }
                for p in proposals
                if p.status == "pending"
            ],
        },
        "plan": {
            "candidate_revision_id": candidate.id if candidate else None,
            "accepted_revision_id": accepted.id if accepted else None,
            "candidate_version": candidate.candidate_version if candidate else None,
            "accepted_version": accepted_link.plan_version if accepted_link else None,
            "status": "accepted" if accepted else "candidate" if candidate else "none",
            "contract": display_plan.chapter_contract if display_plan else None,
            "contract_field_provenance": display_plan.contract_field_provenance if display_plan else {},
            "scene_briefs": [
                {
                    "client_key": b.get("client_key", ""),
                    "order": i,
                    "title": b.get("title", b.get("client_key", "")),
                    "brief": b.get("scene_brief", b.get("brief", {})),
                    "field_provenance": b.get("field_provenance", {}),
                    "status": "accepted" if accepted else "proposed",
                }
                for i, b in enumerate(display_plan.scene_briefs if display_plan else [])
            ],
        },
        "scenes": scene_views,
        "chapter_revision": {
            "staged_revision_id": staged_revision.id if staged_revision else None,
            "accepted_revision_id": chapter.accepted_chapter_revision_id,
            "review_run_id": staged_revision.review_run_id if staged_revision else None,
            "review_issues": staged_revision.review_issues if staged_revision else [],
            "review_summary": staged_revision.review_summary if staged_revision else {},
            "history": revision_history,
        },
        "active_run": run_snapshot,
        "affected_scene_ids": list((active.normalized_input or {}).get("affected_scene_ids", [])) if active else [],
        "stale_scene_ids": list((active.normalized_input or {}).get("stale_scene_ids", [])) if active else [],
        "blocking_reasons": blocking,
        "canon_run_id": canon_run.id if canon_run else None,
        "canon": canon_view,
    }


def aggregate_chapter_revision(
    session: Session,
    chapter_id: str,
    scene_revision_ids: list[str],
    reason: str,
    ctx: CommandContext,
) -> ChapterRevision:
    """按 accepted plan 固化场景版本，生成不可变的 staged 章节版本。

    `scene_revision_ids` 为空时仅在计划没有场景时允许；有计划场景时会从
    当前 accepted scene pointer 读取并再次校验。这样可避免把草稿或旧基线
    混入章节版本，后续提交才能进行确定性的 CAS 检查。
    """
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")

    plan_link = session.execute(
        select(ChapterPlanRevisionLink).where(
            ChapterPlanRevisionLink.chapter_id == chapter_id
        )
    ).scalar_one_or_none()
    expected_scene_ids: list[str] = []
    if plan_link is not None:
        plan = session.get(ChapterPlanRevision, plan_link.plan_revision_id)
        if plan is None or plan.status != "accepted":
            raise AppError("PLAN_NOT_ACCEPTED", "accepted plan is required before aggregation")
        expected_scene_ids = [
            row.scene_id
            for row in session.execute(
                select(ChapterPlanSceneLink)
                .where(ChapterPlanSceneLink.plan_revision_id == plan.id)
                .order_by(ChapterPlanSceneLink.sort_order)
            ).scalars()
        ]

    requested_revision_ids: list[str | None] = list(scene_revision_ids)
    if not requested_revision_ids and expected_scene_ids:
        requested_revision_ids = []
        for scene_id in expected_scene_ids:
            scene = session.get(Scene, scene_id)
            requested_revision_ids.append(scene.accepted_scene_revision_id if scene is not None else None)
        if any(revision_id is None for revision_id in requested_revision_ids):
            raise AppError("SCENE_NOT_ACCEPTED", "all scenes require an accepted scene revision")
    requested_ids: list[str] = [
        revision_id for revision_id in requested_revision_ids if revision_id is not None
    ]
    if expected_scene_ids and len(requested_ids) != len(expected_scene_ids):
        raise AppError("SCENE_NOT_ACCEPTED", "all planned scenes require accepted scene revisions")

    fixed_links: list[tuple[str, str]] = []
    seen_scenes: set[str] = set()
    for scene_revision_id in requested_ids:
        scene_revision = session.get(SceneRevision, scene_revision_id)
        if scene_revision is None or scene_revision.status != "accepted":
            raise AppError("SCENE_NOT_ACCEPTED", "accepted scene revision is required")
        scene = session.get(Scene, scene_revision.scene_id)
        if scene is None or scene.chapter_id != chapter_id:
            raise AppError("SCENE_NOT_ACCEPTED", "scene revision does not belong to the chapter")
        if scene.accepted_scene_revision_id is None:
            raise AppError("SCENE_NOT_ACCEPTED", "accepted scene revision is required")
        if scene.accepted_scene_revision_id != scene_revision.id:
            raise AppError("SCENE_STALE", "scene baseline changed before aggregation")
        if scene.id in seen_scenes:
            raise AppError("SCENE_NOT_ACCEPTED", "a scene may appear only once in a chapter revision")
        seen_scenes.add(scene.id)
        fixed_links.append((scene.id, scene_revision.id))
    if expected_scene_ids and [scene_id for scene_id, _ in fixed_links] != expected_scene_ids:
        raise AppError("PLAN_REVISION_CONFLICT", "scene revisions do not match the accepted plan")

    rev = ChapterRevision(
        chapter_id=chapter_id,
        parent_revision_id=chapter.accepted_chapter_revision_id,
        status="staged",
        reason=reason,
    )
    session.add(rev)
    session.flush()
    for order, (scene_id, scene_rev_id) in enumerate(fixed_links):
        session.add(
            ChapterRevisionScene(
                chapter_revision_id=rev.id,
                scene_id=scene_id,
                scene_revision_id=scene_rev_id,
                sort_order=order,
            )
        )
    session.flush()
    return rev


def commit_chapter_version(
    session: Session,
    chapter_revision_id: str,
    ctx: CommandContext,
) -> ChapterRevision:
    rev = session.execute(
        select(ChapterRevision)
        .where(ChapterRevision.id == chapter_revision_id)
        .with_for_update()
    ).scalar_one_or_none()
    if rev is None:
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter revision does not exist")
    chapter = session.execute(
        select(Chapter).where(Chapter.id == rev.chapter_id).with_for_update()
    ).scalar_one_or_none()
    if chapter is None:
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter does not exist")
    if rev.status == "accepted":
        if chapter.accepted_chapter_revision_id == rev.id:
            return rev
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter revision is no longer the accepted pointer")
    if rev.status != "staged":
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter revision is not staged")
    expected_base = ctx.get("base_chapter_revision_id")
    if expected_base is not None and expected_base != chapter.accepted_chapter_revision_id:
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter baseline is stale")
    if chapter.chapter_sync_status in {"out_of_sync", "stale"} or chapter.entry_handoff_status not in (None, "in_sync"):
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter is out of sync")
    for link in session.execute(
        select(ChapterRevisionScene).where(
            ChapterRevisionScene.chapter_revision_id == rev.id
        )
    ).scalars():
        scene = session.get(Scene, link.scene_id)
        if scene is None or scene.accepted_scene_revision_id != link.scene_revision_id:
            raise AppError("CHAPTER_OUT_OF_SYNC", "scene baseline changed after aggregation")
    rev.status = "accepted"
    chapter.accepted_chapter_revision_id = rev.id
    chapter.chapter_sync_status = "in_sync"
    session.flush()
    # Task 5C：在权威的章节接受事务边界入队 chapter_revision.accepted outbox 事件。
    # API、Worker、领域服务所有章节接受路径都经本函数，保证只入队一次；绝不在此
    # 直接调用 CanonAgent。Canon 消费者按 (chapter_id, accepted_chapter_revision_id)
    # 幂等创建章节 Canon 运行。
    from ..runtime.outbox import PostgresRunOutbox

    run_id = ctx.get("generation_run_id")
    # 仅当接受路径确实属于某个真实运行（API/Worker）时才关联 generation_run_id；
    # 领域服务直接接受（如领域测试）无真实运行，传 None 以便 outbox 幂等入队。
    if run_id is not None and session.get(GenerationRun, run_id) is None:
        run_id = None
    PostgresRunOutbox(session).enqueue(
        {
            "resource_type": "chapter_revision",
            "resource_id": rev.id,
            "payload_schema": "canon-auto.v1",
            "payload": {
                "event_type": "chapter_revision.accepted",
                "chapter_id": rev.chapter_id,
                "accepted_chapter_revision_id": rev.id,
            },
            "producer_command_id": ctx.get("manual_command_id") or ctx.get("idempotency_key") or f"chapter-accept:{rev.id}",
            "generation_run_id": run_id,
        },
        fencing_token=0,
    )
    return rev


def rollback_chapter_revision(
    session: Session,
    chapter_id: str,
    target_revision_id: str,
    ctx: CommandContext,
) -> ChapterRevision:
    """创建回滚到显式目标父版本的新的 staged 章节版本。

    回滚绝不删除原版本，而是创建一条可追溯的新血缘记录，其 parent 指向
    目标版本并记录作者决策，保证历史与审计可还原。目标版本必须属于该章节。
    """
    target = session.get(ChapterRevision, target_revision_id)
    if target is None or target.chapter_id != chapter_id or target.status != "accepted":
        raise AppError("CHAPTER_OUT_OF_SYNC", "target revision does not belong to the chapter")
    rev = ChapterRevision(
        chapter_id=chapter_id,
        parent_revision_id=target_revision_id,
        status="staged",
        reason=f"rollback to {target_revision_id}: {ctx.get('author_decision') or 'author'}",
    )
    session.add(rev)
    session.flush()
    for link in session.execute(
        select(ChapterRevisionScene)
        .where(ChapterRevisionScene.chapter_revision_id == target.id)
        .order_by(ChapterRevisionScene.sort_order)
    ).scalars():
        session.add(
            ChapterRevisionScene(
                chapter_revision_id=rev.id,
                scene_id=link.scene_id,
                scene_revision_id=link.scene_revision_id,
                sort_order=link.sort_order,
            )
        )
    session.flush()
    return rev


def persist_chapter_review_output(
    session: Session,
    chapter_revision_id: str,
    output: object,
    ctx: CommandContext,
) -> ChapterRevision:
    """将 ChapterReviewAgent 的结构化结果写入 staged 版本。

    审校写入与版本聚合在同一 Worker 事务内；只允许修改 staged 版本，避免
    重放或旧 Worker 覆盖已经 accepted 的历史版本。
    """
    revision = session.execute(
        select(ChapterRevision)
        .where(ChapterRevision.id == chapter_revision_id)
        .with_for_update()
    ).scalar_one_or_none()
    if revision is None or revision.status != "staged":
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter review requires a staged revision")
    model_dump = getattr(output, "model_dump", None)
    if callable(model_dump):
        data: dict[str, Any] = model_dump()
    else:
        data = cast(dict[str, Any], output)
    revision.review_issues = list(data.get("review_issues") or [])
    revision.review_summary = {
        "status": data.get("status"),
        "overall_rating": data.get("overall_rating", ""),
        "submitted": bool(data.get("submitted")),
        "clarification_questions": list(data.get("clarification_questions") or []),
    }
    revision.review_run_id = ctx.get("generation_run_id")
    session.flush()
    return revision
