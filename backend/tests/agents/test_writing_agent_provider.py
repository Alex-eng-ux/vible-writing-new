"""WritingAgent 真实 Provider 接线测试（stub Provider，不访问网络）。

覆盖：注入 Provider 时生成并校验 WritingOutput（Prompt 含场景上下文）、缺少
简介/上下文时不调用模型返回 needs_clarification、Provider 错误原样上抛、
模型响应不合 schema 时映射 LLM_RESPONSE_INVALID、未注入 Provider 保持
Fake 确定性输出（默认测试不访问网络）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.agents.writing_agent import WritingAgent
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
    }
    base.update(overrides)
    return AgentInputEnvelope(**base)


class _StubProvider:
    """记录调用并返回固定结果或抛错的桩 Provider。"""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, str]] = []

    def invoke_structured(
        self, *, prompt: str, generation_run_id: str, agent_run_id: str, node_name: str, system_prompt: str
    ) -> dict:
        self.calls.append(
            {
                "prompt": prompt,
                "generation_run_id": generation_run_id,
                "agent_run_id": agent_run_id,
                "node_name": node_name,
                "system_prompt": system_prompt,
            }
        )
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return {
            "status": "ready",
            "mode": "continue",
            "content": "雨夜咖啡馆，林默与旧友重逢。",
            "candidate_facts": [],
            "unresolved_assumptions": [],
            "context_source_refs": [],
            "evidence_refs": [],
            "clarification_questions": [],
        }


def test_writing_agent_with_provider_returns_validated_output() -> None:
    """注入 Provider：调用模型并返回经 WritingOutput 校验的结构化草稿。"""
    provider = _StubProvider()
    agent = WritingAgent(provider=provider)  # type: ignore[arg-type]
    env = _envelope(
        scene_brief={"goal": "重逢", "summary": "雨夜咖啡馆"},
        context_manifest=[{"source_id": "rev-1", "kind": "revision", "revision_id": "rev-1"}],
    )
    output = agent.run(env)
    assert output.status == "ready"
    assert output.mode == "continue"
    assert "雨夜咖啡馆" in output.content
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["generation_run_id"] == "g1"
    assert call["node_name"] == "writing"
    # Prompt 包含场景上下文，但不含任何密钥。
    assert "重逢" in call["prompt"]
    assert "rev-1" in call["prompt"]
    assert "sk-" not in call["prompt"]


def test_writing_agent_skips_provider_when_no_brief_or_context() -> None:
    """缺少场景简介与上下文：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = WritingAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(scene_brief={}, context_pack=[]))
    assert output.status == "needs_clarification"
    assert provider.calls == []


def test_writing_agent_propagates_provider_error() -> None:
    """Provider 错误（如 LLM_UNAVAILABLE）原样上抛，不产生可提交输出。"""
    provider = _StubProvider(error=AppError("LLM_UNAVAILABLE", "model provider unavailable"))
    agent = WritingAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope(scene_brief={"goal": "x"}))
    assert exc.value.code == "LLM_UNAVAILABLE"


def test_writing_agent_maps_schema_invalid_response() -> None:
    """模型响应不合 WritingOutput schema：映射为 LLM_RESPONSE_INVALID。"""
    provider = _StubProvider(result={"status": "bogus", "mode": "draft", "content": "x"})
    agent = WritingAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope(scene_brief={"goal": "x"}))
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_writing_agent_default_fake_keeps_deterministic_output() -> None:
    """未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = WritingAgent()
    output = agent.run(
        _envelope(
            scene_brief={"goal": "x"},
            request_type="new_chapter",
            context_manifest=[{"source_id": "rev-1", "kind": "revision", "revision_id": "rev-1"}],
        )
    )
    assert output.status == "ready"
    assert output.mode == "draft"
    assert "Generated scene content" in output.content
    assert output.context_source_refs == ["rev-1"]
