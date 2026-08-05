"""API/领域命令的幂等执行助手。

每个非 GET 命令在真正执行前，先按 ``(resource_scope, operation_name,
Idempotency-Key)`` 原子 claim 一条 ``CommandIdempotencyRecord``。已完成的
同指纹 claim 会重放第一次响应；进行中的同键但不同指纹 claim 抛
``IDEMPOTENCY_KEY_REUSE``，防止同一幂等键被不同请求复用。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from ..domain import idempotency as idem


def execute_command(
    session: Session,
    resource_scope: str,
    operation_name: str,
    idempotency_key: str,
    request_fingerprint: str,
    run: Callable[[str | None], tuple[dict, str | None]],
) -> dict:
    """claim 一个命令，只真正执行一次，并持久化第一次响应。

    首次 claim 时由幂等层生成并持久化 ``manual_command_id``，并通过参数
    传给 ``run``；重放与过期接管都会复用首次生成的原 ID，绝不重新生成。
    参数 ``run`` 接收 ``manual_command_id`` 并返回 ``(response, result_ref)``。
    重放时直接返回已存储的第一次响应且不再调用 ``run``。
    """
    now = datetime.now(UTC)
    claim_lease = str(uuid4())
    record = idem.claim(
        session,
        resource_scope,
        operation_name,
        idempotency_key,
        request_fingerprint,
        claim_lease,
        now,
    )
    if record.status == "completed":
        return record.first_response or {}
    manual_command_id = record.manual_command_id
    response, result_ref = run(manual_command_id)
    idem.complete(session, record, response, result_ref, manual_command_id)
    return response
