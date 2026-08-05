from __future__ import annotations

from datetime import timedelta

import pytest

from app.agents.graph import SceneGraph
from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.agents.state import ChapterRunState
from app.db.models import GenerationRun, RunLease, utcnow
from app.errors import AppError
from app.runtime.executor import RunExecutor
from app.runtime.leases import LeaseRepository
from app.runtime.run_identity import RunIdentity


def _create_run(db, run_id="g1") -> None:
    db.add(GenerationRun(id=run_id, project_id="p1", status="running"))
    db.flush()


def _identity(run_id="g1", worker="w1") -> RunIdentity:
    return RunIdentity(
        generation_run_id=run_id,
        agent_run_id="a1",
        agent_attempt_key="ak1",
        parent_generation_run_id=None,
        supersedes_run_id=None,
        parent_plan_revision_id=None,
    )


def _env() -> AgentInputEnvelope:
    return AgentInputEnvelope(
        runtime_context=RuntimeContext(
            generation_run_id="g1", agent_run_id="a1", agent_attempt_key="ak1", thread_id="g1"
        ),
        scene_brief={"goal": "x"},
        context_manifest=[],
    )


def test_claim_returns_lease_and_binds_run(db):
    _create_run(db)
    leases = LeaseRepository(db)
    lease = leases.claim(_identity(), "w1")
    assert lease["fencing_token"] == 1
    assert lease["worker_id"] == "w1"
    run = db.get(GenerationRun, "g1")
    assert run.write_owner_id == "w1"
    assert run.write_fencing_token == 1


def test_renew_ok_for_current_worker(db):
    _create_run(db)
    leases = LeaseRepository(db)
    lease = leases.claim(_identity(), "w1")
    renewed = leases.renew("g1", "w1", lease["fencing_token"], lease["lease_token"])
    assert renewed["fencing_token"] == lease["fencing_token"]


def test_renew_rejects_old_worker(db):
    _create_run(db)
    leases = LeaseRepository(db)
    lease = leases.claim(_identity(), "w1")
    with pytest.raises(AppError) as exc:
        leases.renew("g1", "w2", lease["fencing_token"], lease["lease_token"])
    assert exc.value.code == "RUN_LEASE_LOST"


def test_renew_rejects_old_token_after_takeover(db):
    _create_run(db)
    leases = LeaseRepository(db)
    old = leases.claim(_identity(), "w1")
    # New worker takes over -> increments fencing token.
    new = leases.claim(_identity("g1", "w2"), "w2")
    assert new["fencing_token"] == old["fencing_token"] + 1
    with pytest.raises(AppError) as exc:
        leases.renew("g1", "w1", old["fencing_token"], old["lease_token"])
    assert exc.value.code == "RUN_LEASE_LOST"


def test_old_worker_write_rejected_by_executor(db):
    _create_run(db)
    leases = LeaseRepository(db)
    old = leases.claim(_identity(), "w1")
    leases.claim(_identity("g1", "w2"), "w2")  # w2 takes over
    executor = RunExecutor(leases, SceneGraph(HookRegistry(), AgentResultRouter()), _identity())
    with pytest.raises(AppError) as exc:
        executor.execute(
            "g1", "w1", old["fencing_token"], old["lease_token"], _state(), _env()
        )
    assert exc.value.code == "RUN_LEASE_LOST"


def test_reclaim_expired_takes_over_lease(db):
    _create_run(db)
    leases = LeaseRepository(db)
    leases.claim(_identity(), "w1")
    # Force the lease to be expired.
    db.execute(
        RunLease.__table__.update()
        .where(RunLease.generation_run_id == "g1")
        .values(lease_expires_at=utcnow() - timedelta(seconds=10))
    )
    run = db.get(GenerationRun, "g1")
    run.lease_expires_at = utcnow() - timedelta(seconds=10)
    db.flush()
    count = leases.reclaim_expired(utcnow())
    assert count == 1


def test_executor_runs_graph_from_checkpoint(db):
    _create_run(db)
    leases = LeaseRepository(db)
    lease = leases.claim(_identity(), "w1")
    executor = RunExecutor(leases, SceneGraph(HookRegistry(), AgentResultRouter()), _identity())
    result = executor.execute(
        "g1", "w1", lease["fencing_token"], lease["lease_token"], _state(), _env()
    )
    assert result["last_durable_node"] in ("writing", "continuity", "review", "revision")


def _state() -> ChapterRunState:
    return ChapterRunState(generation_run_id="g1", run_version=1)
