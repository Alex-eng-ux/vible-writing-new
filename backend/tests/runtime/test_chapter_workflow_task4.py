from __future__ import annotations

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.api.schemas import DecisionRequest, RunCreateRequest
from app.db.models import ChapterPlanSceneLink, ChapterRevision, GenerationRun, Scene, SceneRevision
from app.domain.chapters import (
    accept_chapter_plan_revision,
    chapter_workflow_read,
    create_chapter,
    create_chapter_plan_revision,
)
from app.domain.resources import create_project, create_volume
from app.errors import AppError
from app.runtime.run_worker import RunWorker
from app.services.generation_runs import start_generation_run, submit_run_decision


def _chapter(db):
    project = create_project(db, "P-task4", "g", "r", "s", {"actor_id": "a", "idempotency_key": "p-task4"})
    volume = create_volume(db, project.id, "V-task4", "g", "m", "r", {"actor_id": "a", "idempotency_key": "v-task4"})
    return create_chapter(
        db,
        volume.id,
        "C-task4",
        "pov",
        {"text": "继续推进场景", "goal": "finish"},
        {"actor_id": "a", "idempotency_key": "c-task4"},
    )


def test_scene_queue_advances_in_plan_order_after_current_scene_acceptance(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1", "s2"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-task4"},
    )
    plan.scene_briefs = [
        {"client_key": "s1", "title": "S1", "scene_brief": {}},
        {"client_key": "s2", "title": "S2", "scene_brief": {}},
    ]
    plan.chapter_contract = {"scene_keys": ["s1", "s2"], "scenes": plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "accept-task4"},
    )
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-task4")
    worker.tick()
    links = db.execute(
        select(ChapterPlanSceneLink)
        .where(ChapterPlanSceneLink.plan_revision_id == plan.id)
        .order_by(ChapterPlanSceneLink.sort_order)
    ).scalars().all()
    first_run = db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[0].scene_id)
    ).scalar_one()
    assert first_run.status in {"waiting_feedback", "pending_clarification"}
    assert first_run.plan_revision_id == plan.id
    assert first_run.normalized_input["base_scene_revision_id"] is None

    accepted_revision = SceneRevision(
        scene_id=links[0].scene_id,
        content="accepted scene 1",
        content_hash="a" * 64,
        reason="author acceptance",
        source_ref="task4-test",
        status="accepted",
    )
    db.add(accepted_revision)
    db.flush()
    db.execute(
        update(Scene)
        .where(Scene.id == links[0].scene_id)
        .values(accepted_scene_revision_id=accepted_revision.id)
    )
    first_run.status = "accepted"

    second_accepted_revision = SceneRevision(
        scene_id=links[1].scene_id,
        content="accepted scene 2 baseline",
        content_hash="b" * 64,
        reason="existing baseline",
        source_ref="task4-test",
        status="accepted",
    )
    db.add(second_accepted_revision)
    db.flush()
    db.execute(
        update(Scene)
        .where(Scene.id == links[1].scene_id)
        .values(accepted_scene_revision_id=second_accepted_revision.id)
    )
    db.commit()

    worker.tick()
    second_run = db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[1].scene_id)
    ).scalar_one()
    assert second_run.plan_revision_id == plan.id
    assert second_run.normalized_input["base_scene_revision_id"] == second_accepted_revision.id

    # Replaying the accepted-plan outbox remains idempotent.
    worker.tick()
    assert db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[1].scene_id)
    ).scalars().all().__len__() == 1


def test_worker_enqueues_chapter_review_after_all_planned_scenes_are_accepted(db):
    """场景队列完成后只进入章节审校运行，不重新走 Planner 直连路径。"""
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-task5-review"},
    )
    plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    plan.chapter_contract = {"scene_keys": ["s1"], "scenes": plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "accept-task5-review"},
    )
    db.commit()

    link = db.execute(
        select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan.id)
    ).scalar_one()
    scene = db.get(Scene, link.scene_id)
    accepted_scene_revision = SceneRevision(
        scene_id=scene.id,
        content="accepted scene",
        content_hash="e" * 64,
        reason="fixture",
        source_ref="task5-review",
        status="accepted",
    )
    db.add(accepted_scene_revision)
    db.flush()
    scene.accepted_scene_revision_id = accepted_scene_revision.id
    scene_run = GenerationRun(
        id="scene-run-task5-review",
        project_id=chapter.volume_id,
        chapter_id=chapter.id,
        scene_id=scene.id,
        plan_revision_id=plan.id,
        request_type="continue",
        decision_target="scene",
        status="accepted",
        normalized_input={"run_scope": "scene", "plan_revision_id": plan.id},
    )
    db.add(scene_run)
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-task5-review")
    with factory() as session:
        assert worker._plan_scenes_are_accepted(session, chapter.id, plan.id)
    assert worker._recover_accepted_plan_scene_queues() == 0

    chapter_runs = db.execute(
        select(GenerationRun)
        .where(
            GenerationRun.chapter_id == chapter.id,
            GenerationRun.scene_id.is_(None),
        )
    ).scalars().all()
    assert len(chapter_runs) == 1
    assert chapter_runs[0].request_type == "review"
    assert chapter_runs[0].decision_target == "chapter"
    assert chapter_runs[0].plan_revision_id == plan.id

    # 计划 outbox/Worker 重放不能创建第二个章节审校运行。
    assert worker._recover_accepted_plan_scene_queues() == 0
    assert db.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter.id,
            GenerationRun.scene_id.is_(None),
            GenerationRun.plan_revision_id == plan.id,
        )
    ).scalars().all().__len__() == 1


def test_worker_persists_staged_chapter_review_output_before_acceptance(db):
    """章节审校 run 由 Worker 执行后生成 staged 版本并停在章节接受前。"""
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-task5-worker"},
    )
    plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    plan.chapter_contract = {"scene_keys": ["s1"], "scenes": plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "accept-task5-worker"},
    )
    db.commit()

    link = db.execute(
        select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan.id)
    ).scalar_one()
    scene = db.get(Scene, link.scene_id)
    accepted_scene_revision = SceneRevision(
        scene_id=scene.id,
        content="accepted scene",
        content_hash="f" * 64,
        reason="fixture",
        source_ref="task5-worker",
        status="accepted",
    )
    db.add(accepted_scene_revision)
    db.flush()
    scene.accepted_scene_revision_id = accepted_scene_revision.id
    db.add(
        GenerationRun(
            id="scene-run-task5-worker",
            project_id=chapter.volume_id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            plan_revision_id=plan.id,
            request_type="continue",
            decision_target="scene",
            status="accepted",
            normalized_input={"run_scope": "scene", "plan_revision_id": plan.id},
        )
    )
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-task5-worker")
    assert worker.tick() == 1

    db.expire_all()
    review_run = db.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter.id,
            GenerationRun.scene_id.is_(None),
            GenerationRun.plan_revision_id == plan.id,
        )
    ).scalar_one()
    assert review_run.request_type == "review"
    assert review_run.status == "waiting_feedback"
    assert review_run.pending_node == "chapter_review"

    revision = db.execute(
        select(ChapterRevision).where(ChapterRevision.chapter_id == chapter.id)
    ).scalar_one()
    assert revision.status == "staged"
    assert revision.review_run_id == review_run.id
    assert revision.review_summary["overall_rating"] == "pass"
    assert chapter.accepted_chapter_revision_id is None


def test_manual_run_cannot_skip_unaccepted_previous_scene(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1", "s2"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-task4-skip"},
    )
    plan.scene_briefs = [
        {"client_key": "s1", "title": "S1", "scene_brief": {}},
        {"client_key": "s2", "title": "S2", "scene_brief": {}},
    ]
    plan.chapter_contract = {"scene_keys": ["s1", "s2"], "scenes": plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "accept-task4-skip"},
    )
    db.commit()

    links = db.execute(
        select(ChapterPlanSceneLink)
        .where(ChapterPlanSceneLink.plan_revision_id == plan.id)
        .order_by(ChapterPlanSceneLink.sort_order)
    ).scalars().all()
    with pytest.raises(AppError, match="previous scene must be accepted"):
        start_generation_run(
            db,
            "author",
            links[1].scene_id,
            RunCreateRequest(
                run_scope="scene",
                request_type="continue",
                decision_target="scene",
                plan_revision_id=plan.id,
                base_scene_revision_id=None,
            ),
            "manual-skip-command",
            "manual-skip-key",
        )


def test_old_plan_scene_decision_is_rejected_after_plan_replacement(db):
    chapter = _chapter(db)
    first_plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["old-1"]},
        "old",
        {"actor_id": "a", "idempotency_key": "old-plan"},
    )
    first_plan.scene_briefs = [{"client_key": "old-1", "title": "旧场景", "scene_brief": {"version": "old"}}]
    first_plan.chapter_contract = {"scenes": first_plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        first_plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "old-accept"},
    )
    db.commit()
    old_link = db.execute(
        select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == first_plan.id)
    ).scalar_one()
    old_run = start_generation_run(
        db,
        "author",
        old_link.scene_id,
        RunCreateRequest(
            run_scope="scene",
            request_type="continue",
            decision_target="scene",
            plan_revision_id=first_plan.id,
            base_scene_revision_id=None,
        ),
        "old-run-command",
        "old-run-key",
    )
    db.get(GenerationRun, old_run["run_id"]).status = "waiting_feedback"
    db.commit()

    second_plan = create_chapter_plan_revision(
        db,
        chapter.id,
        first_plan.id,
        {"scene_keys": ["new-1"]},
        "new",
        {"actor_id": "a", "idempotency_key": "new-plan"},
    )
    second_plan.scene_briefs = [{"client_key": "new-1", "title": "新场景", "scene_brief": {"version": "new"}}]
    second_plan.chapter_contract = {"scenes": second_plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        second_plan.id,
        first_plan.id,
        1,
        {"actor_id": "a", "idempotency_key": "new-accept"},
    )
    db.commit()

    with pytest.raises(AppError, match="current accepted plan"):
        submit_run_decision(
            db,
            "author",
            old_run["run_id"],
            DecisionRequest(
                idempotency_key="old-decision",
                expected_run_version=1,
                target="scene",
                decision="cancel",
            ),
            "old-decision-command",
        )


def test_outbox_replay_does_not_advance_without_previous_scene_revision(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1", "s2"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-no-revision"},
    )
    plan.scene_briefs = [
        {"client_key": "s1", "title": "S1", "scene_brief": {}},
        {"client_key": "s2", "title": "S2", "scene_brief": {}},
    ]
    plan.chapter_contract = {"scenes": plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "accept-no-revision"},
    )
    db.commit()
    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-no-revision")
    worker.tick()
    links = db.execute(
        select(ChapterPlanSceneLink)
        .where(ChapterPlanSceneLink.plan_revision_id == plan.id)
        .order_by(ChapterPlanSceneLink.sort_order)
    ).scalars().all()
    first_run = db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[0].scene_id)
    ).scalar_one()
    first_run.status = "accepted"
    db.commit()

    worker.tick()
    assert db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == links[1].scene_id)
    ).scalar_one_or_none() is None


def test_worker_uses_accepted_plan_scene_brief_after_scene_edit(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-brief"},
    )
    plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {"tone": "accepted-plan"}}]
    plan.chapter_contract = {"scenes": plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "accept-brief"},
    )
    db.commit()
    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-brief")
    worker.tick()
    link = db.execute(
        select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan.id)
    ).scalar_one()
    scene = db.get(Scene, link.scene_id)
    scene.scene_brief = {"tone": "mutable-scene"}
    db.commit()
    db.expire_all()
    run = db.execute(
        select(GenerationRun).where(GenerationRun.scene_id == link.scene_id)
    ).scalar_one()
    envelope = worker._build_envelope(db, run, {"fencing_token": 0})
    assert envelope.scene_brief == {"tone": "accepted-plan"}


def test_workflow_read_ignores_old_plan_scene_run(db):
    chapter = _chapter(db)
    old_plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "old",
        {"actor_id": "a", "idempotency_key": "read-old-plan"},
    )
    old_plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    old_plan.chapter_contract = {"scenes": old_plan.scene_briefs}
    accept_chapter_plan_revision(db, chapter.id, old_plan.id, None, 1, {"actor_id": "a", "idempotency_key": "read-old-accept"})
    db.commit()
    old_link = db.execute(select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == old_plan.id)).scalar_one()
    old_run = start_generation_run(
        db,
        "author",
        old_link.scene_id,
        RunCreateRequest(run_scope="scene", request_type="continue", decision_target="scene", plan_revision_id=old_plan.id, base_scene_revision_id=None),
        "read-old-run",
        "read-old-run-key",
    )
    db.get(GenerationRun, old_run["run_id"]).status = "waiting_feedback"
    db.commit()

    new_plan = create_chapter_plan_revision(
        db,
        chapter.id,
        old_plan.id,
        {"scene_keys": ["s1"]},
        "new",
        {"actor_id": "a", "idempotency_key": "read-new-plan"},
    )
    new_plan.scene_briefs = [{"client_key": "s1", "scene_id": old_link.scene_id, "title": "S1", "scene_brief": {}}]
    new_plan.chapter_contract = {"scenes": new_plan.scene_briefs}
    accept_chapter_plan_revision(db, chapter.id, new_plan.id, old_plan.id, 1, {"actor_id": "a", "idempotency_key": "read-new-accept"})
    db.commit()

    workflow = chapter_workflow_read(db, chapter.id)
    assert workflow["active_run"] is None
    assert workflow["scenes"][0]["current_run_id"] is None


def test_worker_rejects_queued_scene_when_base_revision_changes(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "base-recheck-plan"},
    )
    plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    plan.chapter_contract = {"scenes": plan.scene_briefs}
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, {"actor_id": "a", "idempotency_key": "base-recheck-accept"})
    db.commit()
    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-base-recheck")
    worker.tick()
    link = db.execute(select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan.id)).scalar_one()
    scene = db.get(Scene, link.scene_id)
    revision = SceneRevision(scene_id=scene.id, content="new", content_hash="c" * 64, reason="edit", source_ref="test", status="accepted")
    db.add(revision)
    db.flush()
    scene.accepted_scene_revision_id = revision.id
    db.commit()
    run = db.execute(select(GenerationRun).where(GenerationRun.scene_id == scene.id)).scalar_one()
    with pytest.raises(AppError, match="scene baseline is stale"):
        worker._build_envelope(db, run, {"fencing_token": 0})


def test_worker_supersedes_queued_scene_from_replaced_plan_before_execution(db):
    chapter = _chapter(db)
    old_plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "old",
        {"actor_id": "a", "idempotency_key": "queued-old-plan"},
    )
    old_plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    old_plan.chapter_contract = {"scenes": old_plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        old_plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "queued-old-accept"},
    )
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    executed_run_ids: list[str] = []
    worker: RunWorker

    def graph_builder(run, session):
        executed_run_ids.append(run.id)
        return worker._default_graph_builder(run, session)

    worker = RunWorker(factory, actor_id="worker-old-plan", graph_builder=graph_builder)
    with factory() as session:
        assert worker._ensure_next_scene_run(session, chapter.id, old_plan.id)
        session.commit()

    new_plan = create_chapter_plan_revision(
        db,
        chapter.id,
        old_plan.id,
        {"scene_keys": ["s1"]},
        "new",
        {"actor_id": "a", "idempotency_key": "queued-new-plan"},
    )
    new_plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    new_plan.chapter_contract = {"scenes": new_plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        new_plan.id,
        old_plan.id,
        1,
        {"actor_id": "a", "idempotency_key": "queued-new-accept"},
    )
    db.commit()

    old_run = db.execute(
        select(GenerationRun).where(GenerationRun.plan_revision_id == old_plan.id)
    ).scalar_one()
    assert old_run.status == "queued"

    worker.tick()
    db.refresh(old_run)
    assert old_run.status == "superseded"
    assert old_run.last_error_code == "PLAN_REVISION_CONFLICT"
    assert old_run.id not in executed_run_ids


def test_workflow_read_surfaces_active_feedback_before_accepted_revision(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "feedback-status-plan"},
    )
    plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    plan.chapter_contract = {"scenes": plan.scene_briefs}
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, {"actor_id": "a", "idempotency_key": "feedback-status-accept"})
    db.commit()
    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-feedback-status")
    worker.tick()
    link = db.execute(select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan.id)).scalar_one()
    scene = db.get(Scene, link.scene_id)
    revision = SceneRevision(scene_id=scene.id, content="accepted", content_hash="d" * 64, reason="accept", source_ref="test", status="accepted")
    db.add(revision)
    db.flush()
    scene.accepted_scene_revision_id = revision.id
    run = db.execute(select(GenerationRun).where(GenerationRun.scene_id == scene.id)).scalar_one()
    run.status = "waiting_feedback"
    db.commit()

    workflow = chapter_workflow_read(db, chapter.id)
    assert workflow["scenes"][0]["status"] == "waiting_feedback"


def test_worker_rejects_queued_scene_run_after_plan_replacement(db):
    """计划替换后，旧计划 queued run 不得进入 graph 执行。"""
    chapter = _chapter(db)
    old_plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1"]},
        "old",
        {"actor_id": "a", "idempotency_key": "queued-old-plan"},
    )
    old_plan.scene_briefs = [{"client_key": "s1", "title": "S1", "scene_brief": {}}]
    old_plan.chapter_contract = {"scene_keys": ["s1"], "scenes": old_plan.scene_briefs}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        old_plan.id,
        None,
        1,
        {"actor_id": "a", "idempotency_key": "queued-old-accept"},
    )
    db.commit()

    graph_builds: list[str] = []
    graph_invocations: list[str] = []

    class _NoopGraph:
        def invoke(self, state, envelope, thread_id, resume=None):
            graph_invocations.append(thread_id)
            return {
                "run_status": "paused",
                "pending_node": "test",
                "last_durable_node": "test",
                "clarification_questions": [],
            }

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(
        factory,
        actor_id="worker-stale-plan",
        graph_builder=lambda run, session: (graph_builds.append(run.id) or _NoopGraph()),
    )
    assert worker._recover_accepted_plan_scene_queues() == 1
    old_run = db.execute(
        select(GenerationRun)
        .where(GenerationRun.chapter_id == chapter.id, GenerationRun.scene_id.is_not(None))
    ).scalar_one()
    assert old_run.status == "queued"

    replacement = create_chapter_plan_revision(
        db,
        chapter.id,
        old_plan.id,
        {"scene_keys": []},
        "replacement",
        {"actor_id": "a", "idempotency_key": "queued-new-plan"},
    )
    replacement.scene_briefs = []
    replacement.chapter_contract = {"scene_keys": [], "scenes": []}
    accept_chapter_plan_revision(
        db,
        chapter.id,
        replacement.id,
        old_plan.id,
        1,
        {"actor_id": "a", "idempotency_key": "queued-new-accept"},
    )
    db.commit()

    with factory() as session:
        assert worker._process_one(session, old_run.id) is True

    db.expire_all()
    old_run = db.get(GenerationRun, old_run.id)
    assert old_run is not None
    assert old_run.status == "superseded"
    assert old_run.last_error_code == "PLAN_REVISION_CONFLICT"
    assert graph_builds == []
    assert graph_invocations == []
