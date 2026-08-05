from __future__ import annotations

import pytest

from app.agents.hook_registry import HookRegistry
from app.agents.hooks import (
    CommitGuardHook,
    ErrorHook,
    FactExtractionHook,
    LifecycleHook,
    SchemaHook,
)
from app.agents.nodes import AgentCallable
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RouterOutcome, WritingOutput
from app.agents.writing_agent import WritingAgent
from app.errors import AppError


class _Recorder(LifecycleHook):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def before(self, agent_type: str, envelope: AgentInputEnvelope) -> None:
        self.calls.append(f"before:{agent_type}")

    def after(self, agent_type: str, output, outcome: RouterOutcome) -> None:
        self.calls.append(f"after:{agent_type}")


def test_hook_order_before_after():
    from app.agents.schemas import RuntimeContext

    registry = HookRegistry()
    recorder = _Recorder()
    registry.register("writing", recorder)
    callable_ = AgentCallable("writing", WritingAgent(), registry, AgentResultRouter())
    env = AgentInputEnvelope(
        runtime_context=RuntimeContext(
            generation_run_id="g1", agent_run_id="a1", agent_attempt_key="ak1", thread_id="g1"
        ),
        scene_brief={"goal": "x"},
    )
    callable_({}, env)
    assert recorder.calls == ["before:writing", "after:writing"]


def test_commit_guard_hook_requires_lease_on_mismatch(db):
    from app.domain.commit_guard import CommitGuard

    guard = CommitGuardHook(CommitGuard(db))
    # No lease + no write fence on a run-rooted operation: should fail closed.
    with pytest.raises(AppError):
        guard.validate(
            operation="commit_scene_draft",
            actor_id="a",
            base_revision_id=None,
            idempotency_key="k",
            source_refs=[],
            write_fence=None,
            lease_context=None,
        )


def test_schema_hook_blocks_bad_needs_clarification():
    hook = SchemaHook()
    with pytest.raises(AppError):
        hook.validate(WritingOutput(status="needs_clarification", mode="draft", clarification_questions=[]))
    # Passing case.
    hook.validate(WritingOutput(status="needs_clarification", mode="draft", clarification_questions=["q"]))


def test_fact_extraction_hook_extracts_candidates():
    hook = FactExtractionHook()
    out = WritingOutput(
        status="ready",
        mode="draft",
        content="x",
        candidate_facts=[
            {
                "candidate_type": "fact",
                "local_key": "f1",
                "claim": "c",
                "status": "candidate",
                "scope": "scene",
                "evidence_refs": [],
            }
        ],
    )
    facts = hook.extract(out)
    assert len(facts) == 1
    assert facts[0]["local_key"] == "f1"


def test_error_hook_rethrows_app_errors():
    hook = ErrorHook()
    with pytest.raises(AppError):
        hook.handle(AppError("RUN_LEASE_LOST"))


def test_error_hook_wraps_unknown_errors():
    hook = ErrorHook()
    with pytest.raises(AppError) as exc:
        hook.handle(RuntimeError("boom"))
    assert exc.value.code == "INTERNAL_ERROR"
