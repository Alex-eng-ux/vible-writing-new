from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Chapter, NovelProject, Scene, SceneRevision, Volume
from app.domain.resources import create_project, create_volume
from app.errors import AppError
from app.services.deletion import delete_project, delete_scene_revision


def _hierarchy(db):
    project = create_project(
        db, "Delete Project", "genre", "reader", "style",
        {"actor_id": "delete-test", "idempotency_key": "delete-project"},
    )
    volume = create_volume(
        db, project.id, "Volume", "goal", "main", "range",
        {"actor_id": "delete-test", "idempotency_key": "delete-volume"},
    )
    chapter = Chapter(
        volume_id=volume.id, title="Chapter", pov="p", chapter_intent={"text": ""}
    )
    db.add(chapter)
    db.flush()
    scene = Scene(chapter_id=chapter.id, title="Scene", scene_brief={"pov": "p"})
    db.add(scene)
    db.flush()
    return project, volume, chapter, scene


def test_delete_project_removes_the_entire_resource_tree(db):
    project, volume, chapter, scene = _hierarchy(db)
    project_id, volume_id, scene_id = project.id, volume.id, scene.id
    db.commit()

    assert delete_project(db, project_id) is True
    db.commit()
    db.expire_all()

    assert db.get(NovelProject, project_id) is None
    assert db.get(Volume, volume_id) is None
    assert db.get(Scene, scene_id) is None


def test_delete_accepted_scene_revision_is_rejected(db):
    _, _, _, scene = _hierarchy(db)
    revision = SceneRevision(
        scene_id=scene.id,
        content='{"type":"doc","content":[]}',
        content_hash="0" * 64,
        reason="test",
        source_ref="test",
        status="accepted",
    )
    db.add(revision)
    db.flush()
    scene.accepted_scene_revision_id = revision.id
    db.commit()

    with pytest.raises(AppError, match="accepted"):
        delete_scene_revision(db, scene.id, revision.id)

    assert db.scalar(select(SceneRevision.id).where(SceneRevision.id == revision.id)) == revision.id
