"""资源删除服务。

删除关系使用 RESTRICT 保护，不能依赖单个 ORM 对象的 cascade。这里先收集目标资源
及其关联行的 id，再按元数据依赖逆序删除，保证项目、卷、章节、场景删除在同一事务内完成。
调用方负责提交事务；任意异常都会由请求层回滚整个删除操作。
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..db.models import Base, Chapter, NovelProject, Scene, SceneRevision, Volume
from ..errors import AppError


def _collect_ids(session: Session, scope_column: str, scope_id: str) -> set[str]:
    """收集目标资源及所有外键风格关联 id，供依赖表逆序清理。"""
    known: set[str] = {scope_id}
    changed = True
    while changed:
        changed = False
        for table in Base.metadata.sorted_tables:
            scope_columns = [
                column
                for column in table.c
                if column.name == scope_column or column.name.endswith("_id")
            ]
            if not scope_columns:
                continue
            predicates = [column.in_(known) for column in scope_columns]
            rows = session.execute(select(table).where(or_(*predicates))).mappings()
            for row in rows:
                for name, value in row.items():
                    if (name == "id" or name.endswith("_id")) and isinstance(value, str):
                        if value not in known:
                            known.add(value)
                            changed = True
    return known


def _delete_by_ids(session: Session, ids: set[str]) -> None:
    """按外键依赖逆序删除关联行，所有删除仍属于调用方事务。"""
    for table in reversed(Base.metadata.sorted_tables):
        predicates = [column.in_(ids) for column in table.c if column.name == "id" or column.name.endswith("_id")]
        if predicates:
            session.execute(delete(table).where(or_(*predicates)))


def delete_project(session: Session, project_id: str) -> bool:
    if session.get(NovelProject, project_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "project not found")
    _delete_by_ids(session, _collect_ids(session, "project_id", project_id))
    return True


def delete_volume(session: Session, volume_id: str) -> bool:
    if session.get(Volume, volume_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "volume not found")
    _delete_by_ids(session, _collect_ids(session, "volume_id", volume_id))
    return True


def delete_chapter(session: Session, chapter_id: str) -> bool:
    if session.get(Chapter, chapter_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter not found")
    _delete_by_ids(session, _collect_ids(session, "chapter_id", chapter_id))
    return True


def delete_scene(session: Session, scene_id: str) -> bool:
    if session.get(Scene, scene_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene not found")
    _delete_by_ids(session, _collect_ids(session, "scene_id", scene_id))
    return True


def delete_scene_revision(session: Session, scene_id: str, revision_id: str) -> bool:
    revision = session.get(SceneRevision, revision_id)
    if revision is None or revision.scene_id != scene_id:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene revision not found")
    scene = session.get(Scene, scene_id)
    if scene is not None and scene.accepted_scene_revision_id == revision_id:
        raise AppError("RESOURCE_REFERENCED", "accepted scene revision cannot be deleted")
    _delete_by_ids(session, _collect_ids(session, "scene_revision_id", revision_id))
    return True
