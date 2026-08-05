from __future__ import annotations

import os
import uuid

import pytest

from app.agents.graph import SceneGraph
from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.agents.state import ChapterRunState
from app.db.models import GenerationRun
from app.errors import AppError
from app.runtime.checkpointer import build_postgres_checkpointer, setup_checkpoint_tables
from app.runtime.executor import RunExecutor
from app.runtime.leases import LeaseRepository
from app.runtime.run_identity import RunIdentity

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/novel_test",
)


def _state(run_id: str) -> ChapterRunState:
    return ChapterRunState(
        generation_run_id=run_id,
        run_version=7,
        manifest_id="man-1",
        base_scene_revision_id="r1",
    )


def _env(run_id: str) -> AgentInputEnvelope:
    return AgentInputEnvelope(
        runtime_context=RuntimeContext(
            generation_run_id=run_id,
            agent_run_id="a1",
            agent_attempt_key="ak1",
            thread_id=run_id,
        ),
        scene_brief={"goal": "x"},
        context_manifest=[],
        draft_text="draft",
        accepted_text="accepted",
    )


def _identity(run_id: str, worker: str) -> RunIdentity:
    return RunIdentity(
        generation_run_id=run_id,
        agent_run_id="a1",
        agent_attempt_key="ak1",
        parent_generation_run_id=None,
        supersedes_run_id=None,
        parent_plan_revision_id=None,
    )


def _create_run(db, run_id: str) -> None:
    db.add(GenerationRun(id=run_id, project_id="p1", status="running"))
    db.flush()


def test_postgres_checkpoint_recovers_across_instances(db):
    """A checkpoint written to Postgres is reachable by a rebuilt graph instance."""
    run_id = f"rec-{uuid.uuid4().hex[:8]}"
    setup_checkpoint_tables(TEST_DATABASE_URL)
    cp1 = build_postgres_checkpointer(TEST_DATABASE_URL)
    graph1 = SceneGraph(HookRegistry(), AgentResultRouter(), checkpointer=cp1)
    result = graph1.invoke(_state(run_id), _env(run_id), thread_id=run_id)
    assert result["last_durable_node"] == "revision"
    snapshot1 = graph1.get_state(run_id)
    assert snapshot1.values["generation_run_id"] == run_id
    assert snapshot1.values["run_version"] == 7
    assert snapshot1.values["manifest_id"] == "man-1"
    assert snapshot1.values["base_scene_revision_id"] == "r1"
    assert snapshot1.values["last_durable_node"] == "revision"
    checkpoint_id1 = snapshot1.config["configurable"]["checkpoint_id"]
    cp1.close()  # simulate instance teardown / process restart

    # A newly built graph instance with a fresh connection resumes the same run.
    cp2 = build_postgres_checkpointer(TEST_DATABASE_URL)
    graph2 = SceneGraph(HookRegistry(), AgentResultRouter(), checkpointer=cp2)
    snapshot2 = graph2.get_state(run_id)
    assert snapshot2.values["generation_run_id"] == run_id
    assert snapshot2.values["run_version"] == snapshot1.values["run_version"]
    assert snapshot2.values["manifest_id"] == snapshot1.values["manifest_id"]
    assert snapshot2.values["base_scene_revision_id"] == snapshot1.values["base_scene_revision_id"]
    assert snapshot2.values["last_durable_node"] == snapshot1.values["last_durable_node"]
    assert snapshot2.config["configurable"]["checkpoint_id"] == checkpoint_id1
    cp2.close()


def test_old_worker_resume_rejected_after_postgres_takeover(db):
    """A stale worker cannot resume a Postgres-backed run after a takeover."""
    run_id = f"fence-{uuid.uuid4().hex[:8]}"
    _create_run(db, run_id)
    leases = LeaseRepository(db)
    old = leases.claim(_identity(run_id, "w1"), "w1")

    cp1 = build_postgres_checkpointer(TEST_DATABASE_URL)
    graph1 = SceneGraph(HookRegistry(), AgentResultRouter(), checkpointer=cp1)
    executor1 = RunExecutor(
        leases,
        graph1,
        _identity(run_id, "w1"),
    )
    executor1.execute(run_id, "w1", old["fencing_token"], old["lease_token"], _state(run_id), _env(run_id))
    checkpoint_id_before = graph1.get_state(run_id).config["configurable"]["checkpoint_id"]
    cp1.close()

    # A new worker takes over the run, incrementing the fencing token.
    new = leases.claim(_identity(run_id, "w2"), "w2")
    assert new["fencing_token"] == old["fencing_token"] + 1

    cp2 = build_postgres_checkpointer(TEST_DATABASE_URL)
    graph2 = SceneGraph(HookRegistry(), AgentResultRouter(), checkpointer=cp2)
    executor2 = RunExecutor(
        leases,
        graph2,
        _identity(run_id, "w2"),
    )
    # The old worker's resume is rejected and writes nothing.
    with pytest.raises(AppError) as exc:
        executor2.execute(
            run_id, "w1", old["fencing_token"], old["lease_token"], _state(run_id), _env(run_id)
        )
    assert exc.value.code == "RUN_LEASE_LOST"
    # The rejected write must not create a duplicate checkpoint.
    checkpoint_id_after = graph2.get_state(run_id).config["configurable"]["checkpoint_id"]
    assert checkpoint_id_after == checkpoint_id_before
    cp2.close()
