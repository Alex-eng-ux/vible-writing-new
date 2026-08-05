from __future__ import annotations

import pytest

from app.context import retrievers
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


def test_metadata_retriever_returns_scene_contract(db, volume):
    from app.domain.chapters import create_chapter, create_scene

    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    scene = create_scene(db, chapter.id, "S1", {"brief": 1}, {"actor_id": "a", "idempotency_key": "k"})
    meta = retrievers.SqlMetadataRetriever(db)
    items = meta.retrieve(_req(scene.id), [])
    kinds = {i["source_type"] for i in items}
    assert "scene" in kinds
    assert any(i["priority"] == 0 for i in items)


def test_metadata_rejects_missing_scene(db, volume):
    meta = retrievers.SqlMetadataRetriever(db)
    with pytest.raises(AppError) as exc:
        meta.retrieve(_req("missing-scene"), [])
    assert exc.value.code == "CONTEXT_SOURCE_UNAVAILABLE"


def test_metadata_filters_accepted_revisions_by_whitelist(db, volume):
    from app.domain.chapters import create_chapter, create_scene
    from app.domain.drafts import commit_scene_draft, persist_scene_draft

    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    scene = create_scene(db, chapter.id, "S1", {"brief": 1}, {"actor_id": "a", "idempotency_key": "k"})
    ctx = {"generation_run_id": None, "write_fence": None, "manual_command_id": "m", "source": "author",
           "actor_id": "a", "idempotency_key": "k", "expected_run_version": None}
    draft = persist_scene_draft(db, scene.id, "accepted content", None, [], ctx)
    rev = commit_scene_draft(db, draft.id, {**ctx, "author_decision": "accept"})
    meta = retrievers.SqlMetadataRetriever(db)
    items = meta.retrieve(_req(scene.id), [rev.id])
    revs = [i for i in items if i["source_type"] == "revision"]
    assert len(revs) == 1
    assert revs[0]["source_revision_id"] == rev.id


def test_metadata_includes_canon_facts_and_entities(db, volume):
    from app.db.models import CanonFact, Entity, Volume
    from app.domain.chapters import create_chapter, create_scene

    project_id = db.get(Volume, volume).project_id
    db.add(CanonFact(project_id=project_id, fact_text="A canon fact", status="active"))
    db.add(Entity(project_id=project_id, name="Hero", kind="character"))
    db.flush()
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    scene = create_scene(db, chapter.id, "S1", {"brief": 1}, {"actor_id": "a", "idempotency_key": "k"})
    meta = retrievers.SqlMetadataRetriever(db)
    items = meta.retrieve(_req(scene.id), [])
    kinds = {i["source_type"] for i in items}
    assert "canon" in kinds
    assert "entity" in kinds


def test_vector_retriever_degrades_when_unavailable(db, volume):
    vec = retrievers.SqlVectorRetriever(db, available=False)
    items = vec.retrieve("query", ["s1"], 10)
    assert items == []


def test_vector_retriever_empty_without_whitelist(db, volume):
    vec = retrievers.SqlVectorRetriever(db, available=True)
    items = vec.retrieve("query", [], 10)
    assert items == []
