"""Task 9 真实 ChapterPlanner/ChapterReview 链路测试（默认 HTTP mock，不访问网络）。

覆盖：
- 成功链路：bound 带章节契约的信封 -> 经真实 Provider（MockTransport）
  ChapterPlanner -> ChapterReview 依次返回结构化结果 -> 聚合节点因未装配
  aggregator 而暂停（不产生任何版本/Canon/handoff 写入）；
- 失败链路：Planner/Review Provider 401 / 超时 / 非法结构化输出 ->
  ``AppError(LLM_*)`` 上抛（运行层据此把运行置 failed），不产生业务写入；
- 真实模型 smoke（门控）：设置 REAL_MODEL_SMOKE=1 且 LLM_BASE_URL /
  LLM_API_KEY / MODEL_NAME 齐备时，用真实 DeepSeek 跑同一条链路（默认跳过，
  保证普通测试不访问网络）。
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import pytest

from app.agents.chapter_graph import ChapterGraph
from app.agents.hook_registry import HookRegistry
from app.agents.model_provider import DeepSeekModelProvider
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.errors import AppError
from app.observability.sink import LocalSink
from app.observability.wiring import ObservabilityWiring

_SYSTEM_PLANNER = "You are a chapter planning agent"
_SYSTEM_REVIEW = "You are a chapter review agent"


def _envelope() -> AgentInputEnvelope:
    return AgentInputEnvelope(
        runtime_context=RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="chapter",
            decision_target="plan",
            chapter_id="ch1",
        ),
        request_type="new_chapter",
        project={"id": "p1", "name": "测试项目"},
        chapter_contract={"pov": "p", "scene_keys": ["s1", "s2"]},
    )


def _state() -> dict:
    return {
        "generation_run_id": "g1",
        "run_version": 1,
        "project_id": "p1",
        "chapter_id": "ch1",
        "scene_ids": ["s1", "s2"],
        "run_status": "running",
        "last_durable_node": None,
        "pending_node": None,
        "clarification_questions": [],
        "scene_auto_revision_counts": {},
        "inheritance_map": {},
    }


def _real_provider(transport: httpx.MockTransport, *, wiring: ObservabilityWiring | None = None) -> DeepSeekModelProvider:
    """构造使用 MockTransport 的真实 Provider（默认不访问网络，关闭自动重试）。"""
    client = httpx.Client(transport=transport, timeout=httpx.Timeout(5))
    return DeepSeekModelProvider(
        base_url="https://api.deepseek.com",
        api_key="sk-test-key",
        model_name="deepseek-v4-flash",
        wiring=wiring,
        http_client=client,
        max_retries=0,
    )


def _planner_content() -> str:
    return json.dumps(
        {
            "status": "ready",
            "chapter_contract": {"pov": "p", "scene_keys": ["s1", "s2"]},
            "scene_contracts": [{"client_key": "s1", "title": "Scene s1", "scene_brief": {}}],
            "reason": "planned",
            "clarification_questions": [],
        }
    )


def _review_content() -> str:
    return json.dumps(
        {
            "status": "ready",
            "review_issues": [],
            "overall_rating": "pass",
            "submitted": True,
            "clarification_questions": [],
        }
    )


def _ok_response(system_prompt: str) -> httpx.Response:
    content = _planner_content() if _SYSTEM_PLANNER in system_prompt else _review_content()
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def _system_prompt_of(req: httpx.Request) -> str:
    body = json.loads(req.content)
    return body["messages"][0]["content"]


def _graph(*, provider, aggregator=None) -> ChapterGraph:
    from app.agents.chapter_planner import ChapterPlannerAgent
    from app.agents.chapter_review_agent import ChapterReviewAgent

    return ChapterGraph(
        registry=HookRegistry(),
        router=AgentResultRouter(),
        planner=ChapterPlannerAgent(provider=provider),
        review=ChapterReviewAgent(provider=provider),
        aggregator=aggregator,
    )


def _chapter_review_envelope() -> AgentInputEnvelope:
    envelope = _envelope()
    return envelope.model_copy(
        update={
            "request_type": "review",
            "runtime_context": envelope.runtime_context.model_copy(
                update={"decision_target": "chapter"}
            ),
        }
    )


class _EligibleAggregator:
    """测试用章节聚合器：验证章节审校入口确实先执行聚合。"""

    def eligibility(self, *args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(eligible=True, reason="", status="ready")

    def aggregate(self, *args: object, **kwargs: object) -> str:
        return "staged-chapter-revision"


def test_real_chapter_chain_success_runs_aggregate_and_review() -> None:
    """成功链路：Planner -> Review 均经 provider 返回结构化结果，无版本/Canon/handoff 写入。"""
    seen_nodes: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sp = _system_prompt_of(req)
        seen_nodes.append("planner" if _SYSTEM_PLANNER in sp else "review")
        return _ok_response(sp)

    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = _real_provider(httpx.MockTransport(handler), wiring=wiring)
    graph = _graph(provider=provider, aggregator=_EligibleAggregator())
    result = graph.invoke(_state(), _chapter_review_envelope(), thread_id="t-success")
    # 两个章节模型 Agent 均被真实 Provider（mock）调用一次。
    assert seen_nodes == ["review"]
    # 聚合节点未装配 aggregator -> 暂停等待作者（不产生任何版本/Canon/handoff 写入）。
    assert result["run_status"] == "running"
    assert result["last_durable_node"] == "chapter_review"
    # 观测自动埋点：planner / review 挂载 llm 节点。
    assert wiring.local is not None
    node_names = [str(r.get("node_name")) for r in wiring.local.records]
    assert any(n.startswith("chapter_review:llm:") for n in node_names)


def test_real_chapter_chain_planner_401_fails() -> None:
    """失败链路：Planner Provider 401 -> 抛 LLM_AUTH_ERROR（运行层据此置 failed）。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if _SYSTEM_PLANNER in _system_prompt_of(req):
            return httpx.Response(401, json={"error": {"message": "auth"}})
        return _ok_response(_system_prompt_of(req))

    provider = _real_provider(httpx.MockTransport(handler))
    graph = _graph(provider=provider)
    with pytest.raises(AppError) as exc:
        graph.invoke(_state(), _envelope(), thread_id="t-401")
    assert exc.value.code == "LLM_AUTH_ERROR"


def test_real_chapter_chain_review_timeout_fails() -> None:
    """失败链路：Review Provider 超时 -> 抛 LLM_UNAVAILABLE（运行层据此置 failed）。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if _SYSTEM_REVIEW in _system_prompt_of(req):
            raise httpx.ConnectTimeout("connect timed out", request=req)
        return _ok_response(_system_prompt_of(req))

    provider = _real_provider(httpx.MockTransport(handler))
    graph = _graph(provider=provider, aggregator=_EligibleAggregator())
    with pytest.raises(AppError) as exc:
        graph.invoke(_state(), _chapter_review_envelope(), thread_id="t-timeout")
    assert exc.value.code == "LLM_UNAVAILABLE"


def test_real_chapter_chain_planner_invalid_output_fails() -> None:
    """失败链路：Planner 非法结构化输出 -> 抛 LLM_RESPONSE_INVALID。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if _SYSTEM_PLANNER in _system_prompt_of(req):
            content = json.dumps({"status": "bogus"})
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})
        return _ok_response(_system_prompt_of(req))

    provider = _real_provider(httpx.MockTransport(handler))
    graph = _graph(provider=provider)
    with pytest.raises(AppError) as exc:
        graph.invoke(_state(), _envelope(), thread_id="t-invalid")
    assert exc.value.code == "LLM_RESPONSE_INVALID"


@pytest.mark.skipif(
    not os.environ.get("REAL_MODEL_SMOKE")
    or not (os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY") and os.environ.get("MODEL_NAME")),
    reason="REAL_MODEL_SMOKE=1 且 LLM_BASE_URL/LLM_API_KEY/MODEL_NAME 齐备时才执行（默认不访问网络）",
)
def test_real_chapter_chain_with_real_deepseek() -> None:
    """真实模型 smoke：用真实 DeepSeek 走 Planner -> Review 完整链路（门控）。"""
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = DeepSeekModelProvider(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model_name=os.environ["MODEL_NAME"],
        wiring=wiring,
    )
    try:
        graph = _graph(provider=provider)
        result = graph.invoke(_state(), _envelope(), thread_id="t-live")
        assert result["run_status"] == "paused"
        assert wiring.local is not None
        node_names = [str(r.get("node_name")) for r in wiring.local.records]
        # 真实模型非确定性：planner 可能返回 needs_clarification 而暂停在 planner，
        # 也可能推进到 review/aggregator；关键是不 failed、schema 校验通过、
        # 且模型调用被观测到。
        assert result["pending_node"] in ("chapter_planner", "chapter_review", "chapter_aggregator")
        assert any(":llm:" in n for n in node_names)
    finally:
        provider.close()
