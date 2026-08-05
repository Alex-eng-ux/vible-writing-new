"""作者 ChangeSet 领域服务：创建、提交与空文档基线。

空场景首稿必须先落一条规范化空 ProseMirror 文档的
``SceneDraftArtifact``，再以 ``root_draft_artifact_id`` 一对一关联到
ChangeSet，保证“首稿基线唯一、可回放、可幂等”。基线内容哈希不匹配或
场景已有 accepted 版本时阻断整个事务。创建与提交都会锁定场景行，并在
提交时重新校验当前 accepted 基线，拒绝过期 ChangeSet。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ChangeSet, Scene, SceneDraftArtifact, SceneRevision
from ..errors import AppError
from .drafts import commit_scene_draft, persist_scene_draft
from .interfaces import ChangeSetCommandContext
from .manuscript import commit_scene_change_set
from .prosemirror import (
    apply_prosemirror_steps,
    empty_doc_content,
    empty_doc_hash,
)

__all__ = [
    "empty_doc_content",
    "empty_doc_hash",
    "apply_prosemirror_steps",
    "create_author_change_set",
    "commit_change_set",
]


def _lock_scene(session: Session, scene_id: str) -> Scene:
    """以行锁锁定场景，防止并发创建/提交时校验通过后状态被改变。"""
    scene = session.execute(
        select(Scene).where(Scene.id == scene_id).with_for_update()
    ).scalar_one_or_none()
    if scene is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene not found")
    return scene


def _has_accepted_revision(session: Session, scene_id: str) -> bool:
    """判断场景是否已有 accepted 版本，用于阻止对已接受场景再建首稿。"""
    row = session.execute(
        select(SceneRevision.id)
        .where(SceneRevision.scene_id == scene_id, SceneRevision.status == "accepted")
        .limit(1)
    ).first()
    return row is not None


def _current_accepted_revision_id(session: Session, scene_id: str) -> str | None:
    """返回场景当前 accepted 修订 id（按创建时间取最新），无则返回 None。"""
    row = session.execute(
        select(SceneRevision.id)
        .where(SceneRevision.scene_id == scene_id, SceneRevision.status == "accepted")
        .order_by(SceneRevision.created_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def create_author_change_set(
    session: Session,
    scene_id: str,
    base_scene_revision_id: str | None,
    operation_format: str,
    operations: list[dict],
    base_content_hash: str,
    ctx: ChangeSetCommandContext,
) -> tuple[ChangeSet, SceneDraftArtifact | None]:
    """创建作者 ChangeSet；空场景首稿时先建一对一关联的根草稿。

    - ``base_scene_revision_id is None``：操作作用于规范化空 ProseMirror
      文档。先锁定场景、确认尚无 accepted 版本，再应用操作得到首稿内容并
      创建 ``SceneDraftArtifact``，通过 ``root_draft_artifact_id`` 一对一
      关联。
    - 否则：基线版本必须存在、属于该场景，且其内容哈希与
      ``base_content_hash`` 匹配，否则视为基线过期（SCENE_STALE）。
    """
    if ctx.get("source") != "author":
        raise AppError("COMMAND_CONTEXT_MISMATCH", "author change sets require source=author")
    if operation_format != "prosemirror_step":
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH", "author change sets require prosemirror_step format"
        )
    if not ctx.get("manual_command_id"):
        raise AppError("COMMAND_CONTEXT_MISMATCH", "author change sets require a manual_command_id")

    if base_scene_revision_id is None:
        _lock_scene(session, scene_id)
        if _has_accepted_revision(session, scene_id):
            raise AppError(
                "SCENE_STATE_INCOMPATIBLE", "scene already has an accepted revision"
            )
        if base_content_hash != empty_doc_hash():
            raise AppError("SCENE_STALE", "first draft baseline must match the empty document")
        applied = apply_prosemirror_steps(empty_doc_content(), operations)
        artifact = persist_scene_draft(session, scene_id, applied, None, [], ctx)
        change_set = ChangeSet(
            scene_id=scene_id,
            base_scene_revision_id=None,
            operation_format=operation_format,
            operations=operations,
            base_content_hash=base_content_hash,
            source="author",
            root_draft_artifact_id=artifact.id,
            status="pending",
        )
        session.add(change_set)
        session.flush()
        return change_set, artifact

    revision = session.get(SceneRevision, base_scene_revision_id)
    if revision is None or revision.scene_id != scene_id:
        raise AppError("SCENE_STALE", "base revision does not belong to the scene")
    if base_content_hash != revision.content_hash:
        raise AppError("SCENE_STALE", "base content hash does not match the revision")
    change_set = ChangeSet(
        scene_id=scene_id,
        base_scene_revision_id=base_scene_revision_id,
        operation_format=operation_format,
        operations=operations,
        base_content_hash=base_content_hash,
        source="author",
        root_draft_artifact_id=None,
        status="pending",
    )
    session.add(change_set)
    session.flush()
    return change_set, None


def commit_change_set(
    session: Session,
    change_set_id: str,
    ctx: ChangeSetCommandContext,
) -> SceneRevision:
    """提交 ChangeSet，生成新的 SceneRevision。

    提交前锁定场景并重新校验当前 accepted 基线：
        - 根 ChangeSet（关联根草稿）：作者 accept 时经 ``commit_scene_draft``
          物化；若场景在首稿创建后已被接受过版本则拒绝（SCENE_STALE）。
        - 非根 ChangeSet：其基线必须仍是场景当前 accepted 修订，否则视为
          过期 ChangeSet 拒绝提交（SCENE_STALE）。
    提交成功后把 ChangeSet 状态置为 committed。

    失败条件：非 pending 状态、根草稿非 accept、基线过期或场景不存在时
    抛对应 AppError。
    """
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None or change_set.status != "pending":
        raise AppError("SCENE_STATE_INCOMPATIBLE", "change set is not committable")

    _lock_scene(session, change_set.scene_id)
    current_accepted = _current_accepted_revision_id(session, change_set.scene_id)

    if change_set.root_draft_artifact_id is not None:
        if ctx.get("author_decision") != "accept":
            raise AppError("SCENE_NOT_ACCEPTED", "root draft can only be committed on accept")
        if current_accepted is not None:
            raise AppError("SCENE_STALE", "scene accepted a revision after first-draft creation")
        rev = commit_scene_draft(session, change_set.root_draft_artifact_id, ctx)
    else:
        if change_set.base_scene_revision_id != current_accepted:
            raise AppError("SCENE_STALE", "change set baseline is stale")
        rev = commit_scene_change_set(session, change_set.scene_id, change_set.id, ctx)

    change_set.status = "committed"
    session.flush()
    return rev
