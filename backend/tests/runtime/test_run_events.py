from __future__ import annotations

import pytest

from app.errors import AppError
from app.runtime.run_events import FakeRunEventEmitter, RunEventEmitterPort, sanitize_payload


def test_emit_requires_fencing_token():
    emitter = FakeRunEventEmitter()
    with pytest.raises(AppError):
        emitter.emit("g1", "node_started", {}, None)  # type: ignore[arg-type]


def test_emit_assigns_sequence_and_sanitizes():
    emitter = FakeRunEventEmitter()
    emitter.emit("g1", "node_started", {"content": "secret prose", "node": "writing"}, 1)
    assert len(emitter.events) == 1
    ev = emitter.events[0]
    assert ev["sequence"] == 1
    assert ev["payload"]["content"] == "[redacted]"
    assert ev["payload"]["node"] == "writing"


def test_emit_uses_passed_fencing_token():
    emitter = FakeRunEventEmitter()
    emitter.emit("g1", "node_started", {}, 5)
    assert emitter.events[0]["fencing_token"] == 5


def test_port_wrapper_sanitizes_payload():
    inner = FakeRunEventEmitter()
    port = RunEventEmitterPort(inner)
    port.emit("g1", "node_started", {"draft_text": "secret", "node": "writing"}, 1)
    assert inner.events[0]["payload"]["draft_text"] == "[redacted]"


def test_sanitize_payload_redacts_sensitive_keys():
    out = sanitize_payload({"content": "x", "prompt": "y", "node": "writing"})
    assert out["content"] == "[redacted]"
    assert out["prompt"] == "[redacted]"
    assert out["node"] == "writing"


def test_sanitize_payload_keeps_non_sensitive():
    out = sanitize_payload({"node": "writing", "worker_id": "w1"})
    assert out["node"] == "writing"
    assert out["worker_id"] == "w1"
