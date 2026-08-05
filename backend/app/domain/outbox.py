"""发件箱（outbox）模式：以去重与重试原子性发布领域事件。

发件箱记录与业务事务同库写入，实现“业务提交与事件发布原子一致”。
发布失败不得回滚已提交的业务事务；重复发布按资源唯一键去重、按
attempt_count 递增重试。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import RunOutboxRecord


class Outbox:
    """以去重与重试原子性发布发件箱记录。

    发布失败必须不得回滚已提交的业务事务。重复发布按资源唯一键去重。
    """

    def __init__(self, session: Session) -> None:
        """用当前会话构造；所有记录操作均写入该会话。"""
        self._session = session

    def enqueue(
        self,
        resource_type: str,
        resource_id: str,
        payload_schema: str,
        payload: dict,
        producer_command_id: str,
        generation_run_id: str | None = None,
    ) -> RunOutboxRecord:
        """入队一条待投递的发件箱记录。

        参数：
            resource_type: 资源类型。
            resource_id: 资源 id。
            payload_schema: 载荷 schema 标识。
            payload: 事件载荷。
            producer_command_id: 产生该事件的命令 id。
            generation_run_id: 关联的生成运行 id（可选）。

        返回：新创建或已存在的 RunOutboxRecord。

        幂等约束：以 (resource_type, resource_id, producer_command_id) 为唯一键，
        若已存在则直接返回既有记录，不重复插入。

        副作用：向会话新增记录并 flush；须在调用方事务内提交。
        """
        existing = self._session.execute(
            select(RunOutboxRecord).where(
                RunOutboxRecord.resource_type == resource_type,
                RunOutboxRecord.resource_id == resource_id,
                RunOutboxRecord.producer_command_id == producer_command_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        record = RunOutboxRecord(
            resource_type=resource_type,
            resource_id=resource_id,
            payload_schema=payload_schema,
            payload=payload,
            delivery_status="pending",
            attempt_count=0,
            producer_command_id=producer_command_id,
            generation_run_id=generation_run_id,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def mark_publishing(self, record: RunOutboxRecord) -> None:
        """将记录标记为“发布中”并递增尝试次数，随后 flush。

        副作用：修改记录状态并 flush；须在调用方事务内提交。
        """
        record.delivery_status = "publishing"
        record.attempt_count += 1
        self._session.flush()

    def mark_published(self, record: RunOutboxRecord) -> None:
        """将记录标记为“已发布”并 flush。

        副作用：修改记录状态并 flush；须在调用方事务内提交。
        """
        record.delivery_status = "published"
        self._session.flush()

    def mark_failed(self, record: RunOutboxRecord, last_error: str, retry_after_seconds: int) -> None:
        """将记录标记为“失败”并设置错误信息与下次重试时间。

        参数：
            last_error: 最近一次失败的错误信息。
            retry_after_seconds: 距 created_at 的秒数，用于计算 next_attempt_at。

        副作用：修改记录状态、错误信息与 next_attempt_at 并 flush；
        须在调用方事务内提交。
        """
        record.delivery_status = "failed"
        record.last_error = last_error
        record.next_attempt_at = record.created_at + timedelta(seconds=retry_after_seconds)
        self._session.flush()
