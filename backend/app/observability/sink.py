"""观测 sink：本地结构化日志 sink、LangSmith 降级组合 sink 与统一端口。

边界（Task 8）：
- ``ObservabilitySink`` 是统一观测端口，失败为 fail-open，绝不改变业务结果；
- ``LocalSink`` 只保存脱敏观测元数据与降级记录，不替代 ``RunEvent``、
  ``RunDecision``、正文版本或 checkpoint；所有 payload 先经 ``redact_payload``
  脱敏（默认 deny 内容），脱敏失败记录降级而非保存原文；
- ``FallbackSink`` 组合 LangSmith（primary）与本地 sink：primary 失败时只追加
  本地降级记录并继续，任何异常不上抛业务；同一事件幂等去重，重试不重复发送；
- 所有 sink 对同一 ``generation_run_id``/``agent_run_id`` 保持幂等。
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, cast

from .events import ErrorEvent, NodeEvent, RunContext, RunEndEvent, RunFeedback
from .redaction import REDACTION_VERSION, redact_payload

logger = logging.getLogger("novel.observability")


def _ctx(event: dict[str, Any]) -> dict[str, Any]:
    """返回事件的 run_context 字典（Task 4A/8 两种形状都可能缺失）。"""
    rc = event.get("run_context")
    return rc if isinstance(rc, dict) else {}


def _gen_of(event: dict[str, Any]) -> str:
    """取事件顶层的 generation_run_id（缺失时回退 run_context）。"""
    return str(event.get("generation_run_id") or _ctx(event).get("generation_run_id") or "")


def _agent_of(event: dict[str, Any]) -> str:
    """取事件顶层的 agent_run_id（缺失时回退 run_context）。"""
    return str(event.get("agent_run_id") or _ctx(event).get("agent_run_id") or "")


def _node_of(event: dict[str, Any]) -> str:
    """取节点名（node_name 优先，回退 node/agent_run_id）。"""
    return str(event.get("node_name") or event.get("node") or "")


class ObservabilitySink(Protocol):
    """统一观测端口；失败 fail-open，绝不改变业务结果。

    实现类（LocalSink/LangSmithSink/FallbackSink）内部自行处理脱敏、幂等与
    降级记录，方法不得向调用方抛出业务异常。
    """

    def on_run_start(self, run: RunContext) -> None: ...
    def on_node_end(self, event: NodeEvent) -> None: ...
    def on_error(self, event: ErrorEvent) -> None: ...
    def on_run_end(self, event: RunEndEvent) -> None: ...
    def record_feedback(self, feedback: RunFeedback) -> None: ...


class LocalSink:
    """本地结构化日志 sink：脱敏后写入日志并保留内存记录（测试用）。

    所有事件先经 ``redact_payload`` 脱敏；以 ``(kind, 去重键)`` 幂等去重；
    记录或脱敏失败只追加 ``degraded``，绝不抛出。
    """

    def __init__(self, *, capture_content: bool = False, log_to: logging.Logger | None = None) -> None:
        """构造本地 sink。

        参数：capture_content 为内容采集开关（必须经 ``content_capture_allowed``
        校验环境后传入）；log_to 为可注入的日志器（默认 novel.observability）。
        """
        self._capture = capture_content
        self._log = log_to or logger
        self.records: list[dict] = []
        self.degraded: list[dict] = []
        self._seen: set[str] = set()

    # ------------------------------------------------------------------
    # ObservabilitySink 实现
    # ------------------------------------------------------------------

    def on_run_start(self, run: RunContext) -> None:
        """记录运行开始与脱敏运行元数据（不创建 GenerationRun，不承担入队）。"""
        self._record("run_start", f"run_start:{run['generation_run_id']}", cast(dict[str, Any], run))

    def on_node_end(self, event: NodeEvent) -> None:
        """记录节点耗时、输入版本、输出摘要与 token 用量（输出摘要不含完整内容）。"""
        ev = cast(dict[str, Any], event)
        self._record("node_end", f"node_end:{_gen_of(ev)}:{_agent_of(ev)}:{_node_of(ev)}", ev)

    def on_error(self, event: ErrorEvent) -> None:
        """记录稳定错误码、可重试性与观测降级标记；sink 错误不能覆盖真实业务错误。"""
        ev = cast(dict[str, Any], event)
        self._record("error", f"error:{_gen_of(ev)}:{_node_of(ev)}:{ev['error_code']}", ev)

    def on_run_end(self, event: RunEndEvent) -> None:
        """记录已持久化的运行终态与最终决策（不能把中间输出写成终态）。"""
        self._record("run_end", f"run_end:{event['generation_run_id']}", cast(dict[str, Any], event))

    def record_feedback(self, feedback: RunFeedback) -> None:
        """记录作者反馈哈希与目标（默认不保存反馈正文）。"""
        self._record(
            "feedback",
            f"feedback:{feedback['generation_run_id']}:{feedback['target']}",
            cast(dict[str, Any], feedback),
        )

    # ------------------------------------------------------------------
    # 内部：脱敏 + 幂等 + 降级
    # ------------------------------------------------------------------

    def _record(self, kind: str, dedup_key: str, event: dict[str, Any]) -> None:
        """脱敏后记录一条事件；同一去重键只记录一次；失败追加降级。"""
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)
        try:
            safe = redact_payload(event, capture_content=self._capture)
        except Exception as exc:  # fail-closed：脱敏失败绝不保存原文
            self.degraded.append(
                {
                    "kind": kind,
                    "dedup_key": dedup_key,
                    "reason": f"redaction_failed:{type(exc).__name__}",
                    "redaction_version": REDACTION_VERSION,
                }
            )
            return
        record = {"kind": kind, "redaction_version": REDACTION_VERSION, **safe}
        self.records.append(record)
        try:
            self._log.info("observability %s key=%s", kind, dedup_key)
        except Exception:  # 日志失败也 fail-open
            pass

    def record_degradation(self, entry: dict) -> None:
        """追加一条降级记录（脱敏后保存；调用方不得传入原文）。"""
        try:
            safe = redact_payload(entry, capture_content=False)
        except Exception as exc:
            safe = {"kind": "degradation", "reason": f"redaction_failed:{type(exc).__name__}"}
        self.degraded.append(safe)


class FallbackSink:
    """LangSmith 优先、本地降级的组合 sink（统一端口实现）。

    每个事件按 ``(kind, 去重键)`` 幂等路由：先调用 primary（LangSmith），再调用
    local；primary 失败时通过 ``on_degraded`` 回调把降级记录写入本地，任何异常
    都不上抛业务（fail-open）。业务事务结果不受观测失败影响。
    """

    def __init__(self, primary: ObservabilitySink | None, local: LocalSink) -> None:
        """构造组合 sink。

        参数：primary 为 LangSmith sink（可为 None，表示未启用）；local 为本地
        sink（记录脱敏元数据与降级）。
        """
        self._primary = primary
        self._local = local
        self._seen: set[str] = set()
        # primary 的降级回调：写入本地降级记录（本身不得抛）。
        if primary is not None and hasattr(primary, "degraded"):
            primary.on_degraded = self._local.record_degradation  # type: ignore[attr-defined]

    def _route(self, kind: str, dedup_key: str, event: dict[str, Any]) -> None:
        """幂等路由到 primary 与 local；任何失败只降级不上抛。"""
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)
        errors: list[str] = []
        if self._primary is not None:
            try:
                getattr(self._primary, kind)(event)
            except Exception as exc:  # fail-open：LangSmith 失败不阻断业务
                errors.append(f"{type(self._primary).__name__}: {exc}")
        try:
            getattr(self._local, kind)(event)
        except Exception as exc:  # 本地 sink 自身也应 fail-open；双保险
            errors.append(f"LocalSink: {exc}")
        if errors:
            try:
                self._local.record_degradation(
                    {
                        "kind": kind,
                        "dedup_key": dedup_key,
                        "reason": "; ".join(errors),
                        "redaction_version": REDACTION_VERSION,
                    }
                )
            except Exception:
                pass

    def on_run_start(self, run: RunContext) -> None:
        """记录运行开始（组合路由）。"""
        self._route("on_run_start", f"run_start:{run['generation_run_id']}", cast(dict[str, Any], run))

    def on_node_end(self, event: NodeEvent) -> None:
        """记录节点结束（组合路由）。"""
        ev = cast(dict[str, Any], event)
        self._route("on_node_end", f"node_end:{_gen_of(ev)}:{_agent_of(ev)}:{_node_of(ev)}", ev)

    def on_error(self, event: ErrorEvent) -> None:
        """记录错误（组合路由）。"""
        ev = cast(dict[str, Any], event)
        self._route("on_error", f"error:{_gen_of(ev)}:{_node_of(ev)}:{ev['error_code']}", ev)

    def on_run_end(self, event: RunEndEvent) -> None:
        """记录运行终态（组合路由）。"""
        self._route("on_run_end", f"run_end:{event['generation_run_id']}", cast(dict[str, Any], event))

    def record_feedback(self, feedback: RunFeedback) -> None:
        """记录作者反馈（组合路由）。"""
        self._route(
            "record_feedback",
            f"feedback:{feedback['generation_run_id']}:{feedback['target']}",
            cast(dict[str, Any], feedback),
        )


__all__ = [
    "ObservabilitySink",
    "LocalSink",
    "FallbackSink",
]
