"""章节资源 API：创建场景、读取章节/场景/版本、读取 handoff、章节回滚。

创建场景与章节回滚为幂等命令；读取只返回 accepted 指针与有效 handoff。
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Chapter,
    ChapterHandoff,
    ChapterPlanRevision,
    ChapterPlanRevisionLink,
    ChapterRevision,
    ChapterRevisionScene,
    Scene,
)
from ..domain.chapters import chapter_workflow_read, create_scene, rollback_chapter_revision
from ..domain.commit_guard import CommitGuard
from ..domain.idempotency import fingerprint
from ..domain.interfaces import CommandContext
from ..errors import AppError
from ..services.deletion import delete_chapter
from .commands import execute_command
from .deps import get_actor_id, get_db, get_idempotency_key
from .resources_common import get_accepted_chapter_revision
from .schemas import (
    ChapterHandoffRead,
    ChapterPlanRead,
    ChapterRead,
    ChapterRevisionRead,
    ChapterWorkflowRead,
    ResourceCreated,
    RollbackRequest,
    SceneCreate,
    SceneRead,
)

router = APIRouter(prefix="/api/chapters", tags=["chapters"])


def _chapter_revision_read(session: Session, revision: ChapterRevision) -> dict:
    """构造包含固定场景映射、审校摘要和当前 accepted 指针的版本读模型。"""
    chapter = session.get(Chapter, revision.chapter_id)
    scene_versions = [
        {
            "scene_id": row.scene_id,
            "scene_revision_id": row.scene_revision_id,
            "sort_order": row.sort_order,
        }
        for row in session.execute(
            select(ChapterRevisionScene)
            .where(ChapterRevisionScene.chapter_revision_id == revision.id)
            .order_by(ChapterRevisionScene.sort_order)
        ).scalars()
    ]
    return {
        "id": revision.id,
        "parent_revision_id": revision.parent_revision_id,
        "chapter_id": revision.chapter_id,
        "status": revision.status,
        "reason": revision.reason,
        "created_at": revision.created_at.isoformat(),
        "scene_versions": scene_versions,
        "review_issues": revision.review_issues or [],
        "review_summary": revision.review_summary or {},
        "is_current_accepted": bool(chapter and chapter.accepted_chapter_revision_id == revision.id),
    }


@router.delete("/{chapter_id}", status_code=204)
def remove_chapter(
    chapter_id: str,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
) -> Response:
    """删除章节及其场景、版本和关联记录；删除边界是单个数据库事务。"""
    delete_chapter(session, chapter_id)
    session.commit()
    return Response(status_code=204)


def _current_valid_handoff(session: Session, chapter_id: str) -> ChapterHandoff | None:
    """返回同时满足 accepted、active、in_sync 且来源版本匹配当前 accepted 的承接。

    先取章节当前 accepted 修订指针；没有 accepted 版本则返回 None。随后只
    返回 status=active、entry_handoff_status=in_sync 且
    source_chapter_revision_id 等于当前 accepted 指针的 handoff，绝不回退到
    旧 handoff 或最新修订。
    """
    accepted = get_accepted_chapter_revision(session, chapter_id)
    if accepted is None:
        return None
    return session.execute(
        select(ChapterHandoff)
        .where(
            ChapterHandoff.chapter_id == chapter_id,
            ChapterHandoff.status == "active",
            ChapterHandoff.entry_handoff_status == "in_sync",
            ChapterHandoff.source_chapter_revision_id == accepted.id,
        )
        .order_by(ChapterHandoff.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.post("/{chapter_id}/scenes", status_code=201, response_model=ResourceCreated)
def post_scene(
    chapter_id: str,
    body: SceneCreate,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> ResourceCreated:
    """在指定章节下创建场景，scene_brief 由服务端从请求字段组装。"""
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    scene_brief = {
        "pov": body.pov,
        "location": body.location,
        "story_time": body.story_time,
        "goal": body.goal,
        "entry_state": body.entry_state,
        "required_beats": body.required_beats,
        "forbidden_beats": body.forbidden_beats,
        "expected_exit_state": body.expected_exit_state,
    }
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        CommitGuard(session).validate(
            "resource_create", actor_id, None, idempotency_key, []
        )
        scene = create_scene(
            session,
            chapter_id,
            body.title,
            scene_brief,
            {"actor_id": actor_id, "idempotency_key": idempotency_key},
        )
        response = {
            "id": scene.id,
            "type": "scene",
            "parent_id": chapter_id,
            "version": 1,
            "created_at": scene.created_at.isoformat(),
        }
        return response, scene.id

    return ResourceCreated(
        **execute_command(
            session,
            f"chapter:{chapter_id}",
            "scene_create",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.get("/{chapter_id}", response_model=ChapterRead)
def get_chapter(
    chapter_id: str,
    session: Session = Depends(get_db),
) -> ChapterRead:
    """读取章节；只返回 accepted 章节版本指针与当前有效 handoff。"""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    accepted = get_accepted_chapter_revision(session, chapter_id)
    handoff = _current_valid_handoff(session, chapter_id)
    return ChapterRead(
        id=chapter.id,
        volume_id=chapter.volume_id,
        title=chapter.title,
        pov=chapter.pov,
        accepted_chapter_revision_id=accepted.id if accepted else None,
        entry_handoff_id=handoff.id if handoff else None,
        created_at=chapter.created_at.isoformat(),
    )


@router.get("/{chapter_id}/plan", response_model=ChapterPlanRead)
def get_chapter_plan(
    chapter_id: str,
    session: Session = Depends(get_db),
) -> ChapterPlanRead:
    """读取章节当前 accepted plan 指针（只读；Task 7B 前端运行创建依赖）。

    只查询 ChapterPlanRevisionLink 指向的 plan 修订；无 accepted plan 时返回
    空指针视图，不修改任何领域契约。
    """
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    link = session.execute(
        select(ChapterPlanRevisionLink).where(
            ChapterPlanRevisionLink.chapter_id == chapter_id
        )
    ).scalar_one_or_none()
    if link is None:
        return ChapterPlanRead(
            chapter_id=chapter_id,
            plan_revision_id=None,
            plan_status=None,
            plan_version=None,
            chapter_contract=None,
            plan_reason=None,
        )
    plan = session.get(ChapterPlanRevision, link.plan_revision_id)
    return ChapterPlanRead(
        chapter_id=chapter_id,
        plan_revision_id=link.plan_revision_id,
        plan_status=plan.status if plan else None,
        plan_version=link.plan_version,
        chapter_contract=plan.chapter_contract if plan else None,
        plan_reason=plan.reason if plan else None,
    )


@router.get("/{chapter_id}/workflow", response_model=ChapterWorkflowRead)
def get_chapter_workflow(
    chapter_id: str,
    session: Session = Depends(get_db),
) -> ChapterWorkflowRead:
    """返回章节工作台单一权威组合视图。"""
    return ChapterWorkflowRead(**chapter_workflow_read(session, chapter_id))



def _plan_read(
    chapter_id: str,
    plan_revision_id: str,
    plan_status: str | None,
    plan_version: int,
    chapter_contract: dict | None,
    plan_reason: str | None,
) -> dict:
    return {
        "chapter_id": chapter_id,
        "plan_revision_id": plan_revision_id,
        "plan_status": plan_status,
        "plan_version": plan_version,
        "chapter_contract": chapter_contract,
        "plan_reason": plan_reason,
    }


@router.get("/{chapter_id}/scenes", response_model=list[SceneRead])
def list_scenes(
    chapter_id: str,
    session: Session = Depends(get_db),
) -> list[SceneRead]:
    """列出章节下的场景；每场景只返回 accepted 场景版本指针。"""
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    rows = (
        session.execute(
            select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.created_at)
        )
        .scalars()
        .all()
    )
    from .resources_common import get_accepted_scene_revision

    result: list[SceneRead] = []
    for scene in rows:
        accepted = get_accepted_scene_revision(session, scene.id)
        result.append(
            SceneRead(
                id=scene.id,
                chapter_id=scene.chapter_id,
                title=scene.title,
                scene_brief=scene.scene_brief,
                accepted_scene_revision_id=accepted.id if accepted else None,
                created_at=scene.created_at.isoformat(),
            )
        )
    return result


@router.get("/{chapter_id}/revisions", response_model=list[ChapterRevisionRead])
def list_chapter_revisions(
    chapter_id: str,
    session: Session = Depends(get_db),
) -> list[ChapterRevisionRead]:
    """列出章节全部版本（含历史），按创建时间排序。"""
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    rows = (
        session.execute(
            select(ChapterRevision)
            .where(ChapterRevision.chapter_id == chapter_id)
            .order_by(ChapterRevision.created_at)
        )
        .scalars()
        .all()
    )
    return [
        ChapterRevisionRead(**_chapter_revision_read(session, rev))
        for rev in rows
    ]


@router.get("/{chapter_id}/revisions/{revision_id}", response_model=ChapterRevisionRead)
def get_chapter_revision(
    chapter_id: str,
    revision_id: str,
    session: Session = Depends(get_db),
) -> ChapterRevisionRead:
    """读取单个章节版本及其固定场景版本映射和审校结果。"""
    revision = session.get(ChapterRevision, revision_id)
    if revision is None or revision.chapter_id != chapter_id:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter revision not found")
    return ChapterRevisionRead(**_chapter_revision_read(session, revision))


@router.get("/{chapter_id}/handoff", response_model=ChapterHandoffRead | None)
def get_chapter_handoff(
    chapter_id: str,
    session: Session = Depends(get_db),
) -> ChapterHandoffRead | None:
    """读取章节的承接 handoff；只返回 accepted+active+in_sync 且来源匹配当前 accepted 的入口。"""
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    handoff = _current_valid_handoff(session, chapter_id)
    if handoff is None:
        return None
    return ChapterHandoffRead(
        id=handoff.id,
        chapter_id=handoff.chapter_id,
        source_chapter_revision_id=handoff.source_chapter_revision_id,
        entry_handoff_status=handoff.entry_handoff_status,
        chain_hash=handoff.chain_hash,
        status=handoff.status,
    )


@router.post("/{chapter_id}/rollback", response_model=ChapterRevisionRead)
def post_chapter_rollback(
    chapter_id: str,
    body: RollbackRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> ChapterRevisionRead:
    """回滚章节到显式目标父版本；回滚创建新血缘记录，不删除历史。"""
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        CommitGuard(session).validate(
            "chapter_rollback", actor_id, None, idempotency_key, []
        )
        rev = rollback_chapter_revision(
            session,
            chapter_id,
            body.target_revision_id,
            cast(
                CommandContext,
                {
                    "actor_id": actor_id,
                    "idempotency_key": idempotency_key,
                    "author_decision": body.author_decision,
                },
            ),
        )
        response = _chapter_revision_read(session, rev)
        return response, rev.id

    return ChapterRevisionRead(
        **execute_command(
            session,
            f"chapter:{chapter_id}",
            "chapter_rollback",
            idempotency_key,
            request_fp,
            run,
        )
    )
