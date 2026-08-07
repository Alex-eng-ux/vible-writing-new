from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from app.db.e2e_fixtures import seed_plan
from app.db.models import ChapterPlanSceneLink, GenerationRun, RunOutboxRecord
from app.domain.chapters import chapter_workflow_read, create_chapter, create_scene
from app.runtime.run_worker import RunWorker


def _ctx(key: str) -> dict:
    return {"actor_id": "fixture-test", "idempotency_key": key}


def test_seed_plan_can_link_existing_scene_into_accepted_workflow(db, volume):
    """E2E accepted plan 必须映射已有 scene，否则场景运行前置检查无法放行。"""
    chapter = create_chapter(db, volume, "C-fixture", "pov", {"text": "intent"}, _ctx("chapter"))
    scene = create_scene(db, chapter.id, "S-fixture", {}, _ctx("scene"))

    plan_id = seed_plan(db, chapter.id, scene.id)
    db.commit()

    link = db.query(ChapterPlanSceneLink).filter_by(plan_revision_id=plan_id).one()
    assert link.scene_id == scene.id
    workflow = chapter_workflow_read(db, chapter.id)
    assert workflow["plan"]["accepted_revision_id"] == plan_id
    assert workflow["scenes"][0]["scene_id"] == scene.id


def test_seed_plan_can_suppress_automatic_scene_execution(db, volume):
    """运行流 E2E 需要播种 accepted plan，但不能被测试 Worker 抢先启动。"""
    chapter = create_chapter(db, volume, "C-fixture-no-run", "pov", {"text": "intent"}, _ctx("chapter-no-run"))
    scene = create_scene(db, chapter.id, "S-fixture-no-run", {}, _ctx("scene-no-run"))

    plan_id = seed_plan(db, chapter.id, scene.id)
    db.commit()

    outbox = db.query(RunOutboxRecord).filter_by(
        resource_type="chapter_plan", resource_id=plan_id
    ).one()
    assert outbox.delivery_status == "pending"

    worker = RunWorker(
        sessionmaker(bind=db.bind, expire_on_commit=False),
        actor_id="fixture-worker",
        auto_plan_execution=False,
        process_queued_runs=False,
    )
    worker.tick()
    assert db.query(GenerationRun).filter_by(chapter_id=chapter.id, scene_id=scene.id).count() == 0
