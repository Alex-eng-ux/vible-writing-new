"""Task 8 生产 wiring 测试：实际 Worker/运行入口接入自动埋点。

覆盖：
- ``make_wiring`` 无真实 LangSmith API Key 时只用本地 sink（LangSmith 跳过）；
- ``ObservabilityWiring.traced`` 为 SceneGraph/ChapterGraph/CanonGraph 注册
  TraceHook 并包装 GraphObservability，幂等（重复包装/重复埋点被阻止）；
- 实际 ``RunExecutor`` 入口自动产生 run_start / node_end / run_end / error，
  内容进入 sink 前脱敏；
- ``traced_call`` 模型调用入口上报 llm 事件（Fake model，耗时/token 摘要）；
- ``submit_run_decision`` 作者反馈入口调用 record_author_feedback（只存哈希）；
- sink 故障不影响业务、不导致命令重复执行（fail-open + 幂等）。

全部使用 Fake model / 本地 sink，不依赖真实 LangSmith API Key。
"""
from __future__ import annotations

import json
from typing import cast

import pytest

from app.agents.canon_graph import CanonGraph
from app.agents.chapter_graph import ChapterGraph
from app.agents.graph import SceneGraph
from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, ContextManifestEntry, RuntimeContext
from app.agents.state import ChapterRunState
from app.agents.writing_agent import WritingAgent
from app.api.schemas import DecisionRequest
from app.config import AppConfig
from app.db.models import GenerationRun
from app.errors import AppError
from app.observability.redaction import REDACTED
from app.observability.sink import FallbackSink, LocalSink
from app.observability.trace import DEFAULT_AGENT_TYPES, GraphObservability, TraceHook
from app.observability.wiring import ObservabilityWiring, make_wiring
from app.runtime.executor import RunExecutor
from app.runtime.leases import LeaseRepository
from app.runtime.run_identity import RunIdentity
from app.services.generation_runs import submit_run_decision
from tests.fixtures.fake_model import FakeModelProvider


def _cfg(*, langsmith_tracing: bool = False, langsmith_api_key: str = "") -> AppConfig:
    """构造最小合法配置（默认无 LangSmith Key）。"""
    return AppConfig(
        actor_id="test-actor",
        app_env="development",
        deployment_mode="single_user_private",
        api_bind_scope="loopback",
        internal_api_base_url="http://127.0.0.1:8000",
        langsmith_tracing=langsmith_tracing,
        langsmith_api_key=langsmith_api_key,
    )


def _identity(run_id: str = "g1", worker: str = "w1") -> RunIdentity:
    """构造运行身份。"""
    return RunIdentity(
        generation_run_id=run_id,
        agent_run_id="a1",
        agent_attempt_key="ak1",
        parent_generation_run_id=None,
        supersedes_run_id=None,
        parent_plan_revision_id=None,
    )


def _env(thread_id: str = "g1") -> AgentInputEnvelope:
    """构造固定输入信封（含输入版本与 ContextManifest 来源，正文为敏感内容）。"""
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
        context_manifest=[
            ContextManifestEntry(source_id="src-1", kind="scene", revision_id="r2")
        ],
        draft_text="secret draft content",
        accepted_text="secret accepted content",
        base_scene_revision_id="r1",
    )


def _state(thread_id: str = "g1") -> ChapterRunState:
    """构造最小运行状态。"""
    return ChapterRunState(generation_run_id=thread_id, run_version=1)


class _RaisingSink:
    """模拟完全故障的 sink（违反 fail-open 约定），验证防御性保护业务。"""

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


# ---------------------------------------------------------------------------
# make_wiring：无真实 API Key
# ---------------------------------------------------------------------------


def test_make_wiring_builds_local_sink_without_api_key() -> None:
    """LangSmith 关闭时只用本地 sink，不要求真实 API Key。"""
    wiring = make_wiring(_cfg())
    assert isinstance(wiring.sink, FallbackSink)
    assert wiring.sink._primary is None  # type: ignore[attr-defined]
    assert isinstance(wiring.local, LocalSink)
    assert wiring.environment == "development"


def test_make_wiring_skips_langsmith_without_key() -> None:
    """即使开启 langsmith_tracing，缺少 API Key 也跳过 LangSmith（不失败）。"""
    wiring = make_wiring(_cfg(langsmith_tracing=True, langsmith_api_key=""))
    assert isinstance(wiring.sink, FallbackSink)
    assert wiring.sink._primary is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# traced：三图注册 TraceHook + 幂等包装
# ---------------------------------------------------------------------------


def test_wiring_traces_all_graphs_and_is_idempotent(db) -> None:
    """三图注册 TraceHook 并包装 GraphObservability；重复调用不重复包装/埋点。"""
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")

    # 场景图：TraceHook 覆盖全部 Agent 类型。
    registry = HookRegistry()
    scene = SceneGraph(registry, AgentResultRouter())
    wrapper1 = wiring.traced(scene)
    assert isinstance(wrapper1, GraphObservability)
    assert wiring.traced(scene) is wrapper1  # 同一图重复包装返回同一实例
    assert wiring.traced(wrapper1) is wrapper1  # 已包装图原样返回
    for agent_type in DEFAULT_AGENT_TYPES:
        assert any(isinstance(h, TraceHook) for h in registry.lifecycle(agent_type))

    # 章节图：注册到其自身注册表（chapter_planner / chapter_review）。
    ch_registry = HookRegistry()
    ch_graph = ChapterGraph(ch_registry, AgentResultRouter())
    wiring.traced(ch_graph)
    assert any(
        isinstance(h, TraceHook) for h in ch_registry.lifecycle("chapter_planner")
    )
    assert any(
        isinstance(h, TraceHook) for h in ch_registry.lifecycle("chapter_review")
    )

    # Canon 图：注册到其自身注册表（canon）。
    canon_registry = HookRegistry()
    canon_graph = CanonGraph(session=db, registry=canon_registry, router=AgentResultRouter())
    wiring.traced(canon_graph)
    assert any(isinstance(h, TraceHook) for h in canon_registry.lifecycle("canon"))


# ---------------------------------------------------------------------------
# 实际运行入口（RunExecutor）：run_start / node_end / run_end / error
# ---------------------------------------------------------------------------


def test_executor_entry_produces_run_events(db) -> None:
    """实际 RunExecutor 入口自动记录 run_start、四节点 node_end、run_end。"""
    db.add(GenerationRun(id="g1", project_id="p1", status="running"))
    db.flush()
    leases = LeaseRepository(db)
    lease = leases.claim(_identity(), "w1")

    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    executor = RunExecutor(
        leases,
        SceneGraph(HookRegistry(), AgentResultRouter()),
        _identity(),
        observability=wiring,
    )
    result = executor.execute(
        "g1", "w1", lease["fencing_token"], lease["lease_token"], _state(), _env()
    )

    # 业务结果与未包装图一致（revision 等待作者）。
    assert result["last_durable_node"] == "revision"

    records = wiring.local.records if wiring.local is not None else []
    kinds = [r["kind"] for r in records]
    assert "run_start" in kinds
    assert "run_end" in kinds
    node_names = {n["node_name"] for n in records if n["kind"] == "node_end"}
    assert node_names == {"writing", "continuity", "review", "revision"}

    run_start = next(r for r in records if r["kind"] == "run_start")
    assert run_start["generation_run_id"] == "g1"
    assert run_start["project_id"] == "p1"
    assert run_start["environment"] == "evaluation"

    end = next(r for r in records if r["kind"] == "run_end")
    assert end["status"] == "paused"
    assert end["duration_ms"] >= 0
    assert end["degraded_observability"] is False

    # 所有内容进入 sink 前已脱敏。
    blob = json.dumps(records, ensure_ascii=False)
    assert "secret draft content" not in blob
    assert "secret accepted content" not in blob
    assert "Generated scene content" not in blob


def test_executor_entry_records_error_and_re_raises(db) -> None:
    """节点异常：实际入口上报 error 事件后原样重抛（业务失败语义不变）。"""

    class _BoomAgent:
        def run(self, envelope: AgentInputEnvelope) -> None:
            raise AppError("RUN_STATE_CONFLICT", "boom")

    db.add(GenerationRun(id="g-err", project_id="p1", status="running"))
    db.flush()
    leases = LeaseRepository(db)
    lease = leases.claim(_identity("g-err"), "w1")

    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    registry = HookRegistry()
    executor = RunExecutor(
        leases,
        SceneGraph(registry, AgentResultRouter(), writing=cast(WritingAgent, _BoomAgent())),
        _identity("g-err"),
        observability=wiring,
    )
    with pytest.raises(AppError) as exc_info:
        executor.execute(
            "g-err", "w1", lease["fencing_token"], lease["lease_token"], _state("g-err"), _env("g-err")
        )
    assert exc_info.value.code == "RUN_STATE_CONFLICT"

    records = wiring.local.records if wiring.local is not None else []
    errors = [r for r in records if r["kind"] == "error"]
    assert len(errors) == 1
    assert errors[0]["error_code"] == "RUN_STATE_CONFLICT"
    assert errors[0]["node_name"] == "graph"
    # 错误消息进入 sink 前被脱敏（message 键不在白名单）。
    assert errors[0]["message"] == REDACTED


# ---------------------------------------------------------------------------
# traced_call：模型调用入口（Fake model）
# ---------------------------------------------------------------------------


def test_trace_call_entry_records_llm_event() -> None:
    """模型调用入口自动上报 llm 事件：耗时/token 摘要/脱敏；业务结果不变。"""
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    model = FakeModelProvider(
        fixture={
            "status": "ready",
            "text": "x",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )
    wrapped = wiring.traced_call(
        name="generate",
        kind="llm",
        generation_run_id="g1",
        agent_run_id="a1",
        node_name="writing",
    )(model.invoke)
    result = wrapped("secret prompt text")

    assert result["text"] == "x"  # 原返回值原样返回
    records = wiring.local.records if wiring.local is not None else []
    llm_events = [
        r
        for r in records
        if r["kind"] == "node_end" and r["node_name"] == "writing:llm:generate"
    ]
    assert len(llm_events) == 1
    assert llm_events[0]["token_usage"]["total_tokens"] == 15
    assert llm_events[0]["duration_ms"] >= 0
    assert llm_events[0]["output_summary"]["call"] == "generate"
    # 提示词不泄漏到 sink。
    blob = json.dumps(records, ensure_ascii=False)
    assert "secret prompt text" not in blob


# ---------------------------------------------------------------------------
# 作者反馈决策入口：record_author_feedback（只存哈希）
# ---------------------------------------------------------------------------


def test_feedback_decision_records_feedback(db) -> None:
    """作者反馈决策入口调用 record_author_feedback：只存哈希，正文不落库。"""
    run_id = "g-fb"
    db.add(
        GenerationRun(
            id=run_id,
            project_id="p1",
            chapter_id="c1",
            scene_id="s1",
            status="waiting_feedback",
            run_version=1,
            decision_target="scene",
            request_type="continue",
        )
    )
    db.flush()
    sink = LocalSink()
    body = DecisionRequest(
        idempotency_key="fb-key",
        expected_run_version=1,
        target="scene",
        decision="feedback",
        text="请调整语气：林默更谨慎",
    )
    result = submit_run_decision(db, "author-1", run_id, body, "cmd-fb", sink=sink)

    feeds = [r for r in sink.records if r["kind"] == "feedback"]
    assert len(feeds) == 1
    assert feeds[0]["generation_run_id"] == run_id
    assert feeds[0]["target"] == "scene"
    assert feeds[0]["decision"] == "feedback"
    assert feeds[0]["feedback_hash"]
    # 正文不落库，只存哈希。
    blob = json.dumps(sink.records, ensure_ascii=False)
    assert "请调整语气" not in blob
    assert result["run"]["status"] == "waiting_feedback"


# ---------------------------------------------------------------------------
# sink 故障：不影响业务、不重复执行
# ---------------------------------------------------------------------------


class _CountingGraph:
    """统计 invoke 次数的图（供“不重复执行”断言）。"""

    def __init__(self, registry: HookRegistry, real: SceneGraph) -> None:
        self._registry = registry
        self._real = real
        self.calls = 0

    @property
    def registry(self) -> HookRegistry:
        return self._registry

    def invoke(self, state, envelope, thread_id, resume=None):
        self.calls += 1
        return self._real.invoke(state, envelope, thread_id=thread_id, resume=resume)


def test_sink_failure_does_not_break_business_or_repeat(db) -> None:
    """sink 完全故障：决策与运行入口业务结果不变、命令不重复执行。"""
    # 决策入口：raising sink 不影响 feedback 决策事务。
    run_id = "g-sink"
    db.add(
        GenerationRun(
            id=run_id,
            project_id="p1",
            chapter_id="c1",
            scene_id="s1",
            status="waiting_feedback",
            run_version=1,
            decision_target="scene",
            request_type="continue",
        )
    )
    db.flush()
    body = DecisionRequest(
        idempotency_key="sink-key",
        expected_run_version=1,
        target="scene",
        decision="feedback",
        text="x",
    )
    result = submit_run_decision(db, "author-1", run_id, body, "cmd-sink", sink=_RaisingSink())
    assert result["run"]["status"] == "waiting_feedback"

    # 运行入口：raising sink 下业务结果不变、图只执行一次。
    db.add(GenerationRun(id="g-sink-ex", project_id="p1", status="running"))
    db.flush()
    leases = LeaseRepository(db)
    lease = leases.claim(_identity("g-sink-ex"), "w1")
    registry = HookRegistry()
    counting = _CountingGraph(registry, SceneGraph(registry, AgentResultRouter()))
    wiring = ObservabilityWiring(sink=_RaisingSink(), environment="evaluation")
    executor = RunExecutor(leases, counting, _identity("g-sink-ex"), observability=wiring)
    outcome = executor.execute(
        "g-sink-ex",
        "w1",
        lease["fencing_token"],
        lease["lease_token"],
        _state("g-sink-ex"),
        _env("g-sink-ex"),
    )
    assert outcome["last_durable_node"] == "revision"
    assert counting.calls == 1  # 图只执行一次，未因 sink 故障重试
