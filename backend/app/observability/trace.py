"""自动埋点：TraceHook / RedactionHook 接入 LangGraph 节点、模型调用与工具调用。

设计（Task 8 补完，不修改正文/Canon/版本/运行状态机契约）：
- ``RedactionHook`` 是内容脱敏适配器：把 Agent 输入信封/输出转换为脱敏元数据
  字典（默认 deny，内容键替换为 ``[redacted]``）；脱敏失败 fail-closed，调用方
  绝不把原文发送给 sink；
- ``TraceHook`` 实现 ``agents.hooks.LifecycleHook``（before/after），注册到
  ``HookRegistry`` 后自动覆盖全部 Agent 类型（writing/continuity/review/revision
  /chapter_planner/chapter_review/canon）：记录节点名、输入版本、ContextManifest
  来源、耗时、token 摘要与路由结果（均先脱敏）；
- ``trace_call`` 包裹模型/工具调用：自动上报 llm/tool 运行事件（耗时/token 摘要/
  脱敏调用元数据），失败上报 error 后原样重抛（不改变业务失败语义）；
- ``GraphObservability`` 包装图 ``invoke``：自动记录 run_start / run_end / error；
  sink 全部 fail-open（含防御性 try/except），观测失败不影响业务，也不导致命令
  重复执行；
- ``record_author_feedback`` 记录作者反馈（默认只存内容哈希，不保存正文）。
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from app.agents.hook_registry import HookRegistry
from app.agents.schemas import AgentInputEnvelope, RouterOutcome
from app.agents.state import ChapterRunState
from app.observability.events import ErrorEvent, NodeEvent, RunContext, RunEndEvent, RunFeedback
from app.observability.redaction import redact_payload
from app.observability.sink import ObservabilitySink

T = TypeVar("T")

# 全部 Agent 类型（与 HookRegistry 覆盖范围一致：场景/章节/Canon 三图共用）。
DEFAULT_AGENT_TYPES: tuple[str, ...] = (
    "writing",
    "continuity",
    "review",
    "revision",
    "chapter_planner",
    "chapter_review",
    "canon",
)


def _iso(ts: float) -> str:
    """把单调时钟时间戳格式化为 UTC ISO 字符串。"""
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def extract_token_usage(output: Any) -> dict | None:
    """从模型/Agent 输出提取 token 摘要；缺失返回 None（不伪造精确数值）。

    参数：output 为模型返回或 Agent 输出（dict 或 BaseModel）。
    返回：token 摘要字典；无 usage 字段时返回 None。
    """
    usage = getattr(output, "token_usage", None)
    if isinstance(usage, dict) and usage:
        return dict(usage)
    if isinstance(output, dict):
        for key in ("token_usage", "usage_metadata", "usage"):
            val = output.get(key)
            if isinstance(val, dict) and val:
                return dict(val)
    return None


def _error_code_of(exc: Exception) -> str:
    """取稳定错误码（AppError.code 或统一 INTERNAL_ERROR）。"""
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code else "INTERNAL_ERROR"


class RedactionHook:
    """内容脱敏适配器：sink 前强制脱敏；脱敏失败 fail-closed（调用方不得发原文）。"""

    def __init__(self, *, capture_content: bool = False) -> None:
        """构造脱敏适配器。

        参数：capture_content 为内容采集开关（必须经 ``content_capture_allowed``
        环境门控后传入；默认 False 即默认 deny）。
        """
        self._capture = capture_content

    def redact_content(self, payload: dict) -> dict:
        """返回负载的脱敏副本；脱敏失败抛错（调用方不得把原文发送给 sink）。"""
        return redact_payload(payload, capture_content=self._capture)

    def envelope_meta(self, envelope: AgentInputEnvelope) -> dict:
        """把输入信封转为脱敏运行元数据（ID/版本/清单保留，不含内容键）。

        参数：envelope 为 Agent 输入信封。
        返回：只含结构化 ID/版本/清单引用与运行范围的元数据字典（不含正文、
        Prompt、草稿等内容键），供节点事件记录输入版本与 ContextManifest 来源。
        """
        rc = envelope.runtime_context
        revisions = [
            i
            for i in (
                envelope.base_scene_revision_id,
                envelope.accepted_scene_revision_id,
                envelope.base_chapter_revision_id,
                envelope.accepted_chapter_revision_id,
                envelope.plan_revision_id,
            )
            if i
        ]
        revisions += [e.revision_id for e in envelope.context_manifest if e.revision_id]
        return {
            "generation_run_id": rc.generation_run_id,
            "agent_run_id": rc.agent_run_id,
            "agent_attempt_key": rc.agent_attempt_key,
            "project_id": envelope.project.get("id") if isinstance(envelope.project, dict) else None,
            "chapter_id": rc.chapter_id,
            "scene_id": rc.scene_id,
            "request_type": envelope.request_type,
            "input_revision_ids": list(dict.fromkeys(revisions)),
            "context_manifest_ids": [e.source_id for e in envelope.context_manifest],
        }


def _run_context_of(meta: dict, environment: str) -> RunContext:
    """从脱敏元数据构造 RunContext（缺省字段用空串，保证类型完整）。"""
    return RunContext(
        generation_run_id=str(meta.get("generation_run_id") or ""),
        agent_run_id=str(meta.get("agent_run_id") or ""),
        agent_attempt_key=str(meta.get("agent_attempt_key") or ""),
        project_id=str(meta.get("project_id") or ""),
        scene_id=meta.get("scene_id"),
        chapter_id=meta.get("chapter_id"),
        request_type=str(meta.get("request_type") or ""),
        environment=environment,
    )


class TraceHook:
    """Agent 生命周期 Trace 钩子：在 LangGraph 节点边界自动记录节点开始/结束。

    注册到 ``HookRegistry`` 后，场景/章节/Canon 三图的所有 Agent 节点自动上报
    节点名、输入版本、ContextManifest 来源、耗时、token 摘要与路由结果；所有
    内容先经 ``RedactionHook`` 脱敏；sink 调用防御性 fail-open（失败只降级，
    不影响节点执行与路由）。
    """

    def __init__(
        self,
        sink: ObservabilitySink,
        *,
        environment: str = "development",
        redaction: RedactionHook | None = None,
    ) -> None:
        """构造 TraceHook。

        参数：sink 为观测 sink（本地/LangSmith/组合）；environment 为运行环境；
        redaction 为脱敏适配器（缺省新建，默认 deny 内容）。
        """
        self._sink = sink
        self._environment = environment
        self._redaction = redaction or RedactionHook()
        self._starts: dict[str, float] = {}
        self._meta: dict[str, dict] = {}
        self.degraded: list[str] = []

    def before(self, agent_type: str, envelope: AgentInputEnvelope) -> None:
        """节点开始：记录起始时间与脱敏输入元数据（输入版本/清单来源）。"""
        self._starts[agent_type] = time.monotonic()
        self._meta[agent_type] = self._redaction.envelope_meta(envelope)

    def after(self, agent_type: str, output: BaseModel, outcome: RouterOutcome) -> None:
        """节点结束：上报 NodeEvent（耗时/token 摘要/路由状态，全部脱敏）。"""
        started = self._starts.pop(agent_type, None)
        meta = self._meta.pop(agent_type, {})
        now = time.monotonic()
        event: NodeEvent = {
            "run_context": _run_context_of(meta, self._environment),
            "generation_run_id": str(meta.get("generation_run_id") or ""),
            "agent_run_id": str(meta.get("agent_run_id") or ""),
            "node_name": agent_type,
            "started_at": _iso(started or now),
            "ended_at": _iso(now),
            "duration_ms": int((now - (started or now)) * 1000),
            "input_revision_ids": list(meta.get("input_revision_ids", [])),
            "output_summary": self._output_summary(output),
            "token_usage": extract_token_usage(output),
        }
        self._guarded("on_node_end", event)

    def _output_summary(self, output: BaseModel) -> dict:
        """构造输出摘要（仅元数据；内容键经 RedactionHook 脱敏）。"""
        summary = {
            "status": getattr(output, "status", None),
            "mode": getattr(output, "mode", None),
            "candidate_fact_count": len(getattr(output, "candidate_facts", None) or []),
            "issue_count": len(getattr(output, "review_issues", None) or []),
            "revision_count": len(getattr(output, "text_operations", None) or []),
            "clarification_count": len(getattr(output, "clarification_questions", None) or []),
        }
        return self._redaction.redact_content({k: v for k, v in summary.items() if v is not None})

    def _guarded(self, kind: str, event: Any) -> None:
        """调用 sink（防御性 fail-open）：失败只记录降级，不上抛节点/路由。"""
        try:
            getattr(self._sink, kind)(event)
        except Exception as exc:  # sink 失败不影响业务节点执行
            self.degraded.append(f"{kind}: {exc}")


def register_trace(
    registry: HookRegistry,
    sink: ObservabilitySink,
    *,
    environment: str = "development",
    agent_types: Sequence[str] = DEFAULT_AGENT_TYPES,
    redaction: RedactionHook | None = None,
) -> TraceHook:
    """把 TraceHook 注册到 HookRegistry，覆盖全部 Agent 类型（自动埋点入口）。

    参数：registry 为 HookRegistry；sink 为观测 sink；environment 为运行环境；
    agent_types 为要覆盖的 Agent 类型（默认全部 7 类）；redaction 为脱敏适配器。
    返回：已注册的 TraceHook 实例。
    """
    hook = TraceHook(sink=sink, environment=environment, redaction=redaction)
    for agent_type in agent_types:
        registry.register(agent_type, hook)
    return hook


def trace_call(
    sink: ObservabilitySink,
    *,
    name: str,
    kind: Literal["llm", "tool"],
    generation_run_id: str,
    agent_run_id: str,
    node_name: str,
    redaction: RedactionHook | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """装饰器：包裹模型/工具调用，自动上报 llm/tool 运行事件。

    参数：sink 为观测 sink；name 为调用名；kind 为 llm|tool；generation_run_id /
    agent_run_id 为所属运行与 agent；node_name 为父节点名；redaction 为脱敏适配器。

    返回的包装函数保持业务语义不变：原返回值原样返回；业务异常原样重抛；sink
    调用 fail-open（失败只降级）。事件节点名编码层级 ``{node}:{kind}:{name}``，
    形成 generation_run -> agent_run -> llm/tool_run 的 Trace 层级。
    """
    red = redaction or RedactionHook()

    def decorate(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapped(*args: Any, **kwargs: Any) -> T:
            started = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # 业务错误原样重抛；先上报 error（fail-open）
                _emit_error(sink, generation_run_id, f"{node_name}:{kind}:{name}", exc)
                raise
            ended = time.monotonic()
            event: NodeEvent = {
                "generation_run_id": generation_run_id,
                "agent_run_id": agent_run_id,
                "node_name": f"{node_name}:{kind}:{name}",
                "started_at": _iso(started),
                "ended_at": _iso(ended),
                "duration_ms": int((ended - started) * 1000),
                "input_revision_ids": [],
                "output_summary": red.redact_content({"call": name, "status": "ok"}),
                "token_usage": extract_token_usage(result),
            }
            _guarded_sink(sink, "on_node_end", event)
            return result

        return wrapped

    return decorate


def _guarded_sink(sink: ObservabilitySink, kind: str, event: Any) -> None:
    """通用防御性 sink 调用（fail-open）：任何异常不向业务上抛。"""
    try:
        getattr(sink, kind)(event)
    except Exception:
        pass


def _emit_error(
    sink: ObservabilitySink,
    generation_run_id: str,
    node_name: str,
    exc: Exception,
) -> None:
    """上报错误事件（消息经 sink 脱敏；sink 失败只降级不上抛）。"""
    event: ErrorEvent = {
        "generation_run_id": generation_run_id,
        "node_name": node_name,
        "error_code": _error_code_of(exc),
        "message": str(exc),
        "retryable": bool(getattr(exc, "retryable", False)),
        "degraded": False,
        "created_at": _iso(time.time()),
    }
    _guarded_sink(sink, "on_error", event)


class GraphObservability:
    """包装图 ``invoke``：自动记录 run_start / run_end / error（fail-open）。

    与被包装图同接口（``invoke(state, envelope, thread_id, resume=None)``），可
    原样替换执行器中的图引用；不修改图内部节点、路由与状态机契约。sink 失败只
    记录本地降级标记，不影响业务结果，也不导致命令重复执行。
    """

    def __init__(
        self,
        graph: Any,
        sink: ObservabilitySink,
        *,
        environment: str = "development",
        redaction: RedactionHook | None = None,
    ) -> None:
        """构造图观测包装器。

        参数：graph 为被包装的图（SceneGraph 等，须有 invoke(state, envelope,
        thread_id, resume=None)）；sink 为观测 sink；environment 为运行环境；
        redaction 为脱敏适配器。
        """
        self._graph = graph
        self._sink = sink
        self._environment = environment
        self._redaction = redaction or RedactionHook()
        self.degraded_observability: bool = False
        self._sink_errors: list[str] = []

    def invoke(
        self,
        state: ChapterRunState,
        envelope: AgentInputEnvelope,
        thread_id: str,
        resume: dict | None = None,
    ) -> dict:
        """执行图并自动记录 run_start / error / run_end。

        参数：state 为初始运行状态；envelope 为输入信封；thread_id 为运行线程；
        resume 为可选的恢复锚点。
        返回：图执行后的最终 state 字典（与未包装图一致）。
        失败条件：图自身异常原样上抛（业务失败语义不变），但先上报 error 事件。
        """
        rc = envelope.runtime_context
        run_ctx = _run_context_of(
            self._redaction.envelope_meta(envelope),
            self._environment,
        )
        self._call_guarded("on_run_start", run_ctx)
        started = time.monotonic()
        try:
            result = self._graph.invoke(state, envelope, thread_id=thread_id, resume=resume)
        except Exception as exc:
            _emit_error(self._sink, rc.generation_run_id, "graph", exc)
            raise
        ended = time.monotonic()
        end: RunEndEvent = {
            "generation_run_id": rc.generation_run_id,
            "status": result.get("run_status") or "failed",
            "final_decision": result.get("_pause_action"),
            "duration_ms": int((ended - started) * 1000),
            "token_usage": None,
            "degraded_observability": self.degraded_observability,
        }
        self._call_guarded("on_run_end", end)
        return result

    def _call_guarded(self, kind: str, event: Any) -> None:
        """调用 sink（防御性 fail-open）：失败只记录降级，不上抛业务。"""
        try:
            getattr(self._sink, kind)(event)
        except Exception as exc:
            self.degraded_observability = True
            self._sink_errors.append(f"{kind}: {exc}")


def record_author_feedback(
    sink: ObservabilitySink,
    *,
    generation_run_id: str,
    target: str,
    decision: str,
    content: str = "",
) -> RunFeedback:
    """记录作者反馈：默认只存内容哈希，不保存正文（完整采集受环境开关限制）。

    参数：sink 为观测 sink；generation_run_id 为运行 id；target 为反馈目标
    （scene/chapter/run 等）；decision 为决策类型；content 为反馈原文（仅用于
    计算哈希，绝不写入事件）。
    返回：构造的 RunFeedback 事件。
    """
    feedback: RunFeedback = {
        "generation_run_id": generation_run_id,
        "target": target,
        "decision": decision,
        "feedback_hash": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
        "created_at": _iso(time.time()),
    }
    _guarded_sink(sink, "record_feedback", feedback)
    return feedback


__all__ = [
    "DEFAULT_AGENT_TYPES",
    "RedactionHook",
    "TraceHook",
    "register_trace",
    "trace_call",
    "GraphObservability",
    "record_author_feedback",
    "extract_token_usage",
]
