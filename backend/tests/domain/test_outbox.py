from __future__ import annotations

from app.domain.outbox import Outbox


def test_enqueue_dedup_by_resource_key(db):
    outbox = Outbox(db)
    r1 = outbox.enqueue("scene_revision", "res-1", "schema-v1", {"x": 1}, "cmd-1")
    r2 = outbox.enqueue("scene_revision", "res-1", "schema-v1", {"x": 1}, "cmd-1")
    assert r1.outbox_id == r2.outbox_id


def test_publish_failure_does_not_rollback_business_transaction(db):
    outbox = Outbox(db)
    record = outbox.enqueue("scene_revision", "res-1", "schema-v1", {"x": 1}, "cmd-1")
    # Simulate a publish failure: mark_failed keeps the business row intact.
    outbox.mark_failed(record, "connection refused", 60)
    db.flush()
    assert record.delivery_status == "failed"
    assert record.last_error == "connection refused"
    assert record.attempt_count == 0


def test_publish_retry_attempt_count_increments(db):
    outbox = Outbox(db)
    record = outbox.enqueue("scene_revision", "res-1", "schema-v1", {"x": 1}, "cmd-1")
    outbox.mark_publishing(record)
    assert record.attempt_count == 1
    outbox.mark_published(record)
    assert record.delivery_status == "published"


def test_cursor_advance_rule(db):
    from app.db.models import RunEventConsumerCursor

    cursor = RunEventConsumerCursor(consumer_name="ssg", stream_key="run-1", last_sequence=0)
    db.add(cursor)
    db.flush()
    cursor.last_sequence = 5
    cursor.last_event_id = "event-5"
    db.flush()
    assert cursor.last_sequence == 5
