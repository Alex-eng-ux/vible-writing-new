"""手稿（场景）领域：场景修订的创建、回滚与 ChangeSet 物化。

场景内容以不可变修订（SceneRevision）链式存储，当前状态由最新修订决定。
所有写操作都依赖父修订（基线）建立版本基线；回滚不会删除旧修订，而是
创建一条以目标为父的新修订以保留可追溯线。写操作必须发生在已通过
CommitGuard 校验的事务内，本模块不自行校验身份。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Scene, SceneRevision
from ..errors import AppError
from .interfaces import ChangeSetCommandContext, CommandContext


def content_hash(content: str) -> str:
    """计算场景内容的 SHA-256 十六进制哈希。

    参数：content 为待哈希的文本内容。
    返回：内容对应的哈希字符串（UTF-8 编码后计算）。
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_scene_revision(session: Session, scene_id: str, revision_id: str) -> SceneRevision:
    """按 id 读取场景修订并校验其确实属于指定场景。

    参数：scene_id 为场景 id，revision_id 为待查找修订 id。
    返回：匹配的 SceneRevision。
    失败条件：修订不存在或归属场景不匹配时抛 SCENE_STALE（SCENE_STALE 表示
    引用的修订不属于该场景，调用方不得继续使用）。
    """
    rev = session.get(SceneRevision, revision_id)
    if rev is None or rev.scene_id != scene_id:
        raise AppError("SCENE_STALE", "scene revision does not belong to the scene")
    return rev


def _create_scene_revision(
    session: Session,
    scene_id: str,
    parent_revision_id: str | None,
    content: str,
    reason: str,
    source_ref: str,
) -> SceneRevision:
    """私有原语：创建一个暂存（staged）的 SceneRevision。

    仅允许在已通过 CommitGuard 校验的事务内调用。
    不得作为 API、Agent 工具或公共服务入口暴露。

    参数：scene_id 为场景 id；parent_revision_id 为父修订 id（可为 None）；
    content 为内容；reason 为变更原因；source_ref 为来源引用。
    返回：新创建的暂存 SceneRevision。
    副作用：向会话新增修订并 flush。
    """
    rev = SceneRevision(
        scene_id=scene_id,
        parent_revision_id=parent_revision_id,
        content=content,
        content_hash=content_hash(content),
        reason=reason,
        source_ref=source_ref,
        status="staged",
    )
    session.add(rev)
    session.flush()
    return rev


def rollback_scene_revision(
    session: Session,
    scene_id: str,
    target_revision_id: str,
    ctx: CommandContext,
) -> SceneRevision:
    """创建一条回退到指定目标父修订的新暂存修订。

    回滚不删除原始版本；它创建一条以目标为父的可追溯新修订。目标必须是
    祖先基线。

    参数：scene_id 为场景 id；target_revision_id 为回退目标修订 id；
    ctx 为命令上下文（用于记录 author_decision）。
    返回：新创建的回滚修订。
    失败条件：目标修订不存在或不属于该场景时抛 SCENE_STALE。
    副作用：创建并 flush 一条新修订；须在已通过 CommitGuard 的事务内调用。
    """
    target = _get_scene_revision(session, scene_id, target_revision_id)
    newest = session.execute(
        select(SceneRevision)
        .where(SceneRevision.scene_id == scene_id)
        .order_by(SceneRevision.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if newest is not None and newest.parent_revision_id != target_revision_id and newest.id != target_revision_id:
        # Rebase: produce content equal to the target while recording the lineage.
        pass
    return _create_scene_revision(
        session,
        scene_id,
        target_revision_id,
        target.content,
        reason=f"rollback to {target_revision_id}: {ctx.get('author_decision') or 'author'}",
        source_ref=f"rollback:{target_revision_id}",
    )


def commit_scene_change_set(
    session: Session,
    scene_id: str,
    change_set_id: str,
    ctx: ChangeSetCommandContext,
) -> SceneRevision:
    """将 ChangeSet 物化为一条新的 SceneRevision（版本化测试）。

    参数：scene_id 为场景 id；change_set_id 为待提交的 ChangeSet id；
    ctx 为 ChangeSet 命令上下文。
    返回：物化后的新修订。

    失败条件：ChangeSet 不存在、不属于该场景或状态非 pending 时抛
    SCENE_STATE_INCOMPATIBLE；基线修订不存在或不属于该场景时抛 SCENE_STALE。

    副作用：把 ChangeSet 的 prosemirror_step 操作真正应用到基线内容并落盘
    （绝不写入占位字符串 "applied"），将 ChangeSet 状态置为 committed，并
    创建、flush 一条修订；作者接受提交时该修订直接成为 accepted。
    """
    from ..db.models import ChangeSet
    from .prosemirror import apply_prosemirror_steps, empty_doc_content

    cs = session.get(ChangeSet, change_set_id)
    if cs is None or cs.scene_id != scene_id or cs.status != "pending":
        raise AppError("SCENE_STATE_INCOMPATIBLE", "change set is not committable")

    if cs.base_scene_revision_id is None:
        base_content = empty_doc_content()
    else:
        base_rev = session.get(SceneRevision, cs.base_scene_revision_id)
        if base_rev is None or base_rev.scene_id != scene_id:
            raise AppError("SCENE_STALE", "base revision does not belong to the scene")
        base_content = base_rev.content

    applied = apply_prosemirror_steps(base_content, cs.operations)
    cs.status = "committed"
    rev = _create_scene_revision(
        session,
        scene_id,
        cs.base_scene_revision_id,
        applied,
        reason="change set commit",
        source_ref=change_set_id,
    )
    # 作者接受提交时，非根 ChangeSet 物化出的修订也直接成为 accepted，
    # 与根首稿提交语义一致，使 accepted 指针随每次提交推进。
    if ctx.get("author_decision") == "accept":
        rev.status = "accepted"
        scene = session.get(Scene, scene_id)
        if scene is not None:
            scene.accepted_scene_revision_id = rev.id
    return rev
