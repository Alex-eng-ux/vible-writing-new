"""Task 4C Canon 图测试：分支路由、候选持久化、作者确认恢复与正式提交。"""

from __future__ import annotations

from app.agents.canon_graph import CanonGraph
from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.db.models import CanonFact, FactCandidate, GenerationRun, Volume
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    commit_chapter_version,
    create_chapter,
    create_chapter_plan_revision,
)


def _resource_ctx():
    return {"actor_id": "author-1", "idempotency_key": "key-1"}


def _author_ctx():
    return {
        "generation_run_id": None,
        "write_fence": None,
        "manual_command_id": "manual-1",
        "source": "author",
        "actor_id": "author-1",
        "idempotency_key": "key-1",
        "expected_run_version": None,
    }


def _make_run(db, project_id, run_id="g1"):
    """创建目标 GenerationRun，供作者确认提交领取 API command fence。"""
    run = GenerationRun(id=run_id, project_id=project_id, status="running")
    db.add(run)
    db.flush()
    return run


def _make_chapter(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _author_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _author_ctx())
    chapter.chapter_sync_status = "in_sync"
    chapter.entry_handoff_status = "in_sync"
    db.flush()
    return chapter


def _accept_chapter(db, chapter):
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", _author_ctx())
    commit_chapter_version(db, rev.id, _author_ctx())
    return rev


def _envelope(project_id, chapter_id, accepted_chapter_revision_id, canon_scope="chapter", **overrides) -> AgentInputEnvelope:
    base = {
        "runtime_context": RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="chapter",
            decision_target="canon",
            chapter_id=chapter_id,
        ),
        "project": {"id": project_id},
        "canon_scope": canon_scope,
        "accepted_chapter_revision_id": accepted_chapter_revision_id,
        "snapshot_before": {
            "candidates": [
                {"candidate_type": "fact", "local_key": "f1", "claim": "主角是侦探"},
                {
                    "candidate_type": "timeline_event",
                    "local_key": "e1",
                    "claim": "第 3 章主角到达现场",
                },
                {
                    "candidate_type": "plot_thread",
                    "local_key": "p1",
                    "claim": "开启复仇线",
                },
            ]
        },
    }
    base.update(overrides)
    return AgentInputEnvelope(**base)


def _state() -> dict:
    return {
        "generation_run_id": "g1",
        "run_version": 1,
        "project_id": "p1",
        "chapter_id": "ch1",
        "scene_ids": [],
        "run_status": "running",
        "last_durable_node": None,
        "pending_node": None,
        "clarification_questions": [],
        "scene_auto_revision_counts": {},
        "inheritance_map": {},
    }


def test_canon_graph_runs_to_confirmation_and_persists_candidates(db, volume):
    """CanonAgent 节点只持久化候选，不写正式 Canon；随后等待作者确认。"""
    graph = CanonGraph(session=db, registry=HookRegistry(), router=AgentResultRouter())
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = db.get(Volume, volume).project_id
    envelope = _envelope(project_id, chapter.id, rev.id)
    result = graph.invoke(_state(), envelope, thread_id="t1")
    assert result["run_status"] == "paused"
    assert result["pending_node"] == "canon_confirmation"
    # 三类候选已持久化（分别落在三个候选表），正式 Canon 尚未写入。
    from app.db.models import PlotThreadUpdate, TimelineEventCandidate

    candidate_models = [FactCandidate, TimelineEventCandidate, PlotThreadUpdate]
    total = sum(db.query(m).filter(m.project_id == project_id).count() for m in candidate_models)
    assert total == 3
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0


def test_canon_graph_confirm_resume_commits_official_canon(db, volume):
    """作者 confirm 恢复后进入提交节点：以 author 身份（manual_command_id +
    API command fence）章节级确认生成正式 CanonFact。"""
    graph = CanonGraph(session=db, registry=HookRegistry(), router=AgentResultRouter())
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = db.get(Volume, volume).project_id
    _make_run(db, project_id)
    envelope = _envelope(project_id, chapter.id, rev.id)
    graph.invoke(_state(), envelope, thread_id="t-confirm")
    snap = graph.get_state("t-confirm")
    assert snap.values.get("pending_node") == "canon_confirmation"
    cand = db.query(FactCandidate).filter(
        FactCandidate.project_id == project_id, FactCandidate.candidate_type == "fact"
    ).first()
    assert cand is not None
    result = graph.invoke(
        _state(),
        envelope,
        thread_id="t-confirm",
        resume={
            "action": "confirm",
            "manual_command_id": "manual-c1",
            "idempotency_key": "cmd-key-1",
            "expected_run_version": 0,
            "candidate_decisions": [
                {
                    "candidate_id": cand.id,
                    "candidate_type": "fact",
                    "decision": "accepted",
                    "local_key": "f1",
                }
            ],
        },
    )
    assert result["run_status"] == "accepted"
    # 正式 CanonFact 生成，且候选进入 accepted。
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 1
    db.refresh(cand)
    assert cand.status == "accepted"
    # 正式提交以作者命令身份完成：运行写入所有者切换为 api_command + 该命令。
    run = db.get(GenerationRun, "g1")
    assert run.write_owner_kind == "api_command"
    assert run.write_owner_id == "manual-c1"


def test_canon_graph_confirm_three_types_commit_structured(db, volume):
    """作者 confirm 后三类候选按类型写入正式结构（字段级断言）。"""
    from app.db.models import PlotThread, TimelineEvent

    graph = CanonGraph(session=db, registry=HookRegistry(), router=AgentResultRouter())
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = db.get(Volume, volume).project_id
    _make_run(db, project_id)
    hints = [
        {"candidate_type": "fact", "local_key": "f1", "claim": "主角是侦探"},
        {
            "candidate_type": "timeline_event",
            "local_key": "e1",
            "claim": "第 3 章主角到达现场",
            "effective_story_time": {"value": "第3章", "precision": "exact"},
            "entities": ["主角", "现场"],
        },
        {
            "candidate_type": "plot_thread",
            "local_key": "p1",
            "claim": "开启复仇线",
            "thread_state": "advanced",
            "planned_resolution": "第5章",
        },
    ]
    envelope = _envelope(project_id, chapter.id, rev.id, snapshot_before={"candidates": hints})
    graph.invoke(_state(), envelope, thread_id="t-types")
    decisions = []
    from app.db.models import PlotThreadUpdate, TimelineEventCandidate

    for model, ctype, decision in (
        (FactCandidate, "fact", "accepted"),
        (TimelineEventCandidate, "timeline_event", "accepted"),
        (PlotThreadUpdate, "plot_thread", "accepted"),
    ):
        row = db.query(model).filter(model.project_id == project_id, model.candidate_type == ctype).first()
        decisions.append(
            {"candidate_id": row.id, "candidate_type": ctype, "decision": decision, "local_key": row.local_key}
        )
    result = graph.invoke(
        _state(),
        envelope,
        thread_id="t-types",
        resume={
            "action": "confirm",
            "manual_command_id": "manual-types",
            "idempotency_key": "cmd-types",
            "expected_run_version": 0,
            "candidate_decisions": decisions,
        },
    )
    assert result["run_status"] == "accepted"
    fact = db.query(CanonFact).filter(CanonFact.project_id == project_id).one()
    assert fact.fact_text == "主角是侦探"
    event = db.query(TimelineEvent).filter(TimelineEvent.project_id == project_id).one()
    assert event.event_text == "第 3 章主角到达现场"
    assert event.story_time == {"value": "第3章", "precision": "exact"}
    assert event.entities == ["主角", "现场"]
    thread = db.query(PlotThread).filter(PlotThread.project_id == project_id).one()
    assert thread.thread_text == "开启复仇线"
    assert thread.state == "advanced"
    assert thread.planned_resolution == "第5章"


def test_canon_graph_cancel_ends_run_without_official_write(db, volume):
    """cancel 结束运行，不写入正式 Canon。"""
    graph = CanonGraph(session=db, registry=HookRegistry(), router=AgentResultRouter())
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = db.get(Volume, volume).project_id
    envelope = _envelope(project_id, chapter.id, rev.id)
    graph.invoke(_state(), envelope, thread_id="t-cancel")
    result = graph.invoke(_state(), envelope, thread_id="t-cancel", resume={"action": "cancel"})
    assert result["run_status"] == "cancelled"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0


def test_canon_graph_reject_resume_marks_candidate_rejected(db, volume):
    """reject 恢复只更新候选与决策记录，不生成正式 Canon。"""
    graph = CanonGraph(session=db, registry=HookRegistry(), router=AgentResultRouter())
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = db.get(Volume, volume).project_id
    _make_run(db, project_id)
    envelope = _envelope(project_id, chapter.id, rev.id)
    graph.invoke(_state(), envelope, thread_id="t-reject")
    cand = db.query(FactCandidate).filter(
        FactCandidate.project_id == project_id, FactCandidate.candidate_type == "fact"
    ).first()
    result = graph.invoke(
        _state(),
        envelope,
        thread_id="t-reject",
        resume={
            "action": "reject",
            "manual_command_id": "manual-r1",
            "idempotency_key": "cmd-key-r",
            "expected_run_version": 0,
            "candidate_decisions": [
                {
                    "candidate_id": cand.id,
                    "candidate_type": "fact",
                    "decision": "rejected",
                    "local_key": "f1",
                }
            ],
        },
    )
    assert result["run_status"] == "accepted"
    db.refresh(cand)
    assert cand.status == "rejected"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
