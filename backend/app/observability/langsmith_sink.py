"""LangSmith sink：把脱敏 Trace/评测数据发送到 LangSmith。

边界（Task 8）：
- ``client`` 为 langsmith.Client 兼容对象（实现 ``create_run``）；为 None 表示
  未启用（LangSmith 关闭），跳过发送且不算错误；
- 网络错误、超时、API 配额（429）或服务错误一律捕获并转换为本地降级记录，
  绝不向业务抛出（fail-open）；降级分类为 timeout_or_network / quota /
  service_error，不保存原始异常消息（避免任何内容泄漏）；
- 同一 ``(kind, run_id, agent/node)`` 幂等：同一事件重试不重复调用外部 API；
- 发送内容一律经 ``redact_payload`` 脱敏（LangSmith 永不接收原文；完整采集仅
  由显式授权的开发/评测环境开启，且经 LocalSink 而不是本 sink 发送）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from .events import ErrorEvent, NodeEvent, RunContext, RunEndEvent, RunFeedback
from .redaction import REDACTION_VERSION, redact_payload

# langsmith.Client.create_run 的兼容协议（真实 client 也满足该签名）。
# client.create_run(name, inputs, run_type, *, project_name=None, **kwargs) -> None
_ClientLike = Any


def _ctx(event: Any) -> dict[str, Any]:
    """返回事件的 run_context 字典（Task 4A/8 两种形状都可能缺失）。"""
    rc = event.get("run_context")
    return rc if isinstance(rc, dict) else {}


def _gen_of(event: Any) -> str:
    """取事件顶层的 generation_run_id（缺失时回退 run_context）。"""
    return str(event.get("generation_run_id") or _ctx(event).get("generation_run_id") or "")


def _agent_of(event: Any) -> str:
    """取事件顶层的 agent_run_id（缺失时回退 run_context）。"""
    return str(event.get("agent_run_id") or _ctx(event).get("agent_run_id") or "")


def _node_of(event: Any) -> str:
    """取节点名（node_name 优先，回退 node/agent_run_id）。"""
    return str(event.get("node_name") or event.get("node") or "")


def classify_sink_error(exc: Exception) -> str:
    """把 sink 异常分类为稳定降级类别（不保存原始消息）。

    参数：exc 为捕获的异常。
    返回：timeout_or_network / quota / service_error 之一。
    """
    if isinstance(exc, TimeoutError | ConnectionError | OSError):
        return "timeout_or_network"
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "429" in text or "quota" in lowered or "rate" in lowered or "limit" in lowered:
        return "quota"
    return "service_error"


class LangSmithSink:
    """LangSmith 观测 sink（统一端口实现，fail-open + 幂等）。"""

    def __init__(
        self,
        *,
        client: _ClientLike | None = None,
        project: str | None = None,
        on_degraded: Callable[[dict], None] | None = None,
    ) -> None:
        """构造 LangSmith sink。

        参数：client 为 langsmith.Client 兼容对象（None 表示未启用）；project
        为 LangSmith 项目名；on_degraded 为降级回调（FallbackSink 注入本地降级
        记录；回调本身不得抛）。
        """
        self._client = client
        self._project = project
        self.on_degraded = on_degraded
        self.degraded: list[dict] = []
        self.send_attempts: int = 0
        self._seen: set[str] = set()

    # ------------------------------------------------------------------
    # ObservabilitySink 实现
    # ------------------------------------------------------------------

    def on_run_start(self, run: RunContext) -> None:
        """记录运行开始（脱敏元数据）。"""
        self._send("novel:run_start", f"run_start:{run['generation_run_id']}", cast(dict[str, Any], run))

    def on_node_end(self, event: NodeEvent) -> None:
        """记录节点结束（脱敏节点/版本来源/token 摘要）。"""
        ev: dict[str, Any] = cast(dict[str, Any], event)
        self._send("novel:node_end", f"node_end:{_gen_of(ev)}:{_agent_of(ev)}:{_node_of(ev)}", ev)

    def on_error(self, event: ErrorEvent) -> None:
        """记录错误（脱敏错误码/可重试性/降级标记）。"""
        ev: dict[str, Any] = cast(dict[str, Any], event)
        self._send("novel:error", f"error:{_gen_of(ev)}:{_node_of(ev)}:{ev['error_code']}", ev)

    def on_run_end(self, event: RunEndEvent) -> None:
        """记录运行终态（脱敏状态/最终决策/耗时/token 摘要）。"""
        self._send("novel:run_end", f"run_end:{event['generation_run_id']}", cast(dict[str, Any], event))

    def record_feedback(self, feedback: RunFeedback) -> None:
        """记录作者反馈（脱敏哈希与目标）。"""
        self._send(
            "novel:feedback",
            f"feedback:{feedback['generation_run_id']}:{feedback['target']}",
            cast(dict[str, Any], feedback),
        )

    # ------------------------------------------------------------------
    # 内部：脱敏 + 幂等 + 降级
    # ------------------------------------------------------------------

    def _send(self, name: str, dedup_key: str, inputs: dict[str, Any]) -> None:
        """幂等发送脱敏负载；未启用/失败均不抛出。"""
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)
        if self._client is None:
            return  # LangSmith 未启用：跳过（配置选择，不是失败）
        self.send_attempts += 1
        try:
            redacted = redact_payload(inputs, capture_content=False)
            self._client.create_run(
                name=name,
                inputs=redacted,
                run_type="chain",
                project_name=self._project,
            )
        except Exception as exc:  # 网络/超时/配额/服务错误一律降级
            self._degrade(name, dedup_key, classify_sink_error(exc))

    def _degrade(self, name: str, dedup_key: str, reason: str) -> None:
        """记录降级事件并回调（回调本身不得抛）。"""
        entry: dict = {
            "kind": name,
            "dedup_key": dedup_key,
            "reason": reason,
            "redaction_version": REDACTION_VERSION,
        }
        self.degraded.append(entry)
        if self.on_degraded is not None:
            try:
                self.on_degraded(entry)
            except Exception:
                pass


__all__ = [
    "LangSmithSink",
    "classify_sink_error",
]
