"""Task 5B outbox 发布与事件 fencing 测试。

验证：发布失败不回滚已提交业务事务；重复发布幂等（不产生重复业务事件）；
旧 fencing token 拒绝写入事件与 outbox；消费者游标持久化与读取。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.api.schemas import RunCreateRequest
from app.db.models import GenerationRun, RunOutboxRecord
from app.domain.chapters import create_chapter
from app.domain.resources import create_project, create_volume
from app.errors import AppError
from app.runtime.outbox import PostgresOutboxPublisher, PostgresRunOutbox
from app.runtime.run_events import PostgresRunEventStore
from app.services.generation_runs import start_generation_run


def _clear_outbox(db) -> None:
    """清空全局待发布记录，保证发布器测试彼此隔离（发布器是全局扫描器）。"""
    db.execute(delete(RunOutboxRecord))
    db.commit()


def _hierarchy(db):
    project = create_project(db, "P", "g", "r", "s", {"actor_id": "a", "idempotency_key": "p"})
    vol = create_volume(db, project.id, "V", "goal", "main", "range", {"actor_id": "a", "idempotency_key": "v"})
    chapter = create_chapter(db, vol.id, "C", "p", {"text": "intent"}, {"actor_id": "a", "idempotency_key": "c"})
    return chapter


def _start_run(db, chapter) -> str:
    body = RunCreateRequest(run_scope="chapter", request_type="new_chapter", decision_target="plan")
    return start_generation_run(db, "a", chapter.id, body, "manual-1", "key-1")["run_id"]


def test_publish_failure_does_not_rollback_business(db):
    """发布失败只标记 outbox 记录，不回滚已提交的运行/事件业务事务。"""
    _clear_outbox(db)
    chapter = _hierarchy(db)
    run_id = _start_run(db, chapter)
    db.commit()  # 业务事务（运行 + run_queued 事件 + outbox 入队）已提交

    def bad_deliver(payload):
        raise RuntimeError("deliver boom")

    publisher = PostgresOutboxPublisher(db, deliver=bad_deliver)
    processed = publisher.publish(datetime.now(UTC))
    db.commit()
    assert processed == 1
    # 业务数据仍在（未被回滚）。
    run = db.get(GenerationRun, run_id)
    assert run is not None and run.status == "queued"
    # 事件仍在。
    store = PostgresRunEventStore(db)
    assert store.max_sequence(run_id) == 1
    # outbox 记录进入 failed 且保留错误，等待重试。
    row = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalar_one()
    assert row.delivery_status == "failed"
    assert "deliver boom" in (row.last_error or "")
    assert row.attempt_count == 1


def test_repeat_publish_is_idempotent(db):
    """已 published 的 outbox 记录不再被处理，重复发布不产生重复业务事件。"""
    _clear_outbox(db)
    chapter = _hierarchy(db)
    run_id = _start_run(db, chapter)
    db.commit()

    publisher = PostgresOutboxPublisher(db, deliver=lambda payload: None)
    first = publisher.publish(datetime.now(UTC))
    db.commit()
    assert first >= 1
    second = publisher.publish(datetime.now(UTC))
    db.commit()
    assert second == 0  # 没有待处理记录

    rows = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalars().all()
    assert len(rows) == 1  # 不产生重复业务记录
    assert rows[0].delivery_status == "published"
    assert rows[0].attempt_count == 1
    # 事件也保持单一。
    store = PostgresRunEventStore(db)
    assert store.max_sequence(run_id) == 1


def test_stale_fencing_token_cannot_write(db):
    """旧 fencing token 拒绝写入 RunEvent 与 outbox（fail-closed，RUN_LEASE_LOST）。"""
    run = GenerationRun(
        id="run-stale",
        project_id="proj-x",
        status="queued",
        run_version=1,
        write_fencing_token=3,
    )
    db.add(run)
    db.flush()

    store = PostgresRunEventStore(db)
    with pytest.raises(AppError) as exc:
        store.emit("run-stale", "run_queued", {}, fencing_token=2)
    assert exc.value.code == "RUN_LEASE_LOST"
    outbox = PostgresRunOutbox(db)
    with pytest.raises(AppError) as exc:
        outbox.enqueue(
            {
                "resource_type": "run",
                "resource_id": "run-stale",
                "payload_schema": "run-event.v1",
                "payload": {"event_type": "run_queued"},
                "producer_command_id": "cmd-stale",
                "generation_run_id": "run-stale",
            },
            fencing_token=2,
        )
    assert exc.value.code == "RUN_LEASE_LOST"
    # 当前 token 可以写入，序号从 1 分配。
    event = store.emit("run-stale", "run_queued", {"x": 1}, fencing_token=3, producer_command_id="cmd-1")
    assert event.sequence == 1


def test_consumer_cursor_persist_and_advance(db):
    """消费者游标按 consumer_name + stream_key 持久化并可推进读取。"""
    run = GenerationRun(
        id="run-cur",
        project_id="proj-x",
        status="queued",
        run_version=1,
        write_fencing_token=0,
    )
    db.add(run)
    db.flush()
    store = PostgresRunEventStore(db)
    store.advance_consumer_cursor("sse", "run-cur", 5, "run-cur:5")
    db.flush()
    cursor = store.get_consumer_cursor("sse", "run-cur")
    assert cursor is not None
    assert cursor.last_sequence == 5
    assert cursor.last_event_id == "run-cur:5"
    # 再次推进覆盖游标（先持久化成功游标再确认 outbox 的契约）。
    store.advance_consumer_cursor("sse", "run-cur", 9, "run-cur:9")
    db.flush()
    cursor2 = store.get_consumer_cursor("sse", "run-cur")
    assert cursor2.last_sequence == 9
    assert store.get_consumer_cursor("other", "run-cur") is None


def test_publishing_lease_expired_reclaim(db):
    """发布进程崩溃后卡在 publishing 的记录，租约过期后可由新发布者重新领取。"""
    _clear_outbox(db)
    chapter = _hierarchy(db)
    run_id = _start_run(db, chapter)
    db.commit()

    # 模拟发布进程崩溃：记录卡在 publishing，存在过期租约。
    row = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalar_one()
    row.delivery_status = "publishing"
    row.publisher_owner = "dead-worker"
    row.publisher_lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
    db.commit()

    # 新发布者领取并发布成功。
    publisher = PostgresOutboxPublisher(db, deliver=lambda payload: None, owner="fresh-worker")
    processed = publisher.publish(datetime.now(UTC))
    db.commit()
    assert processed == 1
    db.expire_all()
    row = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalar_one()
    assert row.delivery_status == "published"
    assert row.publisher_owner is None
    assert row.publisher_lease_expires_at is None


def test_publishing_lease_active_not_reclaimed(db):
    """仍在有效租约内的 publishing 记录不会被其他发布者重复领取。"""
    _clear_outbox(db)
    chapter = _hierarchy(db)
    run_id = _start_run(db, chapter)
    db.commit()

    row = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalar_one()
    row.delivery_status = "publishing"
    row.publisher_owner = "active-worker"
    row.publisher_lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
    db.commit()

    publisher = PostgresOutboxPublisher(db, deliver=lambda payload: None, owner="other-worker")
    processed = publisher.publish(datetime.now(UTC))
    db.commit()
    assert processed == 0
    db.expire_all()
    row = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalar_one()
    assert row.delivery_status == "publishing"
    assert row.publisher_owner == "active-worker"


def test_consumer_advances_cursor_before_outbox_confirm(db):
    """消费者成功后先推进持久化游标、再确认 outbox；崩溃后重试不后退游标。"""
    _clear_outbox(db)
    chapter = _hierarchy(db)
    run_id = _start_run(db, chapter)
    db.commit()
    store = PostgresRunEventStore(db)

    calls = {"n": 0}

    def consumer(payload):
        # 消费者处理：先推进持久化游标，再确认 outbox（投递成功返回）。
        calls["n"] += 1
        store.advance_consumer_cursor("consumer-b", run_id, 1, f"{run_id}:1")
        db.flush()
        if calls["n"] == 1:
            # 首次在推进游标之后、确认 outbox 之前崩溃。
            raise RuntimeError("crash after cursor advance")

    publisher = PostgresOutboxPublisher(db, deliver=consumer, owner="cons-b")
    # 第 1 次：游标已推进但 outbox 未确认（failed），等待重试。
    publisher.publish(datetime.now(UTC))
    db.commit()
    cursor = store.get_consumer_cursor("consumer-b", run_id)
    assert cursor is not None and cursor.last_sequence == 1
    row = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalar_one()
    assert row.delivery_status == "failed"
    # 第 2 次：重试窗口已到，outbox 重新投递；游标不后退（幂等），本次成功。
    publisher.publish(datetime.now(UTC) + timedelta(seconds=10))
    db.commit()
    db.expire_all()
    row = db.execute(
        select(RunOutboxRecord).where(RunOutboxRecord.generation_run_id == run_id)
    ).scalar_one()
    assert row.delivery_status == "published"
    assert row.attempt_count == 2
    assert calls["n"] == 2
    cursor = store.get_consumer_cursor("consumer-b", run_id)
    assert cursor is not None and cursor.last_sequence == 1
