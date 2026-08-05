from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.schemas import (
    AgentInputEnvelope,
    ContinuityOutput,
    ReviewOutput,
    RevisionOutput,
    RouterOutcome,
    RuntimeContext,
    WritingOutput,
)


def _runtime() -> RuntimeContext:
    return RuntimeContext(
        generation_run_id="g1",
        agent_run_id="a1",
        agent_attempt_key="ak1",
        thread_id="g1",
    )


def test_writing_output_requires_mode_and_status():
    out = WritingOutput(status="ready", mode="draft", content="text")
    assert out.mode == "draft"
    assert out.content == "text"


def test_writing_output_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        WritingOutput(status="ready", mode="invalid", content="x")  # type: ignore[arg-type]


def test_writing_output_needs_clarification_requires_questions():
    out = WritingOutput(status="needs_clarification", mode="draft", clarification_questions=["q"])
    assert out.status == "needs_clarification"


def test_continuity_output_status_enum():
    out = ContinuityOutput(status="pass")
    assert out.status == "pass"


def test_review_output_status_enum():
    out = ReviewOutput(status="ready")
    assert out.status == "ready"


def test_revision_output_operation_format_fixed():
    out = RevisionOutput(status="ready", base_scene_revision_id="r1")
    assert out.operation_format == "semantic_text"


def test_envelope_runtime_context_required():
    env = AgentInputEnvelope(runtime_context=_runtime())
    assert env.runtime_context.generation_run_id == "g1"
    assert env.request_type == "continue"


def test_envelope_rejects_bad_request_type():
    with pytest.raises(ValidationError):
        AgentInputEnvelope(runtime_context=_runtime(), request_type="bogus")  # type: ignore[arg-type]


def test_router_outcome_status_enum():
    out = RouterOutcome(status="needs_clarification", pending_node="writing", clarification_questions=["q"])
    assert out.status == "needs_clarification"
    assert out.pending_node == "writing"
