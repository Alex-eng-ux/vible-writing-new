from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CommandIdempotencyRecord
from ..errors import AppError


class IdService:
    """按对象类型与幂等键分配正式 ID。

    重试会复用同一个 ID；agent/hook 不具备创建正式 ID 的能力（只能由服务端
    分配）。跨运行引用的 ID 分配会被拒绝。
    """

    def __init__(self, session: Session) -> None:
        """初始化 ID 分配服务。

        参数：session 为数据库会话。
        副作用：持有会话引用，事务边界由调用方管理。
        """
        self._session = session

    def allocate(self, object_type: str, idempotency_key: str, scope: str) -> str:
        """按对象类型与幂等键分配一个正式 ID 并返回。

        参数：object_type 为对象类型，用于构造资源作用域键；idempotency_key
        为幂等键，同一键重试复用相同 ID；scope 为作用域（如项目/运行上下文）。
        返回：分配的正式 ID（字符串形式的 UUID）。
        副作用：在会话中新增或更新 CommandIdempotencyRecord 并 flush；调用方
        需负责提交事务。
        失败条件：idempotency_key 为空时抛 AppError("COMMAND_CONTEXT_MISMATCH")。
        幂等约束：在 (resource_scope, operation_name="allocate_id", idempotency_key)
        元组上幂等——已存在且带 result_ref 的记录直接返回既有 ID，不重复插入。
        """
        if not idempotency_key:
            raise AppError("COMMAND_CONTEXT_MISMATCH", "idempotency key is required")
        scope_key = f"id:{object_type}:{scope}"
        record = self._session.execute(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.resource_scope == scope_key,
                CommandIdempotencyRecord.operation_name == "allocate_id",
                CommandIdempotencyRecord.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if record is not None and record.result_ref:
            return record.result_ref
        new_id = str(uuid.uuid4())
        if record is None:
            record = CommandIdempotencyRecord(
                resource_scope=scope_key,
                operation_name="allocate_id",
                idempotency_key=idempotency_key,
                request_fingerprint=f"allocate:{object_type}",
                status="completed",
                result_ref=new_id,
            )
            self._session.add(record)
        else:
            record.result_ref = new_id
            record.status = "completed"
        self._session.flush()
        return new_id
