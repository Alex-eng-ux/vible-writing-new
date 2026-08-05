from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Chapter,
    ChapterPlanRevision,
    ChapterPlanRevisionLink,
    ChapterRevision,
    ChapterRevisionScene,
    GenerationRun,
    Scene,
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


def accept_chapter_plan_revision(
    session: Session,
    chapter_id: str,
    plan_revision_id: str,
    expected_current_plan_revision_id: str,
    expected_plan_version: int,
    ctx: CommandContext,
) -> ChapterPlanRevision:
    """CAS-accept a plan revision; the current pointer must match expectations."""
    plan = session.get(ChapterPlanRevision, plan_revision_id)
    if plan is None or plan.chapter_id != chapter_id:
        raise AppError("PLAN_REVISION_CONFLICT", "plan revision does not belong to the chapter")
    if plan.status == "accepted":
        return plan  # idempotent accept
    link = session.execute(
        select(ChapterPlanRevisionLink).where(ChapterPlanRevisionLink.chapter_id == chapter_id)
    ).scalar_one_or_none()
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
    session.flush()
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
    for spec in scene_specs:
        client_key = spec.get("client_key")
        if not client_key:
            raise AppError("PLAN_REVISION_CONFLICT", "scene spec requires a client_key")
        if client_key in mapping:
            raise AppError("PLAN_REVISION_CONFLICT", "duplicate client_key in scene specs")
        if spec.get("scene_id"):
            mapping[client_key] = spec["scene_id"]
            continue
        scene = Scene(
            chapter_id=chapter_id,
            title=spec.get("title", client_key),
            scene_brief=spec.get("scene_brief", {}),
        )
        session.add(scene)
        session.flush()
        mapping[client_key] = scene.id
    return mapping


def aggregate_chapter_revision(
    session: Session,
    chapter_id: str,
    scene_revision_ids: list[str],
    reason: str,
    ctx: CommandContext,
) -> ChapterRevision:
    """Persist a chapter revision with a fixed scene revision list (primitive only)."""
    rev = ChapterRevision(chapter_id=chapter_id, parent_revision_id=None, status="staged", reason=reason)
    session.add(rev)
    session.flush()
    for order, scene_rev_id in enumerate(scene_revision_ids):
        session.add(
            ChapterRevisionScene(
                chapter_revision_id=rev.id,
                scene_id="",
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
    rev = session.get(ChapterRevision, chapter_revision_id)
    if rev is None or rev.status != "staged":
        raise AppError("CHAPTER_OUT_OF_SYNC", "chapter revision is not staged")
    rev.status = "accepted"
    chapter = session.get(Chapter, rev.chapter_id)
    if chapter is not None:
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
            "producer_command_id": ctx.get("manual_command_id") or "chapter-accept",
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
    if target is None or target.chapter_id != chapter_id:
        raise AppError("CHAPTER_OUT_OF_SYNC", "target revision does not belong to the chapter")
    rev = ChapterRevision(
        chapter_id=chapter_id,
        parent_revision_id=target_revision_id,
        status="staged",
        reason=f"rollback to {target_revision_id}: {ctx.get('author_decision') or 'author'}",
    )
    session.add(rev)
    session.flush()
    return rev
