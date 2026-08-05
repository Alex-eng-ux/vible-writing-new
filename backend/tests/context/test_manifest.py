from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.context import manifest
from app.context.models import ContextManifestEntry
from app.errors import AppError

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def _entry(source_id: str, source_type: str, rev: str | None) -> ContextManifestEntry:
    return ContextManifestEntry(
        source_id=source_id,
        source_type=source_type,
        source_revision_id=rev,
        resolved_at=NOW,
    )


def test_manifest_create_then_reuse_same_id(db):
    entries = [_entry("s1", "revision", "r1")]
    first = manifest.create_or_reuse(db, "g1", "fp", entries, None, None, None)
    second = manifest.create_or_reuse(db, "g1", "fp", entries, None, None, None)
    assert first["manifest_id"] == second["manifest_id"]
    assert first["request_fingerprint"] == "fp"


def test_manifest_reuse_keeps_source_order_and_versions(db):
    entries = [_entry("s1", "revision", "r1"), _entry("c1", "canon", None)]
    first = manifest.create_or_reuse(db, "g1", "fp", entries, None, None, None)
    second = manifest.create_or_reuse(db, "g1", "fp", entries, None, None, None)
    assert [e["source_id"] for e in second["entries"]] == [e["source_id"] for e in first["entries"]]
    s1 = next(e for e in second["entries"] if e["source_id"] == "s1")
    assert s1["source_revision_id"] == "r1"
    c1 = next(e for e in second["entries"] if e["source_id"] == "c1")
    assert c1["source_revision_id"] is None


def test_manifest_rejects_fingerprint_change(db):
    entries = [_entry("s1", "revision", "r1")]
    manifest.create_or_reuse(db, "g1", "fp1", entries, None, None, None)
    with pytest.raises(AppError) as exc:
        manifest.create_or_reuse(db, "g1", "fp2", entries, None, None, None)
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_manifest_rejects_source_set_change(db):
    manifest.create_or_reuse(db, "g1", "fp", [_entry("s1", "revision", "r1")], None, None, None)
    with pytest.raises(AppError) as exc:
        manifest.create_or_reuse(
            db, "g1", "fp", [_entry("s1", "revision", "r1"), _entry("c1", "canon", None)], None, None, None
        )
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_manifest_rejects_version_mapping_change(db):
    manifest.create_or_reuse(db, "g1", "fp", [_entry("s1", "revision", "r1")], None, None, None)
    with pytest.raises(AppError) as exc:
        manifest.create_or_reuse(db, "g1", "fp", [_entry("s1", "revision", "r2")], None, None, None)
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_manifest_rejects_handoff_chain_hash_change(db):
    entries = [_entry("s1", "revision", "r1")]
    manifest.create_or_reuse(db, "g1", "fp", entries, "h1", "cr1", "chain-1")
    with pytest.raises(AppError) as exc:
        manifest.create_or_reuse(db, "g1", "fp", entries, "h1", "cr1", "chain-2")
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_validate_replay_rejects_wrong_run(db):
    m = manifest.create_or_reuse(db, "g1", "fp", [_entry("s1", "revision", "r1")], None, None, None)
    with pytest.raises(AppError) as exc:
        manifest.validate_replay(db, "g2", m, "fp")
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_validate_replay_rejects_fingerprint_change(db):
    m = manifest.create_or_reuse(db, "g1", "fp", [_entry("s1", "revision", "r1")], None, None, None)
    with pytest.raises(AppError) as exc:
        manifest.validate_replay(db, "g1", m, "other-fp")
    assert exc.value.code == "CONTEXT_MANIFEST_MISMATCH"


def test_validate_replay_passes_for_matching_run(db):
    m = manifest.create_or_reuse(db, "g1", "fp", [_entry("s1", "revision", "r1")], None, None, None)
    manifest.validate_replay(db, "g1", m, "fp")  # should not raise
