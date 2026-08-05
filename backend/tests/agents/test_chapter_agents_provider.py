"""ChapterPlannerAgent / ChapterReviewAgent 真实 Provider 接线测试（stub Provider，不访问网络）。

覆盖（两类章节 Agent 共用同一桩 Provider 与信封工厂）：
- 注入 Provider 时生成并校验对应的 ChapterPlanOutput / ChapterReviewOutput；
- 缺少章节契约时不调用模型返回 needs_clarification；
- Provider 错误（LLM_*）原样上抛；
- 模型响应不合 schema 时映射 LLM_RESPONSE_INVALID；
- 未注入 Provider 保持 Fake 确定性输出（默认测试不访问网络）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.chapter_planner import ChapterPlannerAgent
from app.agents.chapter_review_agent import ChapterReviewAgent
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.errors import AppError


def _envelope(**overrides: object) -> AgentInputEnvelope:
    base: dict[str, Any] = {
        "runtime_context": RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="chapter",
            decision_target="chapter",
            chapter_id="c1",
        ),
        "request_type": "new_chapter",
        "chapter_contract": {"title": "第一章", "pov": "pov", "scene_keys": ["scene-1", "scene-2"]},
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
        if node_name == "chapter_planner":
            return {
                "status": "ready",
                "chapter_contract": {"title": "第一章"},
                "scene_contracts": [{"client_key": "scene-1", "title": "Scene scene-1", "scene_brief": {}}],
                "reason": "planned",
                "clarification_questions": [],
            }
        return {
            "status": "ready",
            "review_issues": [],
            "overall_rating": "pass",
            "submitted": True,
            "clarification_questions": [],
        }


# ----------------------------------------------------------------------
# ChapterPlannerAgent
# ----------------------------------------------------------------------


def test_chapter_planner_with_provider_returns_validated_output() -> None:
    """注入 Provider：调用模型并返回经 ChapterPlanOutput 校验的结构化结果。"""
    provider = _StubProvider()
    agent = ChapterPlannerAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.scene_contracts[0]["client_key"] == "scene-1"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["generation_run_id"] == "g1"
    assert call["node_name"] == "chapter_planner"
    # Prompt 含章节契约，且不含任何密钥。
    assert "第一章" in call["prompt"]
    assert "sk-" not in call["prompt"]


def test_chapter_planner_skips_provider_when_no_contract() -> None:
    """缺失章节契约：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = ChapterPlannerAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(chapter_contract={}))
    assert output.status == "needs_clarification"
    assert output.clarification_questions
    assert provider.calls == []


def test_chapter_planner_propagates_provider_error() -> None:
    """Provider 错误（如 LLM_UNAVAILABLE）原样上抛，不产生可提交输出。"""
    provider = _StubProvider(error=AppError("LLM_UNAVAILABLE", "model provider unavailable"))
    agent = ChapterPlannerAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_UNAVAILABLE"


def test_chapter_planner_maps_schema_invalid_response() -> None:
    """模型响应不合 ChapterPlanOutput schema：映射为 LLM_RESPONSE_INVALID。"""
    provider = _StubProvider(result={"status": "bogus", "reason": "x"})
    agent = ChapterPlannerAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_chapter_planner_default_fake_keeps_deterministic_output() -> None:
    """未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = ChapterPlannerAgent()
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert [s["client_key"] for s in output.scene_contracts] == ["scene-1", "scene-2"]


# ----------------------------------------------------------------------
# ChapterReviewAgent
# ----------------------------------------------------------------------


def test_chapter_review_with_provider_returns_validated_output() -> None:
    """注入 Provider：调用模型并返回经 ChapterReviewOutput 校验的结构化结果。"""
    provider = _StubProvider()
    agent = ChapterReviewAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.overall_rating == "pass"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["generation_run_id"] == "g1"
    assert call["node_name"] == "chapter_review"
    # Prompt 含章节契约，且不含任何密钥。
    assert "第一章" in call["prompt"]
    assert "sk-" not in call["prompt"]


def test_chapter_review_skips_provider_when_no_contract() -> None:
    """缺失章节契约：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = ChapterReviewAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(chapter_contract={}))
    assert output.status == "needs_clarification"
    assert output.clarification_questions
    assert provider.calls == []


def test_chapter_review_propagates_provider_error() -> None:
    """Provider 错误（如 LLM_RATE_LIMITED）原样上抛，不产生可提交输出。"""
    provider = _StubProvider(error=AppError("LLM_RATE_LIMITED", "rate limited"))
    agent = ChapterReviewAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RATE_LIMITED"


def test_chapter_review_maps_schema_invalid_response() -> None:
    """模型响应不合 ChapterReviewOutput schema：映射为 LLM_RESPONSE_INVALID。"""
    provider = _StubProvider(result={"status": "bogus", "overall_rating": "pass"})
    agent = ChapterReviewAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_chapter_review_default_fake_keeps_deterministic_output() -> None:
    """未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = ChapterReviewAgent()
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.review_issues == []
    assert output.overall_rating == "pass"
