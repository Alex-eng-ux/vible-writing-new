from __future__ import annotations

import pytest

from app.agents.schemas import ChapterReviewOutput, ReviewIssue
from app.db.models import ChapterRevisionScene, Scene, SceneRevision
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    commit_chapter_version,
    create_chapter,
    create_chapter_plan_revision,
    rollback_chapter_revision,
)
from app.errors import AppError


def _ctx(**extra: object) -> dict:
    value = {
        "actor_id": "author-1",
        "idempotency_key": "task5-command",
        "manual_command_id": "task5-command",
        "source": "author",
        "generation_run_id": None,
        "base_chapter_revision_id": None,
    }
    value.update(extra)
    return value


def _chapter_with_plan(db, volume):
    chapter = create_chapter(db, volume, "Chapter", "pov", {"text": "intent"}, _ctx())
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scenes": [{"client_key": "s1", "title": "Scene 1", "scene_brief": {}}]},
        "task5",
        _ctx(),
    )
    plan.scene_briefs = [{"client_key": "s1", "title": "Scene 1", "scene_brief": {}}]
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _ctx())
    chapter.chapter_sync_status = "in_sync"
    db.flush()
    scene = db.query(Scene).filter_by(chapter_id=chapter.id).one()
    scene_revision = SceneRevision(
        scene_id=scene.id,
        content="scene text",
        content_hash="a" * 64,
        reason="seed",
        source_ref="task5",
        status="accepted",
    )
    db.add(scene_revision)
    db.flush()
    scene.accepted_scene_revision_id = scene_revision.id
    db.flush()
    return chapter, scene, scene_revision


def test_aggregate_persists_scene_mapping_and_parent_revision(db, volume):
    chapter, scene, scene_revision = _chapter_with_plan(db, volume)

    revision = aggregate_chapter_revision(
        db, chapter.id, [scene_revision.id], "aggregate", _ctx()
    )

    link = db.query(ChapterRevisionScene).filter_by(chapter_revision_id=revision.id).one()
    assert link.scene_id == scene.id
    assert link.scene_revision_id == scene_revision.id
    assert revision.parent_revision_id is None


def test_aggregate_rejects_scene_revision_not_currently_accepted(db, volume):
    chapter, scene, scene_revision = _chapter_with_plan(db, volume)
    scene.accepted_scene_revision_id = None
    db.flush()

    with pytest.raises(AppError, match="accepted scene revision"):
        aggregate_chapter_revision(db, chapter.id, [scene_revision.id], "aggregate", _ctx())


def test_commit_rejects_changed_scene_baseline_and_keeps_staged_revision(db, volume):
    chapter, scene, scene_revision = _chapter_with_plan(db, volume)
    revision = aggregate_chapter_revision(
        db, chapter.id, [scene_revision.id], "aggregate", _ctx()
    )
    newer = SceneRevision(
        scene_id=scene.id,
        parent_revision_id=scene_revision.id,
        content="new scene text",
        content_hash="b" * 64,
        reason="edit",
        source_ref="task5",
        status="accepted",
    )
    db.add(newer)
    db.flush()
    scene.accepted_scene_revision_id = newer.id
    db.flush()

    with pytest.raises(AppError, match="scene baseline"):
        commit_chapter_version(db, revision.id, _ctx())
    assert revision.status == "staged"
    assert chapter.accepted_chapter_revision_id is None


def test_rollback_copies_fixed_scene_versions(db, volume):
    chapter, scene, scene_revision = _chapter_with_plan(db, volume)
    original = aggregate_chapter_revision(
        db, chapter.id, [scene_revision.id], "aggregate", _ctx()
    )
    commit_chapter_version(db, original.id, _ctx())

    rollback = rollback_chapter_revision(db, chapter.id, original.id, _ctx())

    copied = db.query(ChapterRevisionScene).filter_by(chapter_revision_id=rollback.id).one()
    assert rollback.status == "staged"
    assert rollback.parent_revision_id == original.id
    assert copied.scene_id == scene.id
    assert copied.scene_revision_id == scene_revision.id


def test_review_result_is_persisted_on_staged_revision_and_workflow_readable(db, volume):
    chapter, scene, scene_revision = _chapter_with_plan(db, volume)
    revision = aggregate_chapter_revision(
        db, chapter.id, [scene_revision.id], "aggregate", _ctx()
    )
    output = ChapterReviewOutput(
        status="ready",
        review_issues=[
            ReviewIssue(
                local_key="timeline-1",
                issue_type="timeline",
                severity="medium",
                problem="timeline is unclear",
            )
        ],
        overall_rating="needs_revision",
        submitted=True,
    )

    from app.domain import chapters as chapter_domain

    chapter_domain.persist_chapter_review_output(db, revision.id, output, _ctx())
    db.refresh(revision)
    assert revision.review_summary["overall_rating"] == "needs_revision"
    assert revision.review_issues[0]["problem"] == "timeline is unclear"
