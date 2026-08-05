"""运行事件（run event）的发射与净化。

本模块负责运行产生的业务事件：
- ``sanitize_payload`` 剔除/脱敏可能含未净化正文或提示词的敏感键（prompt、content、
  draft_text、prose、text 等），防止敏感内容进入事件下游；
- ``FakeRunEventEmitter`` 是 Task 4A 测试用的内存发射器，要求非负 fencing_token
  （运行写 fence），业务事件 fail-closed：缺少 token 时抛错而非发射。
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GenerationRun, RunEvent, RunEventConsumerCursor
from app.errors import AppError

# Payload keys that must never be emitted because they may contain unsanitized
# prose or prompts.
_SENSITIVE_KEYS = frozenset({"prompt", "content", "draft_text", "prose", "text"})


def sanitize_payload(payload: dict) -> dict:
    """返回一份去除或脱敏敏感键的负载副本。

    参数:
        payload: 原始事件负载。

    返回:
        新字典；凡 key 命中 ``_SENSITIVE_KEYS`` 的键值被替换为 ``"[redacted]"``，
        其余键原样保留。原字典不被修改。
    """
    out: dict = {}
    for key, value in payload.items():
        if key in _SENSITIVE_KEYS:
            out[key] = "[redacted]"
        else:
            out[key] = value
    return out


class RunEventEmitter(Protocol):
    """运行事件发射器端口，供依赖方做类型约束与测试替身。"""

    def emit(
        self,
        generation_run_id: str,
        event_type: str,
        payload: dict,
        fencing_token: int,
        producer_command_id: str | None = None,
    ) -> None: ...


class FakeRunEventEmitter:
    """Task 4A 测试用的内存发射器。

    要求非负的 fencing_token（运行写 fence）；负载在存储前会被净化。业务事件
    fail-closed：缺少 token 时抛错而非发射。
    """

    def __init__(self) -> None:
        """初始化，清空事件列表。"""
        self.events: list[dict] = []

    def emit(
        self,
        generation_run_id: str,
        event_type: str,
        payload: dict,
        fencing_token: int,
        producer_command_id: str | None = None,
    ) -> None:
        """发射一条运行事件（净化后存入内存）。

        参数:
            generation_run_id: 目标运行 ID。
            event_type: 事件类型。
            payload: 事件负载（发射前会被净化）。
            fencing_token: 运行写 fence token，必须非负。
            producer_command_id: 可选的产生该事件的命令 ID。

        副作用: 向 ``self.events`` 追加一条事件记录，sequence 按顺序递增。
        失败条件: fencing_token 为 None 或为负时抛出 ``RUN_LEASE_LOST``
        （fail-closed，不发射）。
        """
        if fencing_token is None or fencing_token < 0:
            raise AppError("RUN_LEASE_LOST", "fencing_token is required for run events")
        self.events.append(
            {
                "generation_run_id": generation_run_id,
                "event_type": event_type,
                "payload": sanitize_payload(payload),
                "fencing_token": fencing_token,
                "producer_command_id": producer_command_id,
                "sequence": len(self.events) + 1,
            }
        )


class RunEventEmitterPort:
    """对任意发射器强制 fencing 与净化的端口包装器。

    统一在入口处对负载做净化，并把 fencing 校验交给底层发射器负责。
    """

    def __init__(self, emitter: RunEventEmitter) -> None:
        """初始化。

        参数:
            emitter: 底层事件发射器。
        """
        self._emitter = emitter

    def emit(
        self,
        generation_run_id: str,
        event_type: str,
        payload: dict,
        fencing_token: int,
        producer_command_id: str | None = None,
    ) -> None:
        """发射一条净化后的运行事件。

        参数:
            generation_run_id: 目标运行 ID。
            event_type: 事件类型。
            payload: 事件负载（发射前会被净化）。
            fencing_token: 运行写 fence token。
            producer_command_id: 可选的产生该事件的命令 ID。

        副作用: 将净化后的负载交给底层发射器；fencing 校验由底层发射器决定（如
        ``FakeRunEventEmitter`` 会 fail-closed）。
        """
        self._emitter.emit(
            generation_run_id,
            event_type,
            sanitize_payload(payload),
            fencing_token,
            producer_command_id,
        )


class PostgresRunEventStore:
    """Postgres 持久化运行事件存储：SSE 重放与 outbox 的数据源。

    事件序号在目标运行行锁内分配（绝不能由并发写入者直接取最大值加一）；
    写入前校验 `fencing_token` 等于运行当前写入令牌，旧 token 拒绝（fail-closed）。
    消费者游标（`RunEventConsumerCursor`）按 `consumer_name + stream_key` 保存
    最后确认序号，供 outbox 消费者去重与 SSE 重放定位。
    """

    def __init__(self, session: Session) -> None:
        """用当前会话构造事件存储；事务边界由调用方管理。"""
        self._session = session

    def emit(
        self,
        generation_run_id: str,
        event_type: str,
        payload: dict,
        fencing_token: int,
        producer_command_id: str | None = None,
    ) -> RunEvent:
        """在运行行锁内分配序号并写入一条持久化事件。

        参数：generation_run_id 为目标运行 id；event_type 为事件类型；payload 为
        事件负载（写入前净化）；fencing_token 为运行写 fence token；
        producer_command_id 为可选的产生该事件的命令 id。
        返回：新建的 `RunEvent` 行。

        失败条件：运行不存在抛 RUN_STATE_CONFLICT；fencing_token 不等于运行当前
        写入令牌抛 RUN_LEASE_LOST（旧 token 不得写入，fail-closed）。
        """
        run = self._session.execute(
            select(GenerationRun)
            .where(GenerationRun.id == generation_run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
        if fencing_token != run.write_fencing_token:
            raise AppError("RUN_LEASE_LOST", "stale fencing token; cannot write run events")
        max_seq = self._session.execute(
            select(RunEvent.sequence)
            .where(RunEvent.generation_run_id == generation_run_id)
            .order_by(RunEvent.sequence.desc())
            .limit(1)
        ).scalar_one_or_none()
        sequence = (max_seq or 0) + 1
        event = RunEvent(
            generation_run_id=generation_run_id,
            sequence=sequence,
            event_type=event_type,
            payload=sanitize_payload(payload),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def max_sequence(self, generation_run_id: str) -> int:
        """返回该运行已持久化的最大事件序号；没有事件时为 0。"""
        value = self._session.execute(
            select(RunEvent.sequence)
            .where(RunEvent.generation_run_id == generation_run_id)
            .order_by(RunEvent.sequence.desc())
            .limit(1)
        ).scalar_one_or_none()
        return value or 0

    def list_events(
        self, generation_run_id: str, after_sequence: int = 0, limit: int | None = None
    ) -> list[RunEvent]:
        """按序号升序返回该运行的事件；只返回序号大于 after_sequence 的事件。

        供 SSE `Last-Event-ID` 重放使用：客户端传最后序号，服务端从下一序号补发。
        """
        stmt = (
            select(RunEvent)
            .where(
                RunEvent.generation_run_id == generation_run_id,
                RunEvent.sequence > after_sequence,
            )
            .order_by(RunEvent.sequence)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self._session.execute(stmt).scalars().all())

    def advance_consumer_cursor(
        self,
        consumer_name: str,
        stream_key: str,
        last_sequence: int,
        last_event_id: str | None,
    ) -> RunEventConsumerCursor:
        """推进（创建或更新）消费者游标；先持久化成功游标再确认 outbox。

        参数：consumer_name 为消费者名；stream_key 为订阅的运行或事件流；
        last_sequence 为已确认的最大序号；last_event_id 为最后确认事件 id。
        返回：更新后的游标行。
        """
        cursor = self._session.execute(
            select(RunEventConsumerCursor).where(
                RunEventConsumerCursor.consumer_name == consumer_name,
                RunEventConsumerCursor.stream_key == stream_key,
            )
        ).scalar_one_or_none()
        if cursor is None:
            cursor = RunEventConsumerCursor(
                consumer_name=consumer_name,
                stream_key=stream_key,
                last_sequence=last_sequence,
                last_event_id=last_event_id,
            )
            self._session.add(cursor)
        else:
            cursor.last_sequence = last_sequence
            cursor.last_event_id = last_event_id
        self._session.flush()
        return cursor

    def get_consumer_cursor(
        self, consumer_name: str, stream_key: str
    ) -> RunEventConsumerCursor | None:
        """读取指定消费者的游标；不存在返回 None。"""
        return self._session.execute(
            select(RunEventConsumerCursor).where(
                RunEventConsumerCursor.consumer_name == consumer_name,
                RunEventConsumerCursor.stream_key == stream_key,
            )
        ).scalar_one_or_none()
