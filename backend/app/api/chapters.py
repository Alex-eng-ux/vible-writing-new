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
    Scene,
)
from ..domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter_plan_revision,
    create_scene,
    materialize_chapter_plan,
    rollback_chapter_revision,
)
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
    ResourceCreated,
    RollbackRequest,
    SceneCreate,
    SceneRead,
)

router = APIRouter(prefix="/api/chapters", tags=["chapters"])


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


# 初始章节计划：把章节现有场景映射进计划（稳定 client_key -> scene_id），
# 无场景时创建空计划。作者接受后（ChapterPlanRevisionLink），场景续写/改写/
# 审校运行即可用。计划内容由 planner 生成仍属后续任务；本接口只做幂等的
# 初始化，不绕过 Task 2 的计划事务。
_INIT_PLAN_REASON = "init-plan"


@router.post("/{chapter_id}/plan", response_model=ChapterPlanRead)
def post_chapter_plan(
    chapter_id: str,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> ChapterPlanRead:
    """为章节创建并接受一个初始章节计划（幂等命令）。

    已存在 accepted plan 时直接返回当前指针（幂等重放）；否则把章节现有场景
    映射进计划（client_key=场景 id，scene_id 复用，不重复建场景）并接受，
    使该章节下的场景运行（continue/rewrite/review）可用。
    """
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    link = session.execute(
        select(ChapterPlanRevisionLink).where(
            ChapterPlanRevisionLink.chapter_id == chapter_id
        )
    ).scalar_one_or_none()

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        ctx: CommandContext = cast(
            CommandContext,
            {
                "lease_context": None,
                "write_fence": None,
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
                "author_decision": None,
                "idempotency_key": idempotency_key,
                "expected_run_version": None,
            },
        )
        if link is not None:
            plan = session.get(ChapterPlanRevision, link.plan_revision_id)
            return _plan_read(
                chapter_id,
                link.plan_revision_id,
                plan.status if plan else None,
                link.plan_version,
                plan.chapter_contract if plan else None,
                plan.reason if plan else None,
            ), link.plan_revision_id
        scenes = session.execute(
            select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.created_at)
        ).scalars().all()
        specs = [
            {"scene_id": s.id, "client_key": s.id, "title": s.title, "scene_brief": s.scene_brief or {}}
            for s in scenes
        ]
        plan = create_chapter_plan_revision(
            session, chapter_id, None, {"scenes": specs, "outline": _INIT_PLAN_REASON}, _INIT_PLAN_REASON, ctx
        )
        accept_chapter_plan_revision(session, chapter_id, plan.id, cast(str, None), 1, ctx)
        materialize_chapter_plan(session, chapter_id, plan.id, specs, ctx)
        return _plan_read(
            chapter_id,
            plan.id,
            plan.status,
            plan.plan_version,
            plan.chapter_contract,
            plan.reason,
        ), plan.id

    request_fp = fingerprint({"chapter_id": chapter_id})
    return ChapterPlanRead(
        **execute_command(
            session,
            f"chapter:{chapter_id}",
            "plan_init",
            idempotency_key,
            request_fp,
            run,
        )
    )


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
        ChapterRevisionRead(
            id=rev.id,
            parent_revision_id=rev.parent_revision_id,
            chapter_id=rev.chapter_id,
            status=rev.status,
            reason=rev.reason,
            created_at=rev.created_at.isoformat(),
        )
        for rev in rows
    ]


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
        response = {
            "id": rev.id,
            "parent_revision_id": rev.parent_revision_id,
            "chapter_id": rev.chapter_id,
            "status": rev.status,
            "reason": rev.reason,
            "created_at": rev.created_at.isoformat(),
        }
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
