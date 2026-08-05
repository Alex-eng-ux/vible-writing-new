from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CommandIdempotencyRecord
from ..errors import AppError

CLAIM_TTL = timedelta(minutes=5)


def _fingerprint(request: dict) -> str:
    """对请求体做规范化指纹：键排序、无多余空白、UTF-8 稳定哈希。"""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint(request: dict) -> str:
    """公开的请求体规范化指纹，供 API 幂等 claim 在事务外先计算。"""
    return _fingerprint(request)


def claim(
    session: Session,
    resource_scope: str,
    operation_name: str,
    idempotency_key: str,
    request_fingerprint: str,
    claim_lease: str,
    now,
) -> CommandIdempotencyRecord:
    """原子地 claim 一条幂等键。

    - 新键：创建 processing 记录，并在首次 claim 落库时生成 manual_command_id。
    - 同键同指纹仍在处理：抛 IDEMPOTENCY_IN_PROGRESS。
    - 同键同指纹已完成：重放第一次响应。
    - 同键异指纹：抛 IDEMPOTENCY_KEY_REUSE。
    - 过期 claim 的 processing 记录：由新 claim 者接管。

    manual_command_id 只在首次 claim 时生成并持久化；重放与过期接管都会
    复用首次生成的原 ID，绝不重新生成。
    """
    record = session.execute(
        select(CommandIdempotencyRecord).where(
            CommandIdempotencyRecord.resource_scope == resource_scope,
            CommandIdempotencyRecord.operation_name == operation_name,
            CommandIdempotencyRecord.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()

    if record is None:
        record = CommandIdempotencyRecord(
            resource_scope=resource_scope,
            operation_name=operation_name,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status="processing",
            claim_lease=claim_lease,
            claim_expires_at=now + CLAIM_TTL,
            manual_command_id=str(uuid.uuid4()),
        )
        session.add(record)
        session.flush()
        return record

    if record.status == "completed":
        if record.request_fingerprint != request_fingerprint:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSE",
                "idempotency key reused with a different request",
            )
        return record

    # processing
    if record.request_fingerprint != request_fingerprint:
        raise AppError(
            "IDEMPOTENCY_KEY_REUSE",
            "idempotency key reused with a different request",
        )
    if record.claim_expires_at is not None and record.claim_expires_at > now:
        raise AppError(
            "IDEMPOTENCY_IN_PROGRESS",
            "request with this idempotency key is still in progress",
        )
    # expired claim: takeover
    record.claim_lease = claim_lease
    record.claim_expires_at = now + CLAIM_TTL
    session.flush()
    return record


def complete(
    session: Session,
    record: CommandIdempotencyRecord,
    first_response: dict,
    result_ref: str | None,
    manual_command_id: str | None = None,
) -> None:
    """Mark a claimed idempotency record as completed and persist the first response."""
    record.status = "completed"
    record.first_response = first_response
    record.result_ref = result_ref
    if manual_command_id is not None:
        record.manual_command_id = manual_command_id
    session.flush()


def fail(session: Session, record: CommandIdempotencyRecord) -> None:
    """Mark a claimed idempotency record as failed (released for retry)."""
    record.status = "failed"
    record.claim_lease = None
    record.claim_expires_at = None
    session.flush()
