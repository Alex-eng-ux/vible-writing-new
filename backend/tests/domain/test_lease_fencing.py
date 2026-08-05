from __future__ import annotations

import pytest

from app.db.models import GenerationRun
from app.domain.lease import validate_lease, validate_write_fence
from app.errors import AppError


def _run(db, token=1, owner_kind="worker", owner_id="w1"):
    run = GenerationRun(
        id="run-1",
        project_id="proj-1",
        status="running",
        write_owner_kind=owner_kind,
        write_owner_id=owner_id,
        write_fencing_token=token,
    )
    db.add(run)
    db.flush()
    return run


def test_current_lease_passes(db):
    _run(db, token=1)
    validate_lease(db, {"worker_id": "w1", "fencing_token": 1}, "run-1")


def test_old_fencing_token_rejected(db):
    _run(db, token=5)
    with pytest.raises(AppError) as exc:
        validate_lease(db, {"worker_id": "w1", "fencing_token": 4}, "run-1")
    assert exc.value.code == "RUN_LEASE_LOST"


def test_owner_mismatch_rejected(db):
    _run(db, token=1, owner_id="w2")
    with pytest.raises(AppError) as exc:
        validate_lease(db, {"worker_id": "w1", "fencing_token": 1}, "run-1")
    assert exc.value.code == "RUN_LEASE_LOST"


def test_missing_lease_rejected(db):
    with pytest.raises(AppError) as exc:
        validate_lease(db, None, "run-1")
    assert exc.value.code == "RUN_LEASE_LOST"


def test_write_fence_current_token_passes(db):
    _run(db, token=7, owner_kind="api_command", owner_id="manual-1")
    validate_write_fence(
        db, {"generation_run_id": "run-1", "owner_kind": "api_command", "owner_id": "manual-1", "fencing_token": 7}
    )


def test_write_fence_stale_token_rejected(db):
    _run(db, token=7, owner_kind="api_command", owner_id="manual-1")
    with pytest.raises(AppError) as exc:
        validate_write_fence(
            db, {"generation_run_id": "run-1", "owner_kind": "api_command", "owner_id": "manual-1", "fencing_token": 6}
        )
    assert exc.value.code == "RUN_LEASE_LOST"
