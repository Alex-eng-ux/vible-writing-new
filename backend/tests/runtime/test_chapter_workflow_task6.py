from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import Chapter, GenerationRun, RunOutboxRecord, Volume
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    chapter_workflow_read,
    commit_chapter_version,
    create_chapter,
    create_chapter_plan_revision,
)
from app.domain.resources import create_project, create_volume
from app.runtime.run_worker import RunWorker


def _chapter(db):
    project = create_project(db, "P-task6", "g", "r", "s", {"actor_id": "a", "idempotency_key": "p-task6"})
    volume = create_volume(db, project.id, "V-task6", "g", "m", "r", {"actor_id": "a", "idempotency_key": "v-task6"})
    return create_chapter(
        db,
        volume.id,
        "C-task6",
        "pov",
        {"text": "章末必须留下可追溯的事实", "goal": "canon"},
        {"actor_id": "a", "idempotency_key": "c-task6"},
    )


def _ctx(key: str) -> dict:
    return {
        "actor_id": "author",
        "source": "author",
        "manual_command_id": f"command-{key}",
        "idempotency_key": key,
    }


def test_worker_consumes_chapter_acceptance_and_enqueues_canon_run(db):
    """章节接受 outbox 必须由真实 Worker 消费并幂等创建 Canon 运行。"""
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        "lineage-task6",
        {"scenes": []},
        "fixture",
        _ctx("plan-task6"),
    )
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        _ctx("accept-plan-task6"),
    )
    staged = aggregate_chapter_revision(db, chapter.id, [], "fixture", _ctx("aggregate-task6"))
    commit_chapter_version(db, staged.id, _ctx("accept-chapter-task6"))
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-task6")

    assert worker.tick() == 1

    db.expire_all()
    canon_runs = db.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter.id,
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == staged.id,
        )
    ).scalars().all()
    assert len(canon_runs) == 1
    assert canon_runs[0].status == "waiting_feedback"

    workflow = chapter_workflow_read(db, chapter.id)
    assert workflow["phase"] == "canon_feedback"
    assert workflow["canon_run_id"] == canon_runs[0].id
    assert workflow["canon"]["status"] == "waiting_feedback"
    assert workflow["canon"]["source_revision_id"] == staged.id
    assert workflow["pending_decision"]["target"] == "canon"
    assert workflow["pending_decision"]["kind"] == "canon_feedback"

    accepted_event = db.execute(
        select(RunOutboxRecord).where(
            RunOutboxRecord.resource_type == "chapter_revision",
            RunOutboxRecord.resource_id == staged.id,
        )
    ).scalar_one()
    assert accepted_event.delivery_status == "consumed"

    assert worker.tick() == 0
    assert db.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter.id,
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == staged.id,
        )
    ).scalars().all().__len__() == 1


def test_workflow_keeps_active_chapter_review_out_of_completed_phase(db):
    """已有 accepted 章节版本时，进行中的章节审校仍必须优先展示审校阶段。"""
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        "lineage-active-review",
        {"scenes": []},
        "fixture",
        _ctx("plan-active-review"),
    )
    accept_chapter_plan_revision(
        db,
        chapter.id,
        plan.id,
        None,
        1,
        _ctx("accept-plan-active-review"),
    )
    staged = aggregate_chapter_revision(
        db, chapter.id, [], "fixture", _ctx("aggregate-active-review")
    )
    commit_chapter_version(db, staged.id, _ctx("accept-chapter-active-review"))
    volume = db.get(Volume, db.get(Chapter, chapter.id).volume_id)
    db.add(
        GenerationRun(
            project_id=volume.project_id,
            chapter_id=chapter.id,
            plan_revision_id=plan.id,
            request_type="review",
            decision_target="chapter",
            status="waiting_feedback",
            run_version=1,
            write_fencing_token=0,
            normalized_input={},
        )
    )
    db.commit()

    workflow = chapter_workflow_read(db, chapter.id)

    assert workflow["phase"] == "chapter_feedback"
    assert workflow["pending_decision"]["target"] == "chapter"
    assert workflow["pending_decision"]["kind"] == "accept_chapter"


def test_worker_isolates_chapter_consumer_failure_and_keeps_event_replayable(db, monkeypatch):
    """单条 Canon 消费失败只回滚当前事件，不能阻断同一 tick 的其他事件。"""
    records = [
        RunOutboxRecord(
            resource_type="chapter_revision",
            resource_id="revision-fails",
            payload_schema="canon-auto.v1",
            payload={
                "event_type": "chapter_revision.accepted",
                "chapter_id": "chapter-fails",
                "accepted_chapter_revision_id": "revision-fails",
            },
            delivery_status="pending",
            attempt_count=0,
            producer_command_id="command-fails",
            generation_run_id=None,
        ),
        RunOutboxRecord(
            resource_type="chapter_revision",
            resource_id="revision-ok",
            payload_schema="canon-auto.v1",
            payload={
                "event_type": "chapter_revision.accepted",
                "chapter_id": "chapter-ok",
                "accepted_chapter_revision_id": "revision-ok",
            },
            delivery_status="pending",
            attempt_count=0,
            producer_command_id="command-ok",
            generation_run_id=None,
        ),
    ]
    db.add_all(records)
    db.commit()
    calls: list[str] = []

    def consume(_session, payload):
        calls.append(payload["chapter_id"])
        if payload["chapter_id"] == "chapter-fails":
            raise RuntimeError("temporary canon consumer failure")

    monkeypatch.setattr("app.runtime.run_worker.handle_chapter_accepted_outbox", consume)
    worker = RunWorker(sessionmaker(bind=db.bind, expire_on_commit=False), actor_id="worker-task6-failure")

    assert worker.tick() == 0

    db.expire_all()
    assert calls == ["chapter-fails", "chapter-ok"]
    failed = db.get(RunOutboxRecord, records[0].outbox_id)
    assert failed.delivery_status == "failed"
    assert failed.attempt_count == 1
    assert failed.next_attempt_at is not None
    assert db.get(RunOutboxRecord, records[1].outbox_id).delivery_status == "consumed"

    # 将时间推进到退避窗口之后，验证失败事件可重放，而不是依赖真实等待。
    failed.next_attempt_at = failed.next_attempt_at - timedelta(
        seconds=RunWorker._CONSUMER_RETRY_DELAY_SECONDS + 1
    )
    db.commit()

    monkeypatch.setattr(
        "app.runtime.run_worker.handle_chapter_accepted_outbox",
        lambda _session, payload: calls.append(f"retry:{payload['chapter_id']}"),
    )
    assert worker.tick() == 0
    db.expire_all()
    assert calls[-1] == "retry:chapter-fails"
    retried = db.get(RunOutboxRecord, records[0].outbox_id)
    assert retried.delivery_status == "consumed"
    assert retried.next_attempt_at is None


def test_worker_rejects_chapter_acceptance_outbox_metadata_mismatch(db, monkeypatch):
    """schema 或 resource_id 不匹配时标记失败，且不得调用 Canon 消费者。"""
    records = [
        RunOutboxRecord(
            resource_type="chapter_revision",
            resource_id="revision-schema",
            payload_schema="wrong-schema",
            payload={
                "event_type": "chapter_revision.accepted",
                "chapter_id": "chapter-schema",
                "accepted_chapter_revision_id": "revision-schema",
            },
            delivery_status="pending",
            attempt_count=0,
            producer_command_id="command-schema",
            generation_run_id=None,
        ),
        RunOutboxRecord(
            resource_type="chapter_revision",
            resource_id="revision-resource",
            payload_schema="canon-auto.v1",
            payload={
                "event_type": "chapter_revision.accepted",
                "chapter_id": "chapter-resource",
                "accepted_chapter_revision_id": "revision-payload",
            },
            delivery_status="pending",
            attempt_count=0,
            producer_command_id="command-resource",
            generation_run_id=None,
        ),
    ]
    db.add_all(records)
    db.commit()
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.runtime.run_worker.handle_chapter_accepted_outbox",
        lambda _session, payload: calls.append(payload),
    )
    worker = RunWorker(sessionmaker(bind=db.bind, expire_on_commit=False), actor_id="worker-task6-metadata")

    assert worker.tick() == 0

    db.expire_all()
    assert calls == []
    assert db.get(RunOutboxRecord, records[0].outbox_id).delivery_status == "failed"
    assert db.get(RunOutboxRecord, records[1].outbox_id).delivery_status == "failed"
    assert db.query(GenerationRun).filter(GenerationRun.decision_target == "canon").count() == 0


def test_run_forever_retries_after_outbox_infrastructure_failure(db, monkeypatch):
    """一次 tick 基础设施异常不应终止 Worker 的持续轮询。"""
    worker = RunWorker(sessionmaker(bind=db.bind, expire_on_commit=False), actor_id="worker-task6-loop")
    calls: list[str] = []

    def tick() -> int:
        calls.append("tick")
        if len(calls) == 1:
            raise RuntimeError("temporary database outage")
        return 0

    sleep_calls = 0

    def stop_after_retry(_interval: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise StopIteration("test loop stop")

    monkeypatch.setattr(worker, "tick", tick)
    monkeypatch.setattr(time, "sleep", stop_after_retry)

    try:
        worker.run_forever(interval=0)
    except StopIteration:
        pass

    assert calls == ["tick", "tick"]
