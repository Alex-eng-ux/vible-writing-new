from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph import SceneGraph
from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.review_agent import ReviewAgent
from app.agents.schemas import (
    AgentInputEnvelope,
    ContinuityOutput,
    ReviewIssue,
    ReviewOutput,
    RuntimeContext,
)
from app.agents.state import ChapterRunState


def _env(thread_id="g1", **overrides: object) -> AgentInputEnvelope:
    rt = RuntimeContext(
        generation_run_id=thread_id,
        agent_run_id="a1",
        agent_attempt_key="ak1",
        thread_id=thread_id,
    )
    # 动态字段集合由调用方 overrides 控制，运行时由 pydantic 校验。
    base: dict[str, Any] = dict(
        runtime_context=rt,
        scene_brief={"goal": "x"},
        context_manifest=[],
        draft_text="draft",
        accepted_text="accepted",
        base_scene_revision_id="r1",
    )
    base.update(overrides)
    return AgentInputEnvelope(**base)


def _state(thread_id="g1") -> ChapterRunState:
    return ChapterRunState(generation_run_id=thread_id, run_version=1)


def test_graph_is_compiled_langgraph():
    """The graph must be a real compiled LangGraph StateGraph."""
    graph = SceneGraph(HookRegistry(), AgentResultRouter())
    assert graph._compiled is not None
    assert type(graph._compiled).__name__ == "CompiledStateGraph"


def test_graph_invoke_writing_to_continuity():
    """Writing node runs and routes through the graph; with a full envelope it completes."""
    graph = SceneGraph(HookRegistry(), AgentResultRouter())
    result = graph.invoke(_state(), _env(), thread_id="g1")
    assert result["last_durable_node"] == "revision"
    # Revision has no author feedback to apply -> pauses for the author.
    assert result["run_status"] == "paused"


def test_graph_checkpoint_binds_to_thread_id():
    """Checkpoint is bound to the run via thread_id (generation_run_id)."""
    graph = SceneGraph(HookRegistry(), AgentResultRouter())
    graph.invoke(_state(), _env(), thread_id="g1")
    snapshot = graph.get_state("g1")
    assert snapshot.values["generation_run_id"] == "g1"
    assert snapshot.values["last_durable_node"] == "revision"


def test_graph_review_does_not_call_writing():
    """Review branch must never invoke WritingAgent on the compiled graph."""
    calls = {"writing": 0}

    class _ReviewAgent(ReviewAgent):
        def run(self, envelope):
            calls["writing"] += 0
            return ReviewOutput(status="ready", review_issues=[], overall_rating="pass")

    graph = SceneGraph(HookRegistry(), AgentResultRouter(), review=_ReviewAgent())
    result = graph.invoke(_state(), _env(), thread_id="g1")
    # A clean review run still never calls WritingAgent a second time.
    assert calls["writing"] == 0
    assert result["last_durable_node"] == "revision"


def test_graph_review_high_risk_goes_to_feedback():
    class _ReviewAgent(ReviewAgent):
        def run(self, envelope):
            return ReviewOutput(
                status="ready",
                review_issues=[
                    ReviewIssue(
                        local_key="r1",
                        issue_type="rule",
                        severity="blocking",
                        problem="blocking issue",
                    )
                ],
            )

    graph = SceneGraph(HookRegistry(), AgentResultRouter(), review=_ReviewAgent())
    result = graph.invoke(_state(), _env(), thread_id="g1")
    # Blocking review issue pauses for author feedback; pending_node is set.
    assert result["pending_node"] is not None
    assert result["run_status"] == "paused"


def test_graph_clarification_does_not_continue():
    """Empty scene_brief makes WritingAgent return needs_clarification; graph pauses."""
    graph = SceneGraph(HookRegistry(), AgentResultRouter())
    env = AgentInputEnvelope(
        runtime_context=RuntimeContext(
            generation_run_id="g1", agent_run_id="a1", agent_attempt_key="ak1", thread_id="g1"
        ),
        scene_brief={},
        context_pack=[],
    )
    result = graph.invoke(_state(), env, thread_id="g1")
    assert result["pending_node"] == "writing"
    assert result["run_status"] == "paused"
    assert result["clarification_questions"]


def test_graph_continuity_needs_author_confirmation_pauses():
    class _ContinuityAgent:
        def run(self, envelope):
            return ContinuityOutput(
                status="needs_author_confirmation",
                clarification_questions=["confirm timeline"],
            )

    graph = SceneGraph(HookRegistry(), AgentResultRouter(), continuity=_ContinuityAgent())
    result = graph.invoke(_state(), _env(), thread_id="g1")
    assert result["pending_node"] == "continuity"
    assert result["run_status"] == "paused"


def test_graph_resume_from_checkpoint_accept():
    """A paused run resumes from its checkpoint and continues after author accept."""
    class _ContinuityAgent:
        def run(self, envelope):
            return ContinuityOutput(
                status="needs_author_confirmation",
                clarification_questions=["confirm timeline"],
            )

    graph = SceneGraph(HookRegistry(), AgentResultRouter(), continuity=_ContinuityAgent())
    first = graph.invoke(_state(), _env(), thread_id="g1")
    assert first["run_status"] == "paused"
    assert first["pending_node"] == "continuity"
    # Resume accepting the pending decision.
    result = graph.invoke(_state(), _env(), thread_id="g1", resume={"action": "accept"})
    assert result["run_status"] == "running"
    assert result["pending_node"] is None


def test_graph_unknown_node_raises():
    graph = SceneGraph(HookRegistry(), AgentResultRouter())
    with pytest.raises(Exception):
        graph.step(_state(), _env(), "chapter_planner")
