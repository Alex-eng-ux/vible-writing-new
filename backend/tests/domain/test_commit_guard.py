from __future__ import annotations

import pytest

from app.db.models import GenerationRun
from app.domain.commit_guard import CommitGuard
from app.errors import AppError


def _ctx():
    return {
        "generation_run_id": None,
        "write_fence": None,
        "manual_command_id": "manual-1",
        "source": "author",
        "actor_id": "author-1",
        "idempotency_key": "key-1",
        "expected_run_version": None,
    }


def test_guard_rejects_empty_actor(db):
    guard = CommitGuard(db)
    with pytest.raises(AppError) as exc:
        guard.validate(
            "commit", "", None, "key-1", [], manual_command_id="manual-1"
        )
    assert exc.value.code == "ACTOR_OVERRIDE_FORBIDDEN"


def test_guard_rejects_run_and_manual_both_present(db):
    guard = CommitGuard(db)
    with pytest.raises(AppError) as exc:
        guard.validate(
            "commit", "author-1", None, "key-1", [],
            generation_run_id="run-1", manual_command_id="manual-1",
        )
    assert exc.value.code == "COMMAND_CONTEXT_MISMATCH"


def test_guard_rejects_author_change_set_with_lease(db):
    guard = CommitGuard(db)
    bad = {**_ctx(), "lease_context": {"worker_id": "w1", "fencing_token": 1}}
    with pytest.raises(AppError) as exc:
        guard.validate_change_set_context(bad, "prosemirror_step", "h")
    assert exc.value.code == "COMMAND_CONTEXT_MISMATCH"


def test_guard_rejects_agent_change_set_without_run_identity(db):
    guard = CommitGuard(db)
    bad = {**_ctx(), "source": "agent", "manual_command_id": None,
           "generation_run_id": None, "agent_run_id": None}
    with pytest.raises(AppError) as exc:
        guard.validate_change_set_context(bad, "semantic_text", "h")
    assert exc.value.code == "COMMAND_CONTEXT_MISMATCH"


def test_guard_validates_stale_fencing_token(db):
    run = GenerationRun(
        id="run-1",
        project_id="proj-1",
        status="running",
        write_owner_kind="worker",
        write_owner_id="w1",
        write_fencing_token=5,
    )
    db.add(run)
    db.flush()
    guard = CommitGuard(db)
    with pytest.raises(AppError) as exc:
        guard.validate(
            "commit", "author-1", None, "key-1", [],
            generation_run_id="run-1",
            lease_context={"worker_id": "w1", "fencing_token": 4},
        )
    assert exc.value.code == "RUN_LEASE_LOST"
