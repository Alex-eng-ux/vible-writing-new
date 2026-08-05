"""生产观测装配：把自动埋点接到实际 Worker/运行入口。

设计（Task 8 生产 wiring，不修改正文/Canon/版本/运行状态机契约）：
- ``make_wiring`` 依据配置构建生产 sink：LangSmith 未启用或缺少
  ``LANGSMITH_API_KEY`` 时只用本地 sink（不依赖真实 API Key）；
- ``ObservabilityWiring.traced(graph)`` 是幂等包装入口：为图的 ``HookRegistry``
  注册 ``TraceHook``（覆盖场景/章节/Canon 三图全部 Agent 节点），再把图包上
  ``GraphObservability``；同一图重复调用只注册/包装一次（防重复埋点与重复包装），
  已包装的图原样返回；
- ``traced_call`` / ``record_feedback`` 是模型/工具调用与作者反馈的生产入口，
  分别委托 ``trace_call`` / ``record_author_feedback``（sink 失败 fail-open，
  不影响业务、不导致命令重复执行）；
- 进程级默认 wiring（``get_default_wiring``）供服务层在未显式注入 sink 时使用。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypeVar

from app.config import AppConfig, get_config
from app.observability.events import RunFeedback
from app.observability.langsmith_sink import LangSmithSink
from app.observability.redaction import content_capture_allowed
from app.observability.sink import FallbackSink, LocalSink, ObservabilitySink
from app.observability.trace import (
    GraphObservability,
    RedactionHook,
    record_author_feedback,
    register_trace,
    trace_call,
)

T = TypeVar("T")

# 图/注册表上的幂等标记属性名：防止同一图被重复包装、同一注册表被重复注册钩子。
_WRAPPER_ATTR = "_observability_wrapper"
_TRACE_ATTR = "_observability_trace_registered"


class ObservabilityWiring:
    """生产观测装配：持有 sink，并以幂等方式接入图/模型调用/反馈入口。

    属性：
        sink: 组合 sink（LangSmith 优先 + 本地降级，或纯本地）。
        environment: 运行环境（development/evaluation/production）。
        local: 底层本地 sink（测试断言用；纯本地 sink 时为同一对象）。
    """

    def __init__(
        self,
        sink: ObservabilitySink,
        *,
        environment: str = "development",
        redaction: RedactionHook | None = None,
        local: LocalSink | None = None,
    ) -> None:
        """构造生产装配。

        参数：sink 为观测 sink；environment 为运行环境；redaction 为脱敏适配器
        （缺省新建，默认 deny 内容）；local 为底层本地 sink（缺省从 sink 推断）。
        """
        self.sink = sink
        self.environment = environment
        self._redaction = redaction or RedactionHook()
        self.local = local if local is not None else (sink if isinstance(sink, LocalSink) else None)

    def traced(self, graph: Any) -> GraphObservability:
        """把图包上观测并注册 TraceHook（幂等：防重复包装/重复埋点）。

        参数：graph 为已构建的图（SceneGraph/ChapterGraph/CanonGraph，或已是
        GraphObservability 包装器）。
        返回：GraphObservability 包装器；同一图重复调用返回同一实例。

        约束：已包装的图原样返回；同一 HookRegistry 只注册一次 TraceHook；
        图/注册表不可写标记属性时也保证不重复注册（兜底仍以 sink 幂等去重）。
        """
        if isinstance(graph, GraphObservability):
            return graph
        existing = getattr(graph, _WRAPPER_ATTR, None)
        if existing is not None:
            return existing
        registry = graph.registry
        if not getattr(registry, _TRACE_ATTR, False):
            register_trace(
                registry,
                self.sink,
                environment=self.environment,
                redaction=self._redaction,
            )
            try:
                setattr(registry, _TRACE_ATTR, True)
            except (AttributeError, TypeError):
                pass
        wrapper = GraphObservability(
            graph,
            self.sink,
            environment=self.environment,
            redaction=self._redaction,
        )
        try:
            setattr(graph, _WRAPPER_ATTR, wrapper)
        except (AttributeError, TypeError):
            pass
        return wrapper

    def traced_call(
        self,
        *,
        name: str,
        kind: Literal["llm", "tool"],
        generation_run_id: str,
        agent_run_id: str,
        node_name: str,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """返回模型/工具调用的生产包装入口（委托 ``trace_call``）。

        参数：name 为调用名；kind 为 llm|tool；generation_run_id / agent_run_id
        为所属运行与 agent；node_name 为父节点名。
        返回：与 ``trace_call`` 一致的装饰器，业务返回值/异常语义不变。
        """
        return trace_call(
            self.sink,
            name=name,
            kind=kind,
            generation_run_id=generation_run_id,
            agent_run_id=agent_run_id,
            node_name=node_name,
            redaction=self._redaction,
        )

    def record_feedback(
        self,
        *,
        generation_run_id: str,
        target: str,
        decision: str,
        content: str = "",
    ) -> RunFeedback:
        """记录作者反馈（默认只存内容哈希，正文不落库；sink 失败 fail-open）。

        参数：generation_run_id 为运行 id；target 为反馈目标；decision 为决策
        类型；content 为反馈原文（仅用于计算哈希，绝不写入事件）。
        返回：构造的 RunFeedback 事件。
        """
        return record_author_feedback(
            self.sink,
            generation_run_id=generation_run_id,
            target=target,
            decision=decision,
            content=content,
        )


def make_wiring(cfg: AppConfig | None = None) -> ObservabilityWiring:
    """按配置构建生产观测装配（LangSmith 未启用/无 Key 时只用本地 sink）。

    参数：cfg 为应用配置；缺省时重新读取环境变量（含 fail-closed 校验）。
    返回：生产 wiring。LangSmith 仅在 ``langsmith_tracing`` 与
    ``langsmith_api_key`` 同时配置时启用；``langsmith_capture_content`` 经
    ``content_capture_allowed`` 环境门控（生产环境即使开关为真也由 config 拒绝）。
    """
    cfg = cfg or get_config()
    capture = content_capture_allowed(cfg.app_env, cfg.langsmith_capture_content)
    local = LocalSink(capture_content=capture)
    primary: LangSmithSink | None = None
    if cfg.langsmith_tracing and cfg.langsmith_api_key:
        primary = LangSmithSink(project=cfg.langsmith_project or None)
    return ObservabilityWiring(
        sink=FallbackSink(primary, local),
        environment=cfg.app_env,
        local=local,
    )


_default_wiring: ObservabilityWiring | None = None


def get_default_wiring() -> ObservabilityWiring:
    """返回进程级默认生产 wiring（惰性构建并缓存）。

    返回：由当前配置构建的 wiring；未显式注入 sink 的服务层使用该实例。
    约束：测试如需隔离，请通过 ``make_wiring`` 或直接注入显式 sink，不要依赖
    进程级缓存。
    """
    global _default_wiring
    if _default_wiring is None:
        _default_wiring = make_wiring()
    return _default_wiring


__all__ = [
    "ObservabilityWiring",
    "make_wiring",
    "get_default_wiring",
]
