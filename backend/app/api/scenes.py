"""场景资源 API：作者手工 ChangeSet、场景回滚、ChangeSet 提交。

所有命令为幂等命令；作者命令强制 source=author + prosemirror_step 格式，
并携带首次生成的 manual_command_id，generation_run_id/agent_run_id/lease/
write_fence 全部为空，绝不伪造运行身份。
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ChangeSet, Scene, SceneRevision
from ..domain.change_sets import (
    commit_change_set,
    create_author_change_set,
    empty_doc_hash,
)
from ..domain.commit_guard import CommitGuard
from ..domain.idempotency import fingerprint
from ..domain.interfaces import ChangeSetCommandContext, CommandContext, ManualChangeSetContext
from ..domain.manuscript import rollback_scene_revision
from ..errors import AppError
from ..services.deletion import delete_scene, delete_scene_revision
from .commands import execute_command
from .deps import get_actor_id, get_db, get_idempotency_key
from .schemas import (
    ChangeSetCreated,
    ChangeSetRequest,
    CommitRequest,
    RevisionDetail,
    RevisionRead,
    RollbackRequest,
)

router = APIRouter(prefix="/api/scenes", tags=["scenes"])


@router.delete("/{scene_id}", status_code=204)
def remove_scene(
    scene_id: str,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
) -> Response:
    """删除场景及其版本历史、草稿、变更集和运行关联记录。"""
    delete_scene(session, scene_id)
    session.commit()
    return Response(status_code=204)


@router.delete("/{scene_id}/revisions/{revision_id}", status_code=204)
def remove_scene_revision(
    scene_id: str,
    revision_id: str,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
) -> Response:
    """删除单条版本；当前 accepted 版本受保护，避免破坏场景基线。"""
    delete_scene_revision(session, scene_id, revision_id)
    session.commit()
    return Response(status_code=204)


@router.get("/{scene_id}/revisions", response_model=list[RevisionRead])
def list_scene_revisions(
    scene_id: str,
    session: Session = Depends(get_db),
) -> list[RevisionRead]:
    """列出场景全部版本（含历史），按创建时间排序。"""
    if session.get(Scene, scene_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene not found")
    rows = (
        session.execute(
            select(SceneRevision)
            .where(SceneRevision.scene_id == scene_id)
            .order_by(SceneRevision.created_at)
        )
        .scalars()
        .all()
    )
    return [
        RevisionRead(
            id=rev.id,
            parent_revision_id=rev.parent_revision_id,
            scene_id=rev.scene_id,
            content_hash=rev.content_hash,
            status=rev.status,
            reason=rev.reason,
            created_at=rev.created_at.isoformat(),
        )
        for rev in rows
    ]


@router.get("/{scene_id}/revisions/{revision_id}", response_model=RevisionDetail)
def get_scene_revision_detail(
    scene_id: str,
    revision_id: str,
    session: Session = Depends(get_db),
) -> RevisionDetail:
    """读取场景指定版本的详情（含正文字段）。

    只读端点：返回该版本对应的规范化 ProseMirror 正文 JSON（content）与
    来源引用，供前端作为编辑基线与版本比较；不修改任何领域契约。
    """
    rev = session.get(SceneRevision, revision_id)
    if rev is None or rev.scene_id != scene_id:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene revision not found")
    return RevisionDetail(
        id=rev.id,
        parent_revision_id=rev.parent_revision_id,
        scene_id=rev.scene_id,
        content_hash=rev.content_hash,
        status=rev.status,
        reason=rev.reason,
        created_at=rev.created_at.isoformat(),
        content=rev.content,
        source_ref=rev.source_ref,
    )


@router.post("/{scene_id}/changesets", response_model=ChangeSetCreated)
def post_changeset(
    scene_id: str,
    body: ChangeSetRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> ChangeSetCreated:
    """创建作者 ChangeSet；空场景首稿会先建草稿并一对一关联 root_draft。"""
    if session.get(Scene, scene_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene not found")
    if body.source != "author":
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH", "manual scene writes require source=author"
        )
    if body.operation_format != "prosemirror_step":
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH", "author changesets require prosemirror_step format"
        )
    base_hash = body.base_content_hash or empty_doc_hash()
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        assert manual_command_id is not None
        ctx: ManualChangeSetContext = {
            "generation_run_id": None,
            "write_fence": None,
            "manual_command_id": manual_command_id,
            "source": "author",
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "expected_run_version": None,
        }
        CommitGuard(session).validate_manual_change_set_context(
            ctx, body.operation_format, base_hash
        )
        change_set, artifact = create_author_change_set(
            session,
            scene_id,
            body.base_scene_revision_id,
            body.operation_format,
            body.operations,
            base_hash,
            ctx,
        )
        response = {
            "change_set_id": change_set.id,
            "scene_id": scene_id,
            "base_scene_revision_id": change_set.base_scene_revision_id,
            "operation_format": change_set.operation_format,
            "source": change_set.source,
            "base_content_hash": change_set.base_content_hash,
            "draft_artifact_id": artifact.id if artifact else None,
            "manual_command_id": manual_command_id,
        }
        return response, change_set.id

    return ChangeSetCreated(
        **execute_command(
            session,
            f"scene:{scene_id}",
            "scene_changeset",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.post("/{scene_id}/rollback", response_model=RevisionRead)
def post_scene_rollback(
    scene_id: str,
    body: RollbackRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> RevisionRead:
    """回滚场景到显式目标父版本；回滚创建新血缘记录，不删除历史。"""
    if session.get(Scene, scene_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene not found")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        assert manual_command_id is not None
        ctx: ManualChangeSetContext = {
            "generation_run_id": None,
            "write_fence": None,
            "manual_command_id": manual_command_id,
            "source": "author",
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "expected_run_version": None,
        }
        CommitGuard(session).validate_manual_change_set_context(
            ctx, "prosemirror_step", "rollback"
        )
        rev = rollback_scene_revision(
            session,
            scene_id,
            body.target_revision_id,
            cast(
                CommandContext,
                {**ctx, "author_decision": body.author_decision},
            ),
        )
        response = {
            "id": rev.id,
            "parent_revision_id": rev.parent_revision_id,
            "scene_id": rev.scene_id,
            "content_hash": rev.content_hash,
            "status": rev.status,
            "reason": rev.reason,
            "created_at": rev.created_at.isoformat(),
        }
        return response, rev.id

    return RevisionRead(
        **execute_command(
            session,
            f"scene:{scene_id}",
            "scene_rollback",
            idempotency_key,
            request_fp,
            run,
        )
    )


commit_router = APIRouter(prefix="/api/changesets", tags=["changesets"])


@commit_router.post("/{change_set_id}/commit", response_model=RevisionRead)
def post_commit(
    change_set_id: str,
    body: CommitRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> RevisionRead:
    """提交 ChangeSet；根草稿仅允许 accept 时物化，并复用同一 manual_command_id。"""
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "change set not found")
    scene_id = change_set.scene_id
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        assert manual_command_id is not None
        ctx: ManualChangeSetContext = {
            "generation_run_id": None,
            "write_fence": None,
            "manual_command_id": manual_command_id,
            "source": "author",
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "expected_run_version": None,
        }
        CommitGuard(session).validate_manual_change_set_context(
            ctx, change_set.operation_format, change_set.base_content_hash
        )
        rev = commit_change_set(
            session,
            change_set_id,
            cast(
                ChangeSetCommandContext,
                {**ctx, "author_decision": body.author_decision},
            ),
        )
        response = {
            "id": rev.id,
            "parent_revision_id": rev.parent_revision_id,
            "scene_id": rev.scene_id,
            "content_hash": rev.content_hash,
            "status": rev.status,
            "reason": rev.reason,
            "created_at": rev.created_at.isoformat(),
        }
        return response, rev.id

    return RevisionRead(
        **execute_command(
            session,
            f"scene:{scene_id}:changeset:{change_set_id}",
            "changeset_commit",
            idempotency_key,
            request_fp,
            run,
        )
    )
