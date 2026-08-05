from __future__ import annotations

import pytest

from app.domain.story_bible import apply_canon_decisions, upsert_canon_candidates
from app.errors import AppError


def _agent_ctx():
    return {
        "generation_run_id": "run-1",
        "agent_run_id": "agent-1",
        "manual_command_id": None,
        "source": "agent",
        "actor_id": "author-1",
        "idempotency_key": "key-a",
        "expected_run_version": 1,
        "lease_context": {"worker_id": "w1", "fencing_token": 1},
        "write_fence": None,
    }


def _candidate(scene_id="scene-1", source_rev="rev-1", fingerprint="fp-1", ctype="fact"):
    return {
        "project_id": "proj-1",
        "chapter_id": "chap-1",
        "scene_id": scene_id,
        "scope": "scene",
        "candidate_type": ctype,
        "fingerprint": fingerprint,
        "source_revision_id": source_rev,
        "content": {"fact": "text"},
        "local_key": "l1",
    }


def test_candidate_source_exactly_one(db):
    bad = _candidate()
    bad["source_revision_id"] = None
    bad["source_draft_artifact_id"] = "draft-1"
    bad["source_change_set_id"] = "cs-1"
    with pytest.raises(AppError) as exc:
        upsert_canon_candidates(db, "run-1", [bad], _agent_ctx())
    assert exc.value.code == "COMMAND_CONTEXT_MISMATCH"


def test_candidate_fingerprint_dedup(db):
    first = upsert_canon_candidates(db, "run-1", [_candidate()], _agent_ctx())
    second = upsert_canon_candidates(db, "run-1", [_candidate()], _agent_ctx())
    assert first[0]["id"] == second[0]["id"]


def test_candidate_status_migration_and_discarded_reject(db):
    """候选只能从 pending 迁移一次；discarded 是终态，已决策候选拒绝重复决策。"""
    cand = upsert_canon_candidates(db, "run-1", [_candidate()], _agent_ctx())[0]
    # pending -> accepted
    records = apply_canon_decisions(
        db,
        [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "accepted"}],
        _agent_ctx(),
    )
    assert records[0].decision == "accepted"
    # 已决策候选拒绝重复决策（并发/重复保护）。
    with pytest.raises(AppError) as exc:
        apply_canon_decisions(
            db,
            [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "rejected"}],
            _agent_ctx(),
        )
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"
    # discarded 是终态：从 pending 丢弃后不可再确认。
    cand2 = upsert_canon_candidates(
        db, "run-1", [_candidate(fingerprint="fp-2")], _agent_ctx()
    )[0]
    apply_canon_decisions(
        db,
        [{"candidate_id": cand2["id"], "candidate_type": "fact", "decision": "discarded"}],
        _agent_ctx(),
    )
    with pytest.raises(AppError) as exc:
        apply_canon_decisions(
            db,
            [{"candidate_id": cand2["id"], "candidate_type": "fact", "decision": "accepted"}],
            _agent_ctx(),
        )
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"
