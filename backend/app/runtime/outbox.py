"""Outbox（发件箱）端口与测试替身。

Outbox 是运行时可靠投递事件/消息的机制：业务写入在某次事务内入队，再由独立的
publisher/consumer 游标读取并投递，从而保证业务状态与投递事件的一致性。本模块
仅冻结端口与内存替身（Task 4A 边界）：
- ``RunOutboxPort.enqueue`` 携带 fencing_token，作为运行写 fence 的一部分；
- Task 5B 才实现 Postgres outbox publisher、consumer cursor 与 SSE 重放；Task 4A
  不得依赖 Task 5B 的 publisher 或 SSE 游标。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol, TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GenerationRun, RunOutboxRecord
from app.errors import AppError


class OutboxMessage(TypedDict):
    """一条待投递的 outbox 消息。

    - resource_type: 资源类型。
    - resource_id: 资源 ID。
    - payload_schema: 负载 schema 标识。
    - payload: 消息负载。
    - producer_command_id: 产生该消息的命令 ID（用于幂等/去重）。
    - generation_run_id: 关联的运行 ID（可为空）。
    """

    resource_type: str
    resource_id: str
    payload_schema: str
    payload: dict
    producer_command_id: str
    generation_run_id: str | None


class RunOutboxPort(Protocol):
    """运行 outbox 端口。

    Task 4A 仅冻结 outbox 端口本身。Task 5B 实现 Postgres outbox publisher、
    consumer cursor 与 SSE 重放；Task 4A 不得依赖 Task 5B 的 publisher 或 SSE
    游标。
    """

    def enqueue(self, message: OutboxMessage, fencing_token: int) -> None: ...


class FakeRunOutbox:
    """Task 4A 测试用的内存 outbox；不发布、不重放。

    记录入队消息及对应的 fencing_token，供测试断言入队行为。
    """

    def __init__(self) -> None:
        """初始化，清空消息列表。"""
        self.messages: list[tuple[OutboxMessage, int]] = []

    def enqueue(self, message: OutboxMessage, fencing_token: int) -> None:
        """将一条消息入队（附带 fencing_token）。

        参数:
            message: 待入队的消息。
            fencing_token: 运行写 fence token。

        副作用: 向 ``self.messages`` 追加 (message, fencing_token) 记录。
        """
        self.messages.append((message, fencing_token))


class PostgresRunOutbox:
    """Postgres 运行 outbox：在当前事务内幂等入队 `RunOutboxRecord`。

    入队前校验 `fencing_token` 等于目标运行当前写入令牌（旧 token 拒绝）；
    按 `(resource_type, resource_id, producer_command_id)` 唯一键幂等，重复
    入队同一消息直接返回，不重复插入。
    """

    def __init__(self, session: Session) -> None:
        """用当前会话构造 outbox；事务边界由调用方管理。"""
        self._session = session

    def enqueue(self, message: OutboxMessage, fencing_token: int) -> None:
        """将一条消息写入 outbox（幂等）。

        参数：message 为待投递消息；fencing_token 为运行写 fence token。
        失败条件：generation_run_id 非空时运行不存在抛 RUN_STATE_CONFLICT；
        旧 fencing token 抛 RUN_LEASE_LOST（fail-closed）。
        """
        run_id = message.get("generation_run_id")
        if run_id is not None:
            run = self._session.get(GenerationRun, run_id)
            if run is None:
                raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
            if fencing_token != run.write_fencing_token:
                raise AppError("RUN_LEASE_LOST", "stale fencing token; cannot enqueue outbox")
        existing = self._session.execute(
            select(RunOutboxRecord).where(
                RunOutboxRecord.resource_type == message["resource_type"],
                RunOutboxRecord.resource_id == message["resource_id"],
                RunOutboxRecord.producer_command_id == message["producer_command_id"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(
            RunOutboxRecord(
                resource_type=message["resource_type"],
                resource_id=message["resource_id"],
                payload_schema=message["payload_schema"],
                payload=message["payload"],
                delivery_status="pending",
                attempt_count=0,
                next_attempt_at=None,
                last_error=None,
                producer_command_id=message["producer_command_id"],
                generation_run_id=run_id,
            )
        )
        self._session.flush()


class PostgresOutboxPublisher:
    """Postgres outbox 发布器：扫描待发布记录并投递，失败进入重试。

    发布在独立事务/调用中执行：业务事务（正文、版本、决策）提交成功后，
    outbox 投递失败绝不回滚已提交业务数据（发布失败不回滚业务事务）。
    投递使用行锁 `FOR UPDATE SKIP LOCKED` 防止并发重复投递；已 published 的
    记录不再处理（重复发布幂等，不产生重复业务效果）。

    发布租约：领取记录时置 `delivery_status=publishing` 并声明 `publisher_owner`
    + `publisher_lease_expires_at`。若发布进程在投递中崩溃，记录会卡在
    publishing；其他发布者看到租约过期后即可重新领取（超时恢复）。
    """

    def __init__(
        self,
        session: Session,
        deliver: Callable[[dict], None] | None = None,
        owner: str = "publisher",
        lease_seconds: int = 60,
        retry_delay_seconds: int = 5,
    ) -> None:
        """构造发布器。

        参数：
            session: 数据库会话。
            deliver: 投递回调（接收 outbox payload）；缺省为直接成功（模拟
                投递到已注册消费者/SSE 流）。测试可注入失败以验证重试语义。
            owner: 本发布者身份（用于发布租约 ownership）。
            lease_seconds: 单次发布租约时长；超时后其他发布者可重新领取。
            retry_delay_seconds: 投递失败后的重试间隔（next_attempt_at 推进）。
        """
        self._session = session
        self._deliver = deliver or (lambda payload: None)
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._retry_delay_seconds = retry_delay_seconds

    def publish(self, now: datetime, max_attempts: int = 5) -> int:
        """投递一批待发布（或崩溃后可重新领取）的 outbox 消息。

        参数：now 为判定租约/重试时间的当前时间；max_attempts 为单条消息最大
        尝试数。
        返回：本次实际处理的消息数量。

        领取范围：`pending|failed` 且 `next_attempt_at` 到期，或 `publishing`
        且发布租约已过期（崩溃记录可重新领取）。
        失败条件：投递回调抛错时该条记录置为 `failed` 并记录 `last_error`、
        递增 `attempt_count` 与 `next_attempt_at`（不抛给调用方，便于批量重试）。
        """
        reclaimable = (
            RunOutboxRecord.delivery_status.in_(("pending", "failed"))
            & (
                RunOutboxRecord.next_attempt_at.is_(None)
                | (RunOutboxRecord.next_attempt_at <= now)
            )
        ) | (
            (RunOutboxRecord.delivery_status == "publishing")
            & RunOutboxRecord.publisher_lease_expires_at.is_not(None)
            & (RunOutboxRecord.publisher_lease_expires_at <= now)
        )
        rows = self._session.execute(
            select(RunOutboxRecord)
            .where(reclaimable)
            .with_for_update(skip_locked=True)
            .order_by(RunOutboxRecord.created_at)
            .limit(100)
        ).scalars().all()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        processed = 0
        for row in rows:
            if row.attempt_count >= max_attempts:
                row.delivery_status = "failed"
                row.publisher_owner = None
                row.publisher_lease_expires_at = None
                continue
            row.delivery_status = "publishing"
            row.publisher_owner = self._owner
            row.publisher_lease_expires_at = lease_until
            row.attempt_count += 1
            try:
                self._deliver(row.payload)
                row.delivery_status = "published"
                row.publisher_owner = None
                row.publisher_lease_expires_at = None
                row.last_error = None
                row.next_attempt_at = None
            except Exception as exc:  # noqa: BLE001 - 投递失败进入重试，不阻断批量
                row.delivery_status = "failed"
                row.publisher_owner = None
                row.publisher_lease_expires_at = None
                row.last_error = str(exc)[:2000]
                # 失败后允许后续调度按重试间隔再次尝试。
                row.next_attempt_at = now + timedelta(seconds=self._retry_delay_seconds)
            processed += 1
        self._session.flush()
        return processed
