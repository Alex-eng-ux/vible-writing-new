from __future__ import annotations

from app.runtime.outbox import FakeRunOutbox, RunOutboxPort


def test_fake_outbox_enqueues_message():
    outbox = FakeRunOutbox()
    outbox.enqueue(
        {
            "resource_type": "chapter_revision",
            "resource_id": "rev-1",
            "payload_schema": "v1",
            "payload": {"status": "accepted"},
            "producer_command_id": "cmd-1",
            "generation_run_id": "g1",
        },
        3,
    )
    assert len(outbox.messages) == 1
    msg, token = outbox.messages[0]
    assert msg["resource_id"] == "rev-1"
    assert token == 3


def test_outbox_implements_port_protocol():
    # Verify the fake satisfies the RunOutboxPort protocol structurally.
    outbox: RunOutboxPort = FakeRunOutbox()
    outbox.enqueue(
        {
            "resource_type": "x",
            "resource_id": "id",
            "payload_schema": "v1",
            "payload": {},
            "producer_command_id": "cmd",
            "generation_run_id": "g1",
        },
        1,
    )
    assert len(outbox.messages) == 1


def test_outbox_does_not_publish_or_track_cursor():
    """Task 4A must not depend on Task 5B's publisher/SSE cursor."""
    outbox = FakeRunOutbox()
    assert not hasattr(outbox, "publish")
    assert not hasattr(outbox, "cursor")
    assert not hasattr(outbox, "replay")
