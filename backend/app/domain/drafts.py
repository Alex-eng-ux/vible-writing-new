"""场景草稿领域：首稿草稿的幂等持久化与作者接受后的物化。

草稿按不同来源采用不同幂等键：自动运行按 (generation_run_id,
agent_run_id, idempotency_key)，作者根编辑按 (manual_command_id,
idempotency_key)。只有作者接受（author_decision == accept）才能把草稿
物化为已接受的场景修订。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Scene, SceneDraftArtifact, SceneRevision
from ..errors import AppError
from .interfaces import ChangeSetCommandContext
from .manuscript import _create_scene_revision, content_hash


def persist_scene_draft(
    session: Session,
    scene_id: str,
    content: str,
    base_scene_revision_id: str | None,
    source_refs: list[str],
    ctx: ChangeSetCommandContext,
) -> SceneDraftArtifact:
    """幂等持久化一个首稿草稿。

    自动运行按 (generation_run_id, agent_run_id, idempotency_key) 去重；
    作者根编辑按 (manual_command_id, idempotency_key) 去重。

    参数：scene_id 为场景 id；content 为草稿内容；base_scene_revision_id 为
    基线修订 id；source_refs 为来源引用；ctx 为 ChangeSet 命令上下文。
    返回：新创建或已存在的 SceneDraftArtifact。

    幂等约束：按上述来源对应的唯一键去重，已存在时直接返回既有草稿。

    副作用：新增草稿并 flush；须在已通过 CommitGuard 的事务内调用。
    """
    if ctx.get("source") == "author":
        run_key = None
        agent_key = None
    else:
        run_key = ctx.get("generation_run_id")
        agent_key = ctx.get("agent_run_id")

    existing = None
    if ctx.get("source") == "author":
        existing = session.execute(
            select(SceneDraftArtifact).where(
                SceneDraftArtifact.scene_id == scene_id,
                SceneDraftArtifact.manual_command_id == ctx.get("manual_command_id"),
                SceneDraftArtifact.idempotency_key == ctx["idempotency_key"],
            )
        ).scalar_one_or_none()
    else:
        existing = session.execute(
            select(SceneDraftArtifact).where(
                SceneDraftArtifact.scene_id == scene_id,
                SceneDraftArtifact.generation_run_id == ctx.get("generation_run_id"),
                SceneDraftArtifact.agent_run_id == ctx.get("agent_run_id"),
                SceneDraftArtifact.idempotency_key == ctx["idempotency_key"],
            )
        ).scalar_one_or_none()

    if existing is not None:
        return existing

    artifact = SceneDraftArtifact(
        scene_id=scene_id,
        content=content,
        content_hash=content_hash(content),
        status="pending",
        generation_run_id=run_key,
        agent_run_id=agent_key,
        manual_command_id=ctx.get("manual_command_id"),
        idempotency_key=ctx["idempotency_key"],
    )
    session.add(artifact)
    session.flush()
    return artifact


def commit_scene_draft(
    session: Session,
    draft_artifact_id: str,
    ctx: ChangeSetCommandContext,
) -> SceneRevision:
    """在作者接受时把首稿草稿物化为一个 SceneRevision。

    只有作者接受才能物化；草稿必须处于 pending；当基线为 None 时场景不得
    已存在已接受的修订。

    参数：draft_artifact_id 为草稿 id；ctx 为 ChangeSet 命令上下文。
    返回：物化后的 SceneRevision（状态为 accepted）。

    失败条件：
        - 草稿不存在或状态非 pending：SCENE_STATE_INCOMPATIBLE。
        - 作者决策非 accept：SCENE_NOT_ACCEPTED。

    副作用：将草稿状态置为 accepted，并创建、flush 一条 accepted 修订；
    须在已通过 CommitGuard 的事务内调用。
    """
    artifact = session.get(SceneDraftArtifact, draft_artifact_id)
    if artifact is None or artifact.status != "pending":
        raise AppError("SCENE_STATE_INCOMPATIBLE", "draft is not committable")
    if ctx.get("author_decision") != "accept":
        raise AppError("SCENE_NOT_ACCEPTED", "draft can only be materialized on author accept")

    # 根草稿基线为 None：场景必须还没有 accepted 版本，否则拒绝物化。
    if _has_accepted_revision(session, artifact.scene_id):
        raise AppError("SCENE_STATE_INCOMPATIBLE", "scene already has an accepted revision")
    artifact.status = "accepted"
    rev = _create_scene_revision(
        session,
        artifact.scene_id,
        None,
        artifact.content,
        reason="first draft accepted",
        source_ref=draft_artifact_id,
    )
    rev.status = "accepted"
    scene = session.get(Scene, artifact.scene_id)
    if scene is not None:
        scene.accepted_scene_revision_id = rev.id
    return rev


def _has_accepted_revision(session: Session, scene_id: str) -> bool:
    """判断场景是否已存在 accepted 状态的修订。

    使用显式 accepted_scene_revision_id 指针，不按“最新行”推断。
    """
    scene = session.get(Scene, scene_id)
    if scene is not None and scene.accepted_scene_revision_id is not None:
        return True
    row = session.execute(
        select(SceneRevision.id)
        .where(SceneRevision.scene_id == scene_id, SceneRevision.status == "accepted")
        .limit(1)
    ).first()
    return row is not None
