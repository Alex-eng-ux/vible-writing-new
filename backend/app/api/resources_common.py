"""资源读取的公共查询：获取当前 accepted 指针而非数据库最新行。

读取接口必须返回显式 accepted 的版本指针，绝不能把“最新行”当作
“已接受版本”，否则会读到未提交的草稿或中间状态。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ChapterRevision, ChapterRevisionScene, SceneRevision


def get_accepted_scene_revision(session: Session, scene_id: str) -> SceneRevision | None:
    """返回场景当前 accepted 的 SceneRevision 指针，绝不返回最新行。"""
    return session.execute(
        select(SceneRevision)
        .where(SceneRevision.scene_id == scene_id, SceneRevision.status == "accepted")
        .order_by(SceneRevision.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_accepted_chapter_revision(session: Session, chapter_id: str) -> ChapterRevision | None:
    """返回章节当前 accepted 的 ChapterRevision 指针，绝不返回最新行。"""
    return session.execute(
        select(ChapterRevision)
        .where(ChapterRevision.chapter_id == chapter_id, ChapterRevision.status == "accepted")
        .order_by(ChapterRevision.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def chapter_revision_scene_versions(
    session: Session, chapter_revision_id: str
) -> list[dict[str, str]]:
    """返回某章节版本引用的固定场景版本列表，按 sort_order 排序。

    章节版本在聚合时固定其包含的场景版本清单，事后读取必须按该快照返回，
    不能随场景最新版本漂移。
    """
    rows = session.execute(
        select(ChapterRevisionScene)
        .where(ChapterRevisionScene.chapter_revision_id == chapter_revision_id)
        .order_by(ChapterRevisionScene.sort_order)
    ).scalars().all()
    return [
        {"scene_id": row.scene_id, "scene_revision_id": row.scene_revision_id}
        for row in rows
    ]
