"""运行 API：运行创建、查询、作者决策、暂停恢复与 SSE 事件重放。

Task 5B 边界：HTTP 请求只负责幂等 claim、写入运行记录/决策/事件/outbox，
不在请求线程执行 LangGraph；`target=canon` 由通用入口拒绝（CANON_NOT_ENABLED），
Canon 专用入口留给 Task 5C。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db.session import get_session_factory
from ..domain.idempotency import fingerprint
from ..errors import AppError
from ..services.generation_runs import (
    replay_run_events,
    resume_paused_run,
    run_snapshot,
    start_generation_run,
    submit_run_decision,
)
from .commands import execute_command
from .deps import get_actor_id, get_db, get_idempotency_key
from .schemas import (
    DecisionRequest,
    DecisionResponse,
    ResumeRequest,
    RunCreateRequest,
    RunSnapshot,
)

router = APIRouter(prefix="/api", tags=["runs"])

_LAST_EVENT_ID_RE = re.compile(r"^(?:[^:]+:)?(\d+)$")

# SSE 轮询间隔：连接建立后持续轮询新事件；测试用 1 秒保证"先连接、后产生事件"
# 能较快被推送，生产可调大以降低数据库压力。
_SSE_POLL_INTERVAL = 1.0


def _parse_last_event_id(last_event_id: str | None) -> int:
    """把 SSE `Last-Event-ID`（形如 `run-id:42` 或 `42`）解析为序号。

    失败条件：无法解析时抛 COMMAND_CONTEXT_MISMATCH。
    """
    if not last_event_id:
        return 0
    match = _LAST_EVENT_ID_RE.match(last_event_id.strip())
    if match is None:
        raise AppError("COMMAND_CONTEXT_MISMATCH", "invalid Last-Event-ID")
    return int(match.group(1))


@router.post("/chapters/{chapter_id}/runs", response_model=RunSnapshot)
def post_chapter_run(
    chapter_id: str,
    body: RunCreateRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> RunSnapshot:
    """创建章节运行（强制 run_scope=chapter，不允许客户端用 body 绕过边界）。"""
    if body.run_scope != "chapter":
        raise AppError("COMMAND_CONTEXT_MISMATCH", "chapter runs require run_scope=chapter")
    if body.scene_id is not None:
        raise AppError("COMMAND_CONTEXT_MISMATCH", "chapter runs must not carry scene_id")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        return (
            start_generation_run(
                session, actor_id, chapter_id, body, manual_command_id or "", idempotency_key
            ),
            manual_command_id,
        )

    return RunSnapshot(
        **execute_command(
            session,
            f"chapter:{chapter_id}",
            "run_start",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.post("/scenes/{scene_id}/runs", response_model=RunSnapshot)
def post_scene_run(
    scene_id: str,
    body: RunCreateRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> RunSnapshot:
    """创建场景运行（强制 run_scope=scene，target_id 即 URL 场景 id）。"""
    if body.run_scope != "scene":
        raise AppError("COMMAND_CONTEXT_MISMATCH", "scene runs require run_scope=scene")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        return (
            start_generation_run(
                session, actor_id, scene_id, body, manual_command_id or "", idempotency_key
            ),
            manual_command_id,
        )

    return RunSnapshot(
        **execute_command(
            session,
            f"scene:{scene_id}",
            "run_start",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.get("/runs/{run_id}", response_model=RunSnapshot)
def get_run(
    run_id: str,
    session: Session = Depends(get_db),
) -> RunSnapshot:
    """返回运行快照；不把中间事件当作 accepted 版本。"""
    return RunSnapshot(**run_snapshot(session, run_id))


@router.post("/runs/{run_id}/decisions", response_model=DecisionResponse)
def post_run_decision(
    run_id: str,
    body: DecisionRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> DecisionResponse:
    """作者决策：claim 命令后取得 API command fence，CAS 版本并写入记录。"""
    if body.idempotency_key != idempotency_key:
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "decision idempotency_key must match the Idempotency-Key header",
        )
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        result = submit_run_decision(
            session, actor_id, run_id, body, manual_command_id or ""
        )
        return result, manual_command_id

    return DecisionResponse(
        **execute_command(
            session,
            f"run:{run_id}",
            "run_decision",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.post("/runs/{run_id}/resume", response_model=DecisionResponse)
def post_run_resume(
    run_id: str,
    body: ResumeRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> DecisionResponse:
    """恢复 paused 运行：校验暂停原因与运行版本后从原 checkpoint 继续。"""
    if body.idempotency_key != idempotency_key:
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "resume idempotency_key must match the Idempotency-Key header",
        )
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        result = resume_paused_run(
            session, actor_id, run_id, body, manual_command_id or ""
        )
        return result, manual_command_id

    return DecisionResponse(
        **execute_command(
            session,
            f"run:{run_id}",
            "run_resume",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.get("/runs/{run_id}/events")
def get_run_events(
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    """SSE 事件流：先按 `Last-Event-ID` 重放已持久化事件，再持续推送新事件。

    连接建立后进入轮询循环：每隔 `_SSE_POLL_INTERVAL` 秒读取一次新产生的
    RunEvent 并实时推送，因此"先连接、后产生事件"也能收到新事件；没有新事件
    时发送 heartbeat 保持连接。事件 payload 已脱敏；客户端按事件 `id` 去重，
    重放核心逻辑（replay_run_events）独立可测。
    """
    after = _parse_last_event_id(last_event_id)
    events = replay_run_events(session, run_id, after_sequence=after)

    async def generator() -> Any:
        last = after
        for event in events:
            yield _sse_frame(event)
            last = event["sequence"]
        while True:
            # 用独立会话轮询新事件（避免在事件循环上复用请求会话做同步查询）。
            new_events = await asyncio.to_thread(_poll_new_events, run_id, last)
            if new_events:
                for event in new_events:
                    yield _sse_frame(event)
                    last = event["sequence"]
            else:
                yield ": heartbeat\n\n"
                await asyncio.sleep(_SSE_POLL_INTERVAL)

    return StreamingResponse(generator(), media_type="text/event-stream")


def _poll_new_events(run_id: str, after_sequence: int) -> list[dict]:
    """在独立会话中读取运行在 `after_sequence` 之后的新事件（供 SSE 轮询）。"""
    factory = get_session_factory()
    with factory() as s:
        return replay_run_events(s, run_id, after_sequence=after_sequence)


def _sse_frame(event: dict) -> str:
    """把 RunEventEnvelope 字典格式化为 SSE 帧。"""
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )
