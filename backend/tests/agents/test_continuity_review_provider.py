"""ContinuityAgent / ReviewAgent 真实 Provider 接线测试（stub Provider，不访问网络）。

覆盖（两类 Agent 共用同一桩 Provider 与信封工厂）：
- 注入 Provider 时生成并校验对应的 ContinuityOutput / ReviewOutput；
- 缺少基线/场景文本时不调用模型返回 needs_clarification；
- Provider 错误（LLM_*）原样上抛；
- 模型响应不合 schema 时映射 LLM_RESPONSE_INVALID；
- 未注入 Provider 保持 Fake 确定性输出（默认测试不访问网络）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.continuity_agent import ContinuityAgent
from app.agents.review_agent import ReviewAgent
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.errors import AppError


def _envelope(**overrides: object) -> AgentInputEnvelope:
    base: dict[str, Any] = {
        "runtime_context": RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="scene",
            decision_target="scene",
            scene_id="s1",
            chapter_id="c1",
        ),
        "request_type": "continue",
        "accepted_text": "雨夜咖啡馆，林默与旧友重逢。",
    }
    base.update(overrides)
    return AgentInputEnvelope(**base)


class _StubProvider:
    """记录调用并返回固定结果或抛错的桩 Provider（接受 system_prompt 参数）。"""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, str]] = []

    def invoke_structured(
        self,
        *,
        prompt: str,
        generation_run_id: str,
        agent_run_id: str,
        node_name: str,
        system_prompt: str | None = None,
    ) -> dict:
        self.calls.append(
            {
                "prompt": prompt,
                "generation_run_id": generation_run_id,
                "agent_run_id": agent_run_id,
                "node_name": node_name,
                "system_prompt": system_prompt or "",
            }
        )
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        if node_name == "continuity":
            return {"status": "pass", "scene_snapshot_delta": {}, "issues": [], "clarification_questions": []}
        return {"status": "ready", "review_issues": [], "overall_rating": "pass", "submitted": False, "clarification_questions": []}


# ----------------------------------------------------------------------
# ContinuityAgent
# ----------------------------------------------------------------------


def test_continuity_agent_with_provider_returns_validated_output() -> None:
    """注入 Provider：调用模型并返回经 ContinuityOutput 校验的结构化结果。"""
    provider = _StubProvider()
    agent = ContinuityAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope())
    assert output.status == "pass"
    assert output.scene_snapshot_delta == {}
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["generation_run_id"] == "g1"
    assert call["node_name"] == "continuity"
    # Prompt 含基线正文，且不含任何密钥。
    assert "雨夜咖啡馆" in call["prompt"]
    assert "sk-" not in call["prompt"]


def test_continuity_agent_skips_provider_when_no_baseline() -> None:
    """缺失基线文本：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = ContinuityAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(accepted_text="", draft_text=None))
    assert output.status == "needs_clarification"
    assert output.clarification_questions
    assert provider.calls == []


def test_continuity_agent_propagates_provider_error() -> None:
    """Provider 错误（如 LLM_UNAVAILABLE）原样上抛，不产生可提交输出。"""
    provider = _StubProvider(error=AppError("LLM_UNAVAILABLE", "model provider unavailable"))
    agent = ContinuityAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_UNAVAILABLE"


def test_continuity_agent_maps_schema_invalid_response() -> None:
    """模型响应不合 ContinuityOutput schema：映射为 LLM_RESPONSE_INVALID。"""
    provider = _StubProvider(result={"status": "bogus", "scene_snapshot_delta": {}})
    agent = ContinuityAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_continuity_agent_default_fake_keeps_deterministic_output() -> None:
    """未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = ContinuityAgent()
    output = agent.run(_envelope())
    assert output.status == "pass"
    assert output.scene_snapshot_delta == {}


# ----------------------------------------------------------------------
# ReviewAgent
# ----------------------------------------------------------------------


def test_review_agent_with_provider_returns_validated_output() -> None:
    """注入 Provider：调用模型并返回经 ReviewOutput 校验的结构化结果。"""
    provider = _StubProvider()
    agent = ReviewAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.overall_rating == "pass"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["generation_run_id"] == "g1"
    assert call["node_name"] == "review"
    # Prompt 含待评审正文，且不含任何密钥。
    assert "雨夜咖啡馆" in call["prompt"]
    assert "sk-" not in call["prompt"]


def test_review_agent_skips_provider_when_no_text() -> None:
    """缺失场景文本：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = ReviewAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(draft_text=None, accepted_text=""))
    assert output.status == "needs_clarification"
    assert output.clarification_questions
    assert provider.calls == []


def test_review_agent_propagates_provider_error() -> None:
    """Provider 错误（如 LLM_RATE_LIMITED）原样上抛，不产生可提交输出。"""
    provider = _StubProvider(error=AppError("LLM_RATE_LIMITED", "rate limited"))
    agent = ReviewAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RATE_LIMITED"


def test_review_agent_maps_schema_invalid_response() -> None:
    """模型响应不合 ReviewOutput schema：映射为 LLM_RESPONSE_INVALID。"""
    provider = _StubProvider(result={"status": "bogus", "overall_rating": "pass"})
    agent = ReviewAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_review_agent_default_fake_keeps_deterministic_output() -> None:
    """未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = ReviewAgent()
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.review_issues == []
    assert output.overall_rating == "pass"
