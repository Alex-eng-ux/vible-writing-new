"""Task 4C CanonAgent 测试：三类候选提取、作用域与来源契约。"""

from __future__ import annotations

from app.agents.canon_agent import CanonAgent
from app.agents.schemas import AgentInputEnvelope, RuntimeContext


def _envelope(canon_scope="chapter", accepted_chapter_revision_id="rev-1", **overrides) -> AgentInputEnvelope:
    base = {
        "runtime_context": RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="chapter",
            decision_target="canon",
            chapter_id="ch1",
            scene_id="sc1",
        ),
        "canon_scope": canon_scope,
        "accepted_chapter_revision_id": accepted_chapter_revision_id,
        "snapshot_before": {},
    }
    base.update(overrides)
    return AgentInputEnvelope(**base)


def test_canon_agent_requires_canon_scope():
    """无 canon_scope 时必须返回 needs_clarification。"""
    out = CanonAgent().run(_envelope(canon_scope=None))
    assert out.status == "needs_clarification"
    assert out.clarification_questions


def test_canon_agent_chapter_requires_accepted_chapter_revision():
    """章节级候选必须绑定 accepted_chapter_revision_id，否则澄清。"""
    out = CanonAgent().run(_envelope(accepted_chapter_revision_id=None))
    assert out.status == "needs_clarification"
    assert out.clarification_questions


def test_canon_agent_scene_requires_accepted_scene_revision_and_scene_id():
    """场景级候选必须绑定 accepted_scene_revision_id 与 scene_id，否则澄清。"""
    out = CanonAgent().run(
        _envelope(canon_scope="scene", accepted_chapter_revision_id=None, accepted_scene_revision_id=None)
    )
    assert out.status == "needs_clarification"
    assert out.clarification_questions


def test_canon_agent_outputs_three_types_chapter_scope():
    """章节级作用域输出三类候选，scope 全部为 chapter，且不生成正式 ID。"""
    hints = [
        {"candidate_type": "fact", "local_key": "f1", "claim": "主角是侦探"},
        {"candidate_type": "timeline_event", "local_key": "e1", "claim": "第 3 章主角到达现场"},
        {"candidate_type": "plot_thread", "local_key": "p1", "claim": "开启复仇线"},
    ]
    out = CanonAgent().run(
        _envelope(
            canon_scope="chapter",
            snapshot_before={"candidates": hints},
        )
    )
    assert out.status == "ready"
    assert len(out.fact_candidates) == 1
    assert len(out.timeline_event_candidates) == 1
    assert len(out.plot_thread_updates) == 1
    for cand in (*out.fact_candidates, *out.timeline_event_candidates, *out.plot_thread_updates):
        assert cand.scope == "chapter"
        assert cand.candidate_id is None
        assert cand.status == "pending_author_confirmation"


def test_canon_agent_scene_scope_candidates_are_scene_scoped():
    """场景级局部候选 scope 必须为 scene，且不得声明为全局已确认。"""
    hints = [
        {"candidate_type": "fact", "local_key": "f1", "claim": "房间里的线索"},
    ]
    out = CanonAgent().run(
        _envelope(
            canon_scope="scene",
            accepted_chapter_revision_id=None,
            accepted_scene_revision_id="rev-sc",
            snapshot_before={"candidates": hints},
        )
    )
    assert out.status == "ready"
    assert len(out.fact_candidates) == 1
    cand = out.fact_candidates[0]
    assert cand.scope == "scene"
    assert cand.source.scene_id == "sc1"


def test_canon_agent_derives_fact_from_accepted_text_when_no_hints():
    """无预置候选时，仅从已接受正文提取候选（不读未接受草稿）。"""
    out = CanonAgent().run(
        _envelope(
            canon_scope="chapter",
            accepted_text="主角在第一章醒来，发现自己失去了记忆。",
        )
    )
    assert out.status == "ready"
    assert len(out.fact_candidates) == 1
    assert out.fact_candidates[0].candidate_type == "fact"
