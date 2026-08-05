from __future__ import annotations

import pytest

from app.domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter,
    create_chapter_plan_revision,
    create_scene,
    materialize_chapter_plan,
)
from app.domain.drafts import commit_scene_draft, persist_scene_draft
from app.domain.manuscript import commit_scene_change_set, rollback_scene_revision
from app.errors import AppError


def _resource_ctx():
    return {"actor_id": "author-1", "idempotency_key": "key-1"}


def _author_change_set_ctx():
    return {
        "generation_run_id": None,
        "write_fence": None,
        "manual_command_id": "manual-1",
        "source": "author",
        "actor_id": "author-1",
        "idempotency_key": "key-1",
        "expected_run_version": None,
    }


def test_scene_parent_chain_and_rollback_preserves_versions(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    scene = create_scene(db, chapter.id, "S1", {"brief": 1}, _resource_ctx())
    draft = persist_scene_draft(db, scene.id, "v1 content", None, [], _author_change_set_ctx())
    rev1 = commit_scene_draft(db, draft.id, {**_author_change_set_ctx(), "author_decision": "accept"})
    assert rev1.parent_revision_id is None
    assert rev1.status == "accepted"

    # Rollback creates a new staged revision, never deletes the original.
    rev2 = rollback_scene_revision(db, scene.id, rev1.id, _author_change_set_ctx())
    assert rev2.parent_revision_id == rev1.id
    assert rev2.status == "staged"
    assert rev2.content == rev1.content


def test_commit_scene_change_set_requires_pending(db, volume):
    from app.db.models import ChangeSet

    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    scene = create_scene(db, chapter.id, "S1", {"brief": 1}, _resource_ctx())
    cs = ChangeSet(
        scene_id=scene.id,
        base_scene_revision_id=None,
        operation_format="prosemirror_step",
        operations=[],
        base_content_hash="h",
        source="author",
        status="committed",
    )
    db.add(cs)
    db.flush()
    with pytest.raises(AppError) as exc:
        commit_scene_change_set(db, scene.id, cs.id, _author_change_set_ctx())
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"


def test_materialize_chapter_plan_maps_client_key_one_to_one(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"contract": 1}, "reason", _author_change_set_ctx())
    accept_chapter_plan_revision(
        db, chapter.id, plan.id, None, 1, _author_change_set_ctx()
    )
    mapping = materialize_chapter_plan(
        db, chapter.id, plan.id, [{"client_key": "k1", "title": "S1"}, {"client_key": "k2", "title": "S2"}],
        _author_change_set_ctx(),
    )
    assert set(mapping.keys()) == {"k1", "k2"}
    assert mapping["k1"] != mapping["k2"]


def test_accept_plan_revision_requires_current_pointer_match(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"contract": 1}, "reason", _author_change_set_ctx())
    with pytest.raises(AppError) as exc:
        accept_chapter_plan_revision(db, chapter.id, plan.id, "wrong-ptr", 1, _author_change_set_ctx())
    assert exc.value.code == "PLAN_REVISION_CONFLICT"
