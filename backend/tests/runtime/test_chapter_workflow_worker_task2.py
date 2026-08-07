from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import ChapterPlanOutput
from app.db.models import (
    ChapterPlanDiscussionMessage,
    ChapterPlanProposal,
    ChapterPlanQuestion,
    ChapterPlanRevision,
    ChapterPlanSceneLink,
    GenerationRun,
    RunOutboxRecord,
)
from app.domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter,
    create_chapter_plan_revision,
)
from app.domain.resources import create_project, create_volume
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_worker import RunWorker


class _PlannerGraph:
    def __init__(self) -> None:
        self.envelope = None
        self.calls = 0

    @property
    def registry(self):
        return HookRegistry()

    def invoke(self, state, envelope, thread_id, resume=None):
        self.calls += 1
        self.envelope = envelope
        return {
            "run_status": "paused",
            "pending_node": "chapter_planner",
            "clarification_questions": [],
            "last_durable_node": "chapter_planner",
            "planner_output": ChapterPlanOutput(
                status="ready",
                chapter_contract={"scene_keys": ["s1"]},
                scene_contracts=[{"client_key": "s1", "title": "S1", "scene_brief": {}}],
                reason="candidate",
            ).model_dump(),
        }


def _chapter(db):
    project = create_project(db, "P", "g", "r", "s", {"actor_id": "a", "idempotency_key": "p-task2"})
    volume = create_volume(db, project.id, "V", "g", "m", "r", {"actor_id": "a", "idempotency_key": "v-task2"})
    return create_chapter(
        db,
        volume.id,
        "C",
        "pov",
        {"text": "主角必须在钟楼做出选择", "goal": "choice"},
        {"actor_id": "a", "idempotency_key": "c-task2"},
    )


def test_worker_rebuilds_planner_context_and_persists_candidate(db):
    chapter = _chapter(db)
    lineage = "lineage-task2"
    db.add(
        ChapterPlanDiscussionMessage(
            chapter_id=chapter.id,
            planning_lineage_id=lineage,
            message_sequence=1,
            role="author",
            kind="intent",
            text="不要安排死亡结局",
        )
    )
    db.add(
        ChapterPlanQuestion(
            chapter_id=chapter.id,
            planning_lineage_id=lineage,
            text="是否保留钟楼？",
            impact="scene",
            status="pending",
        )
    )
    db.add(
        ChapterPlanProposal(
            chapter_id=chapter.id,
            planning_lineage_id=lineage,
            field_path="tone",
            value={"value": "紧张"},
            source="ai",
            status="pending",
        )
    )
    run = GenerationRun(
        id="run-task2-planner",
        project_id=chapter.volume_id,
        chapter_id=chapter.id,
        request_type="new_chapter",
        decision_target="plan",
        status="queued",
        normalized_input={"run_scope": "chapter", "request_type": "new_chapter", "decision_target": "plan", "chapter_intent": {"text": "输入意图"}},
        parent_plan_revision_id=lineage,
    )
    db.add(run)
    db.flush()
    PostgresRunEventStore(db).emit(run.id, "run_queued", {}, fencing_token=0)
    db.commit()

    graph = _PlannerGraph()
    worker = RunWorker(sessionmaker(bind=db.bind, expire_on_commit=False), actor_id="worker-task2", graph_builder=lambda run, session: graph)
    assert worker.tick() == 1

    assert graph.envelope.chapter_intent == {"text": "输入意图"}
    assert graph.envelope.plan_discussion[0]["text"] == "不要安排死亡结局"
    assert graph.envelope.pending_plan_questions[0]["text"] == "是否保留钟楼？"
    assert graph.envelope.pending_plan_proposals[0]["field_path"] == "tone"
    candidate = db.execute(select(ChapterPlanRevision).where(ChapterPlanRevision.source_run_id == run.id)).scalar_one()
    assert candidate.status == "pending"


def test_new_chapter_planner_stops_before_chapter_review(db):
    """首次规划 ready 后只等待作者接受，不沿旧章节图继续审校/聚合。"""
    chapter = _chapter(db)
    run = GenerationRun(
        id="run-task2-planner-routing",
        project_id=chapter.volume_id,
        chapter_id=chapter.id,
        request_type="new_chapter",
        decision_target="plan",
        status="queued",
        normalized_input={
            "run_scope": "chapter",
            "request_type": "new_chapter",
            "decision_target": "plan",
            "chapter_intent": {"text": "输入意图"},
        },
    )
    db.add(run)
    db.flush()
    PostgresRunEventStore(db).emit(run.id, "run_queued", {}, fencing_token=0)
    db.commit()

    class _ReadyPlannerGraph(_PlannerGraph):
        def invoke(self, state, envelope, thread_id, resume=None):
            self.calls += 1
            self.envelope = envelope
            from app.agents.chapter_graph import ChapterGraph

            return ChapterGraph(
                HookRegistry(),
                AgentResultRouter(),
            ).invoke(state, envelope, thread_id)

    worker = RunWorker(
        sessionmaker(bind=db.bind, expire_on_commit=False),
        actor_id="worker-task2",
        graph_builder=lambda run, session: _ReadyPlannerGraph(),
    )
    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run.id)
    assert row.status == "waiting_feedback"
    assert row.pending_node == "chapter_planner"


def test_worker_consumes_accepted_plan_outbox_and_recovers_one_scene_run(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1", "s2"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-task2"},
    )
    plan.scene_briefs = [
        {"client_key": "s1", "title": "S1", "scene_brief": {}},
        {"client_key": "s2", "title": "S2", "scene_brief": {}},
    ]
    plan.chapter_contract = {"scene_keys": ["s1", "s2"], "scenes": plan.scene_briefs}
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, {"actor_id": "a", "idempotency_key": "accept-task2"})
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-task2")
    assert worker.tick() == 1
    runs = db.execute(select(GenerationRun).where(GenerationRun.chapter_id == chapter.id, GenerationRun.scene_id.is_not(None))).scalars().all()
    assert len(runs) == 1
    assert runs[0].plan_revision_id == plan.id
    first_scene = db.execute(select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan.id).order_by(ChapterPlanSceneLink.sort_order)).scalars().first()
    assert runs[0].scene_id == first_scene.scene_id

    assert worker.tick() == 0
    runs = db.execute(select(GenerationRun).where(GenerationRun.chapter_id == chapter.id, GenerationRun.scene_id.is_not(None))).scalars().all()
    assert len(runs) == 1

    # 模拟 outbox 重放与 Worker 重启：重复消费仍只保留同一个场景运行。
    outbox = db.execute(select(RunOutboxRecord).where(RunOutboxRecord.resource_id == plan.id)).scalar_one()
    outbox.delivery_status = "pending"
    db.commit()
    RunWorker(factory, actor_id="worker-task2-restarted").tick()
    runs = db.execute(select(GenerationRun).where(GenerationRun.chapter_id == chapter.id, GenerationRun.scene_id.is_not(None))).scalars().all()
    assert len(runs) == 1
