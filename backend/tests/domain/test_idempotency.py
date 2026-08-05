from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import idempotency
from app.errors import AppError

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def test_concurrent_claim_same_key_in_progress(db):
    rec = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-1", NOW)
    assert rec.status == "processing"
    with pytest.raises(AppError) as exc:
        idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-2", NOW)
    assert exc.value.code == "IDEMPOTENCY_IN_PROGRESS"


def test_same_key_different_fingerprint_reuse(db):
    rec = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-1", NOW)
    idempotency.complete(db, rec, {"result": 1}, "ref-1")
    with pytest.raises(AppError) as exc:
        idempotency.claim(db, "scope:A", "op1", "key-1", "fp-2", "lease-2", NOW)
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSE"


def test_same_key_same_fingerprint_replays_first_response(db):
    rec = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-1", NOW)
    idempotency.complete(db, rec, {"result": 1}, "ref-1")
    replayed = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-2", NOW)
    assert replayed.first_response == {"result": 1}
    assert replayed.status == "completed"


def test_expired_claim_takeover(db):
    rec = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-1", NOW)
    # Same fingerprint, expired claim -> takeover.
    rec.claim_expires_at = NOW - timedelta(minutes=1)
    db.flush()
    taken = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-2", NOW)
    assert taken.claim_lease == "lease-2"


def test_manual_command_id_generated_on_first_claim_and_reused(db):
    rec = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-1", NOW)
    # 首次 claim 落库时生成并持久化 manual_command_id。
    assert rec.manual_command_id is not None
    assert rec.status == "processing"
    idempotency.complete(db, rec, {"result": 1}, "ref-1")
    replayed = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-2", NOW)
    assert replayed.manual_command_id == rec.manual_command_id


def test_manual_command_id_reused_after_expired_claim_takeover(db):
    """claim 后崩溃，过期后被接管，必须复用原 manual_command_id。"""
    rec = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-1", NOW)
    original_id = rec.manual_command_id
    assert original_id is not None
    # 模拟 claim 后进程崩溃：记录仍为 processing 且 claim 已过期。
    rec.claim_expires_at = NOW - timedelta(minutes=1)
    db.flush()
    taken = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-2", NOW)
    assert taken.claim_lease == "lease-2"
    # 接管必须复用首次生成的原 ID，绝不重新生成。
    assert taken.manual_command_id == original_id


def test_manual_command_id_reused_after_complete_then_claim(db):
    """已完成后再 claim（重放）也必须复用原 manual_command_id。"""
    rec = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-1", NOW)
    original_id = rec.manual_command_id
    idempotency.complete(db, rec, {"result": 1}, "ref-1")
    replayed = idempotency.claim(db, "scope:A", "op1", "key-1", "fp-1", "lease-2", NOW)
    assert replayed.manual_command_id == original_id
