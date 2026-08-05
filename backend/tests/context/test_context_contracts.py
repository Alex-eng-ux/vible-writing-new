from __future__ import annotations

import pytest

from app.context import composer
from app.context.models import ContextItem, ContextManifest, ContextPack, SceneRequest
from app.errors import AppError


def test_scene_request_schema_accepts_valid_request_types():
    req = SceneRequest(
        request_type="continue",
        decision_target="scene",
        scene_id="s1",
        base_scene_revision_id="r1",
        base_chapter_revision_id=None,
    )
    assert req["request_type"] == "continue"
    assert req["decision_target"] == "scene"


def test_context_item_requires_source_fields():
    item = ContextItem(
        source_id="s1",
        source_type="revision",
        source_revision_id="r1",
        priority=0,
        content="text",
        token_estimate=4,
        truncation_reason=None,
        metadata={},
    )
    assert item["source_type"] == "revision"
    assert item["priority"] == 0


def test_context_manifest_has_required_fields():
    m = ContextManifest(
        manifest_id="m1",
        generation_run_id="g1",
        request_fingerprint="fp",
        entries=[],
        entry_handoff_id=None,
        entry_source_chapter_revision_id=None,
        entry_handoff_chain_hash=None,
    )
    assert m["manifest_id"] == "m1"
    assert m["generation_run_id"] == "g1"


def test_context_pack_has_required_fields():
    pack = ContextPack(
        generation_run_id="g1",
        scene_id="s1",
        items=[],
        total_token_estimate=0,
        omitted_source_ids=[],
        manifest_id="m1",
    )
    assert pack["scene_id"] == "s1"
    assert pack["manifest_id"] == "m1"


def test_compose_rejects_non_positive_budget(db, volume):
    from app.domain.chapters import create_chapter, create_scene

    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    scene = create_scene(db, chapter.id, "S1", {"brief": 1}, {"actor_id": "a", "idempotency_key": "k"})
    req = SceneRequest(
        request_type="continue",
        decision_target="scene",
        scene_id=scene.id,
        base_scene_revision_id=None,
        base_chapter_revision_id=None,
    )
    with pytest.raises(AppError) as exc:
        composer.compose_context(
            db, volume, scene.id, req, 0, "g1", None, None, None, None, None, None
        )
    assert exc.value.code == "VALIDATION_ERROR"
