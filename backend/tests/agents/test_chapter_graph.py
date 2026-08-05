"""Task 4B 章节图测试：LangGraph 编排、interrupt/Router/checkpoint 恢复。"""

from __future__ import annotations

from typing import Any

from app.agents.chapter_graph import ChapterGraph
from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RuntimeContext


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
    envelope = _envelope("ch1")
    plan = graph.step(state, envelope, "chapter_planner")
    assert plan["last_durable_node"] == "chapter_planner"
    review = graph.step(state, envelope, "chapter_review")
    assert review["last_durable_node"] == "chapter_review"


def test_chapter_graph_resume_accept_returns_to_pending_node():
    """accept 后必须回到 pending_node 继续（规划 -> 审校），同一次 resume 进入审校。"""
    graph = ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
    )
    first = graph.invoke(_state(), _envelope("ch1", chapter_contract={}), thread_id="t-accept")
    assert first["run_status"] == "paused"
    assert first["pending_node"] == "chapter_planner"
    # accept 后回到 planner（pending_node），同一次 resume 就继续到审校及之后。
    result = graph.invoke(
        _state(),
        _envelope("ch1", chapter_contract={"pov": "p", "scene_keys": ["s1", "s2"]}),
        thread_id="t-accept",
        resume={"action": "accept"},
    )
    # 已离开 planner 暂停，进入审校分支（继续到聚合，未接聚合器时在聚合节点暂停）。
    assert result["last_durable_node"] in ("chapter_review", "chapter_aggregator")
    assert result["run_status"] == "paused"


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
