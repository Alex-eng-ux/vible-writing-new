"""Task 4B 章节图测试：LangGraph 编排、interrupt/Router/checkpoint 恢复。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.chapter_graph import ChapterGraph
from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.errors import AppError


def _envelope(chapter_id: str | None = "ch1", **overrides: object) -> AgentInputEnvelope:
    # 动态字段集合由调用方 overrides 控制，运行时由 pydantic 校验，
    # 故以 Any 字典构造输入信封（字段类型在 AgentInputEnvelope 上声明）。
    base: dict[str, Any] = {
        "runtime_context": RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="chapter",
            decision_target="plan",
            chapter_id=chapter_id,
        ),
        "chapter_contract": {"pov": "p", "scene_keys": ["s1", "s2"]},
    }
    base.update(overrides)
    return AgentInputEnvelope(**base)


def _state() -> dict:
    return {
        "generation_run_id": "g1",
        "run_version": 1,
        "project_id": "p1",
        "chapter_id": "ch1",
        "scene_ids": ["s1", "s2"],
        "run_status": "running",
        "last_durable_node": None,
        "pending_node": None,
        "clarification_questions": [],
        "scene_auto_revision_counts": {},
        "inheritance_map": {},
    }


def test_chapter_graph_compiles_and_runs_plan_review_aggregate():
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    result = graph.invoke(_state(), _envelope("ch1"), thread_id="t1")
    assert result["run_status"] in ("running", "paused")
    assert result["last_durable_node"] in ("chapter_planner", "chapter_review", "chapter_aggregator")


def test_chapter_review_run_aggregates_before_review():
    """场景队列完成后的章节运行必须先聚合，再调用章节审校 Agent。"""
    calls: list[str] = []

    class _Aggregator:
        def eligibility(self, *args: object, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(eligible=True, reason="", status="ready")

        def aggregate(self, *args: object, **kwargs: object) -> str:
            calls.append("aggregate")
            return "staged-chapter-revision"

    class _Planner:
        def run(self, envelope: AgentInputEnvelope) -> object:
            raise AssertionError("chapter review run must not invoke planner")

    class _Review:
        def run(self, envelope: AgentInputEnvelope) -> object:
            calls.append("review")
            return SimpleNamespace(
                status="ready",
                review_issues=[],
                overall_rating="pass",
                submitted=True,
                clarification_questions=[],
                model_dump=lambda: {
                    "status": "ready",
                    "review_issues": [],
                    "overall_rating": "pass",
                    "submitted": True,
                    "clarification_questions": [],
                },
            )

    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
        planner=_Planner(),
        review=_Review(),
        aggregator=_Aggregator(),
    )
    envelope = _envelope(
        "ch1",
        request_type="review",
        runtime_context=RuntimeContext(
            generation_run_id="g-review",
            agent_run_id="a-review",
            agent_attempt_key="k-review",
            thread_id="t-review",
            run_scope="chapter",
            decision_target="chapter",
            chapter_id="ch1",
        ),
    )
    result = graph.invoke(_state(), envelope, thread_id="t-review")

    assert calls == ["aggregate", "review"]
    assert result["last_durable_node"] == "chapter_review"


def test_chapter_graph_checkpoint_resume():
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    graph.invoke(_state(), _envelope("ch1"), thread_id="t-resume")
    snap = graph.get_state("t-resume")
    assert snap is not None


def test_chapter_graph_pauses_when_no_contract():
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    state = _state()
    result = graph.invoke(state, _envelope("ch1", chapter_contract={}), thread_id="t-pause")
    assert result["run_status"] == "paused"
    assert result["pending_node"] == "chapter_planner"


def test_chapter_graph_step_planner_and_review():
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    state = _state()
    planning_envelope = _envelope("ch1", request_type="new_chapter")
    plan = graph.step(state, planning_envelope, "chapter_planner")
    assert plan["last_durable_node"] == "chapter_planner"
    review_envelope = _envelope(
        "ch1",
        request_type="review",
        runtime_context=RuntimeContext(
            generation_run_id="g-review-step",
            agent_run_id="a-review-step",
            agent_attempt_key="k-review-step",
            thread_id="t-review-step",
            run_scope="chapter",
            decision_target="chapter",
            chapter_id="ch1",
        ),
    )
    review = graph.step(state, review_envelope, "chapter_review")
    assert review["last_durable_node"] == "chapter_review"


def test_new_chapter_cannot_directly_step_into_chapter_review():
    """普通 new_chapter 规划不得通过旧图适配器直接执行章节审校。"""
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    with pytest.raises(AppError, match="chapter review requires a chapter review run"):
        graph.step(_state(), _envelope("ch1"), "chapter_review")


def test_chapter_router_does_not_expose_legacy_downstream_nodes():
    """章节 Planner/Review 的路由结果交给 workflow controller，而不是旧静态下游。"""
    planner = AgentResultRouter().route(
        SimpleNamespace(status="ready"),
        "chapter_planner",
        "chapter_planner",
    )
    review = AgentResultRouter().route(
        SimpleNamespace(status="ready"),
        "chapter_review",
        "chapter_review",
    )
    assert planner.next_node != "chapter_review"
    assert review.next_node != "chapter_aggregator"


def test_chapter_graph_resume_accept_returns_to_pending_node():
    """accept 后必须回到 pending_node 继续（规划 -> 审校），同一次 resume 进入审校。"""
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    first = graph.invoke(_state(), _envelope("ch1", chapter_contract={}), thread_id="t-accept")
    assert first["run_status"] == "paused"
    assert first["pending_node"] == "chapter_planner"
    # accept 只结束本次候选规划图；场景队列由 workflow controller 接管。
    result = graph.invoke(
        _state(),
        _envelope("ch1", chapter_contract={"pov": "p", "scene_keys": ["s1", "s2"]}),
        thread_id="t-accept",
        resume={"action": "accept"},
    )
    assert result["last_durable_node"] == "chapter_planner"
    assert result["run_status"] == "running"
    assert result["pending_node"] is None


def test_chapter_graph_resume_feedback_carries_author_feedback_and_reexecutes():
    """feedback 必须携带 AuthorFeedback，写入 checkpoint 后重新执行原节点。"""
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    first = graph.invoke(_state(), _envelope("ch1", chapter_contract={}), thread_id="t-fb")
    assert first["pending_node"] == "chapter_planner"
    result = graph.invoke(
        _state(),
        _envelope("ch1", chapter_contract={}),
        thread_id="t-fb",
        resume={"action": "feedback", "author_feedback": {"text": "expand the scene", "target": "plan"}},
    )
    # 重新执行原节点（planner 仍因无契约暂停）。
    assert result["run_status"] == "paused"
    assert result["pending_node"] == "chapter_planner"
    snap = graph.get_state("t-fb")
    assert snap.values.get("author_feedback", {}).get("text") == "expand the scene"


def test_chapter_graph_resume_cancel_ends_run():
    """cancel 后运行结束为 cancelled。"""
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    first = graph.invoke(_state(), _envelope("ch1", chapter_contract={}), thread_id="t-cancel")
    assert first["pending_node"] == "chapter_planner"
    result = graph.invoke(_state(), _envelope("ch1", chapter_contract={}), thread_id="t-cancel", resume={"action": "cancel"})
    assert result["run_status"] == "cancelled"
