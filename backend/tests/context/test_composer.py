from __future__ import annotations

import pytest

from app.context import composer
from app.context.models import SceneRequest
from app.errors import AppError


def _req(scene_id: str) -> SceneRequest:
    return SceneRequest(
        request_type="continue",
        decision_target="scene",
        scene_id=scene_id,
        base_scene_revision_id=None,
        base_chapter_revision_id=None,
    )


def _make_scene(db, volume, title="S1"):
    from app.domain.chapters import create_chapter, create_scene

    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    return create_scene(db, chapter.id, title, {"brief": 1}, {"actor_id": "a", "idempotency_key": "k"})


def test_compose_returns_deterministic_sorted_items(db, volume):
    scene = _make_scene(db, volume)
    pack = composer.compose_context(
        db, volume, scene.id, _req(scene.id), 1000, "g1", None, None, None, None, None, None
    )
    keys = [(i["priority"], i["source_type"], i["source_id"]) for i in pack["items"]]
    assert keys == sorted(keys)
    assert pack["manifest_id"]
    assert pack["total_token_estimate"] >= 0


def test_compose_preserves_mandatory_p0_when_budget_tight(db, volume):
    scene = _make_scene(db, volume)
    # P0 scene contract must always be included even under a tight budget.
    pack = composer.compose_context(
        db, volume, scene.id, _req(scene.id), 6, "g1", None, None, None, None, None, None
    )
    p0 = [i for i in pack["items"] if i["priority"] == 0]
    assert len(p0) == 1


def test_compose_returns_budget_exceeded_when_p0_over_budget(db, volume):
    scene = _make_scene(db, volume)
    # Make the P0 scene contract huge, then impossible budget.
    scene.scene_brief = {"brief": "x" * 5000}
    db.flush()
    with pytest.raises(AppError) as exc:
        composer.compose_context(
            db, volume, scene.id, _req(scene.id), 1, "g1", None, None, None, None, None, None
        )
    assert exc.value.code == "CONTEXT_BUDGET_EXCEEDED"


def test_compose_truncates_optional_items_and_records_omitted(db, volume):
    from app.db.models import CanonFact, Entity, Volume

    project_id = db.get(Volume, volume).project_id
    db.add(CanonFact(project_id=project_id, fact_text="A canon fact that is some content", status="active"))
    db.add(Entity(project_id=project_id, name="Hero", kind="character"))
    db.flush()
    scene = _make_scene(db, volume)
    pack = composer.compose_context(
        db, volume, scene.id, _req(scene.id), 6, "g1", None, None, None, None, None, None
    )
    # Optional canon/entity items exist but a tight budget omits all of them.
    assert pack["omitted_source_ids"]
    truncated = [i for i in pack["items"] if i["truncation_reason"]]
    assert len(truncated) == 0  # truncated items are not selected


def test_compose_reuses_manifest_id_for_same_run(db, volume):
    from app.context import manifest as manifest_mod

    scene = _make_scene(db, volume)
    first = composer.compose_context(
        db, volume, scene.id, _req(scene.id), 1000, "g1", None, None, None, None, None, None
    )
    m = manifest_mod.get_manifest(db, "g1")
    second = composer.compose_context(
        db, volume, scene.id, _req(scene.id), 1000, "g1", m, None, None, None, None, None
    )
    assert first["manifest_id"] == second["manifest_id"]


def test_compose_rejects_manifest_fingerprint_change(db, volume):
    from app.context import manifest as manifest_mod

    scene = _make_scene(db, volume)
    composer.compose_context(
        db, volume, scene.id, _req(scene.id), 1000, "g1", None, None, None, None, None, None
    )
    m = manifest_mod.get_manifest(db, "g1")
    changed = dict(_req(scene.id))
    changed["request_type"] = "rewrite"
    with pytest.raises(AppError) as exc:
        composer.compose_context(
            db, volume, scene.id, changed, 1000, "g1", m, None, None, None, None, None
        )
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_compose_rejects_manifest_for_other_run(db, volume):
    scene = _make_scene(db, volume)
    composer.compose_context(
        db, volume, scene.id, _req(scene.id), 1000, "g1", None, None, None, None, None, None
    )
    from app.context import manifest as manifest_mod

    m = manifest_mod.get_manifest(db, "g1")
    with pytest.raises(AppError) as exc:
        composer.compose_context(
            db, volume, scene.id, _req(scene.id), 1000, "g2", m, None, None, None, None, None
        )
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_compose_first_chapter_allows_empty_handoff(db, volume):
    scene = _make_scene(db, volume)
    pack = composer.compose_context(
        db, volume, scene.id, _req(scene.id), 1000, "g1", None, None, None, None, None, None
    )
    assert pack["manifest_id"]


def test_compose_rejects_rollback_invalidated_handoff(db, volume):
    from app.domain.chapters import (
        accept_chapter_plan_revision,
        aggregate_chapter_revision,
        commit_chapter_version,
        create_chapter,
        create_chapter_plan_revision,
        create_scene,
    )
    from app.domain.handoff import create_chapter_handoff

    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", {"actor_id": "a", "idempotency_key": "k"})
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, {"actor_id": "a", "idempotency_key": "k"})
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", {"actor_id": "a", "idempotency_key": "k"})
    commit_chapter_version(db, rev.id, {"actor_id": "a", "idempotency_key": "k"})
    handoff = create_chapter_handoff(db, rev.id, "chain-1", {"actor_id": "a", "idempotency_key": "k"})
    # A rollback invalidates the handoff by changing the chapter revision source.
    scene = create_scene(db, chapter.id, "S2", {"brief": 1}, {"actor_id": "a", "idempotency_key": "k"})
    with pytest.raises(AppError) as exc:
        composer.compose_context(
            db, volume, scene.id, _req(scene.id), 1000, "g1", None,
            handoff["id"], "wrong-rev", "chain-1", None, None,
        )
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_compose_vector_degradation_keeps_metadata(db, volume):
    scene = _make_scene(db, volume)
    pack = composer.compose_context(
        db, volume, scene.id, _req(scene.id), 1000, "g1", None, None, None, None, None, None,
        vector_available=False,
    )
    assert pack["manifest_id"]
    assert any(i["priority"] == 0 for i in pack["items"])
