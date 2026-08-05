"""Task 8 自动埋点集成测试：TraceHook/RedactionHook 接入节点/模型/工具调用。

覆盖：
- ``register_trace`` 把 TraceHook 注册到全部 Agent 类型（场景/章节/Canon 三图）；
- TraceHook 在 LangGraph 节点边界自动记录节点名、输入版本、ContextManifest 来源、
  耗时、token 摘要与路由状态（内容脱敏）；
- GraphObservability 自动记录 run_start / run_end / error；
- ``trace_call`` 包裹模型/工具调用并上报 llm/tool 运行事件；
- 所有内容进入 sink 前必须先脱敏（原文不出进程）；
- sink 失败不影响业务、不导致命令重复执行（防御性 fail-open）；
- ``record_author_feedback`` 只存内容哈希，不保存正文。

全部使用 Fake model 与本地 sink，不依赖真实 LangSmith API Key。
"""
from __future__ import annotations

import json
from typing import cast

import pytest

from app.agents.graph import SceneGraph
from app.agents.hook_registry import HookRegistry
from app.agents.nodes import AgentCallable
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import (
    AgentInputEnvelope,
    ContextManifestEntry,
    RuntimeContext,
)
from app.agents.state import ChapterRunState
from app.agents.writing_agent import WritingAgent
from app.errors import AppError
from app.observability.redaction import REDACTED
from app.observability.sink import LocalSink
from app.observability.trace import (
    DEFAULT_AGENT_TYPES,
    GraphObservability,
    RedactionHook,
    record_author_feedback,
    register_trace,
    trace_call,
)
from tests.fixtures.fake_model import FakeModelProvider


def _env(thread_id: str = "g1") -> AgentInputEnvelope:
    """构造固定输入信封（含输入版本与 ContextManifest 来源）。"""
    rt = RuntimeContext(
        generation_run_id=thread_id,
        agent_run_id="a1",
        agent_attempt_key="ak1",
        thread_id=thread_id,
        chapter_id="c1",
        scene_id="s1",
    )
    return AgentInputEnvelope(
        project={"id": "p1"},
        runtime_context=rt,
        scene_brief={"goal": "x"},
        context_manifest=[ContextManifestEntry(source_id="src-1", kind="scene", revision_id="r2")],
        draft_text="secret draft content",
        accepted_text="secret accepted content",
        base_scene_revision_id="r1",
    )


def _state(thread_id: str = "g1") -> ChapterRunState:
    """构造最小运行状态。"""
    return ChapterRunState(generation_run_id=thread_id, run_version=1)


# ---------------------------------------------------------------------------
# register_trace / TraceHook 节点埋点
# ---------------------------------------------------------------------------


def test_register_trace_covers_all_agent_types() -> None:
    """TraceHook 注册覆盖全部 7 类 Agent（场景/章节/Canon 三图节点）。"""
    registry = HookRegistry()
    hook = register_trace(registry, LocalSink())
    for agent_type in DEFAULT_AGENT_TYPES:
        assert hook in registry.lifecycle(agent_type)


def test_trace_hook_records_node_metadata() -> None:
    """节点结束自动记录节点名/输入版本/清单来源/耗时/token 摘要，内容脱敏。"""
    registry = HookRegistry()
    sink = LocalSink()
    register_trace(registry, sink)
    callable_ = AgentCallable("writing", WritingAgent(), registry, AgentResultRouter())
    callable_(_state(), _env())

    nodes = [r for r in sink.records if r["kind"] == "node_end"]
    assert len(nodes) == 1
    node = nodes[0]
    assert node["node_name"] == "writing"
    # 输入版本：base_scene_revision_id + ContextManifest revision_id，去重保序。
    assert node["input_revision_ids"] == ["r1", "r2"]
    assert node["duration_ms"] >= 0
    assert node["token_usage"] is None  # 占位 Agent 无 token 摘要，不伪造数值
    # 输出摘要只含元数据，不含正文/草稿内容。
    assert node["output_summary"]["status"] == "ready"
    blob = json.dumps(sink.records, ensure_ascii=False)
    assert "secret draft content" not in blob
    assert "secret accepted content" not in blob
    assert "Generated scene content" not in blob


def test_redaction_hook_keeps_ids_redacts_content() -> None:
    """RedactionHook：信封元数据保留 ID/版本/清单；内容键一律脱敏。"""
    hook = RedactionHook()
    meta = hook.envelope_meta(_env())
    assert meta["generation_run_id"] == "g1"
    assert meta["project_id"] == "p1"
    assert meta["chapter_id"] == "c1"
    assert meta["scene_id"] == "s1"
    assert meta["input_revision_ids"] == ["r1", "r2"]
    assert meta["context_manifest_ids"] == ["src-1"]
    # 内容键不出现在元数据中（envelope_meta 只提取结构化字段）。
    assert "draft_text" not in meta
    assert "accepted_text" not in meta
    assert "scene_brief" not in meta
    # 即使误传内容，redact_content 也强制脱敏。
    red = hook.redact_content({"prompt": "secret", "id": "keep-me"})
    assert red["prompt"] == REDACTED
    assert red["id"] == "keep-me"


# ---------------------------------------------------------------------------
# GraphObservability：run_start / node_end / run_end / error
# ---------------------------------------------------------------------------


def test_graph_observability_records_full_run() -> None:
    """完整图运行自动记录 run_start、四节点 node_end、run_end；内容脱敏。"""
    registry = HookRegistry()
    sink = LocalSink()
    register_trace(registry, sink)
    graph = GraphObservability(SceneGraph(registry, AgentResultRouter()), sink, environment="evaluation")
    result = graph.invoke(_state(), _env(), thread_id="g1")

    # 业务结果与未包装图一致（revision 等待作者）。
    assert result["last_durable_node"] == "revision"

    kinds = [r["kind"] for r in sink.records]
    assert "run_start" in kinds
    assert "run_end" in kinds
    node_names = {n["node_name"] for n in sink.records if n["kind"] == "node_end"}
    assert node_names == {"writing", "continuity", "review", "revision"}

    run_start = next(r for r in sink.records if r["kind"] == "run_start")
    assert run_start["generation_run_id"] == "g1"
    assert run_start["project_id"] == "p1"
    assert run_start["environment"] == "evaluation"

    end = next(r for r in sink.records if r["kind"] == "run_end")
    assert end["status"] == "paused"
    assert end["duration_ms"] >= 0
    assert end["degraded_observability"] is False

    # 无任何原文内容泄漏。
    blob = json.dumps(sink.records, ensure_ascii=False)
    assert "secret draft content" not in blob
    assert "secret accepted content" not in blob
    assert "Generated scene content" not in blob


def test_graph_error_records_error_and_re_raises() -> None:
    """图节点异常：自动上报 error 事件后原样重抛（业务失败语义不变）。"""

    class _BoomAgent:
        def run(self, envelope: AgentInputEnvelope) -> None:
            raise AppError("RUN_STATE_CONFLICT", "boom")

    registry = HookRegistry()
    sink = LocalSink()
    register_trace(registry, sink)
    graph = GraphObservability(
        SceneGraph(registry, AgentResultRouter(), writing=cast(WritingAgent, _BoomAgent())),
        sink,
    )

    with pytest.raises(AppError) as exc_info:
        graph.invoke(_state(), _env(), thread_id="g1")
    assert exc_info.value.code == "RUN_STATE_CONFLICT"

    errors = [r for r in sink.records if r["kind"] == "error"]
    assert len(errors) == 1
    assert errors[0]["error_code"] == "RUN_STATE_CONFLICT"
    assert errors[0]["node_name"] == "graph"
    # 错误消息进入 sink 前被脱敏（message 键不在白名单）。
    assert errors[0]["message"] == REDACTED


# ---------------------------------------------------------------------------
# trace_call：模型/工具调用埋点
# ---------------------------------------------------------------------------


def test_trace_call_records_llm_event_with_token_usage() -> None:
    """模型调用自动上报 llm 运行事件：耗时/token 摘要/脱敏元数据；业务结果不变。"""
    sink = LocalSink()
    model = FakeModelProvider(
        fixture={"status": "ready", "text": "x", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    )
    wrapped = trace_call(
        sink, name="generate", kind="llm", generation_run_id="g1", agent_run_id="a1", node_name="writing"
    )(model.invoke)
    result = wrapped("secret prompt text")

    assert result["text"] == "x"  # 原返回值原样返回
    llm_events = [r for r in sink.records if r["kind"] == "node_end" and r["node_name"] == "writing:llm:generate"]
    assert len(llm_events) == 1
    assert llm_events[0]["token_usage"]["total_tokens"] == 15
    assert llm_events[0]["duration_ms"] >= 0
    assert llm_events[0]["output_summary"]["call"] == "generate"
    # 提示词不泄漏到 sink。
    blob = json.dumps(sink.records, ensure_ascii=False)
    assert "secret prompt text" not in blob


def test_trace_call_tool_error_emits_error_and_re_raises() -> None:
    """工具调用失败：上报 error（脱敏）后原样重抛业务异常。"""
    sink = LocalSink()

    def boom() -> None:
        raise AppError("RUN_STATE_CONFLICT", "tool exploded")

    wrapped = trace_call(
        sink, name="apply", kind="tool", generation_run_id="g1", agent_run_id="a1", node_name="revision"
    )(boom)
    with pytest.raises(AppError) as exc_info:
        wrapped()
    assert exc_info.value.code == "RUN_STATE_CONFLICT"
    errors = [r for r in sink.records if r["kind"] == "error"]
    assert len(errors) == 1
    assert errors[0]["node_name"] == "revision:tool:apply"
    assert errors[0]["error_code"] == "RUN_STATE_CONFLICT"


# ---------------------------------------------------------------------------
# sink 失败：不影响业务、不重复执行
# ---------------------------------------------------------------------------


class _RaisingSink:
    """模拟完全故障的 sink（违反 fail-open 约定），验证钩子防御性保护业务。"""

    def on_run_start(self, run: object) -> None:
        raise RuntimeError("sink down")

    def on_node_end(self, event: object) -> None:
        raise RuntimeError("sink down")

    def on_error(self, event: object) -> None:
        raise RuntimeError("sink down")

    def on_run_end(self, event: object) -> None:
        raise RuntimeError("sink down")

    def record_feedback(self, feedback: object) -> None:
        raise RuntimeError("sink down")


def test_sink_failure_does_not_break_business_or_repeat() -> None:
    """sink 故障：业务结果不变、图只执行一次（不触发重试）、降级被记录。"""
    registry = HookRegistry()
    hook = register_trace(registry, _RaisingSink())
    real_graph = SceneGraph(registry, AgentResultRouter())

    calls = {"invoke": 0}

    class _CountingGraph:
        def invoke(self, state, envelope, thread_id, resume=None):
            calls["invoke"] += 1
            return real_graph.invoke(state, envelope, thread_id=thread_id, resume=resume)

    obs = GraphObservability(_CountingGraph(), _RaisingSink())
    result = obs.invoke(_state(), _env(), thread_id="g1")

    # 业务结果与未包装图一致；sink 故障未导致命令重复执行。
    assert result["last_durable_node"] == "revision"
    assert calls["invoke"] == 1
    assert obs.degraded_observability is True
    # TraceHook 自身防御性降级（不把 sink 异常上抛给节点执行）。
    assert any("sink down" in d for d in hook.degraded)


# ---------------------------------------------------------------------------
# record_author_feedback
# ---------------------------------------------------------------------------


def test_record_author_feedback_hashes_content_only() -> None:
    """作者反馈只存哈希，正文不落库。"""
    sink = LocalSink()
    feedback = record_author_feedback(
        sink,
        generation_run_id="g1",
        target="scene",
        decision="feedback",
        content="请调整语气：林默更谨慎",
    )
    feeds = [r for r in sink.records if r["kind"] == "feedback"]
    assert len(feeds) == 1
    assert feeds[0]["feedback_hash"] == feedback["feedback_hash"]
    assert feeds[0]["target"] == "scene"
    assert feeds[0]["decision"] == "feedback"
    blob = json.dumps(sink.records, ensure_ascii=False)
    assert "请调整语气" not in blob  # 正文不落库，只存哈希
