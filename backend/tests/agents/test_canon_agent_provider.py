"""CanonAgent 真实 Provider 接线测试（stub Provider，不访问网络）。

覆盖：
- 注入 Provider 时生成并校验 `CanonOutput`；
- 缺少已接受版本指针时不调用模型返回 needs_clarification；
- Provider 错误（LLM_*）原样上抛；
- 模型响应不合 schema 时映射 LLM_RESPONSE_INVALID；
- 未注入 Provider 保持 Fake 确定性输出（默认测试不访问网络）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.canon_agent import CanonAgent
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
            decision_target="canon",
            chapter_id="c1",
        ),
        "project": {"id": "p1", "name": "测试项目"},
        "request_type": "continue",
        "canon_scope": "chapter",
        "accepted_chapter_revision_id": "rev-ch1",
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
        return {
            "status": "ready",
            "fact_candidates": [
                {
                    "candidate_id": None,
                    "candidate_type": "fact",
                    "local_key": "f1",
                    "claim": "林默是旧友",
                    "status": "pending_author_confirmation",
                    "scope": "chapter",
                    "source": {"chapter_id": "c1", "source_id": "rev-ch1"},
                    "effective_story_time": {"value": "", "precision": "unknown"},
                    "narrative_knowledge": "objective",
                    "resolution_action": "confirm_existing",
                    "entities": [],
                }
            ],
            "timeline_event_candidates": [],
            "plot_thread_updates": [],
            "ambiguous_claims": [],
            "clarification_questions": [],
            "evidence_refs": [],
        }


def test_canon_agent_with_provider_returns_validated_output() -> None:
    """注入 Provider：调用模型并返回经 CanonOutput 校验的结构化结果。"""
    provider = _StubProvider()
    agent = CanonAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.fact_candidates[0].candidate_type == "fact"
    assert output.fact_candidates[0].scope == "chapter"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["generation_run_id"] == "g1"
    assert call["node_name"] == "canon"
    # Prompt 含已接受正文，且不含任何密钥。
    assert "林默与旧友重逢" in call["prompt"]
    assert "sk-" not in call["prompt"]


def test_canon_agent_skips_provider_when_no_accepted_revision() -> None:
    """缺已接受章节版本：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = CanonAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(accepted_chapter_revision_id=None))
    assert output.status == "needs_clarification"
    assert output.clarification_questions
    assert provider.calls == []


def test_canon_agent_propagates_provider_error() -> None:
    """Provider 错误（如 LLM_UNAVAILABLE）原样上抛，不产生可提交输出。"""
    provider = _StubProvider(error=AppError("LLM_UNAVAILABLE", "model provider unavailable"))
    agent = CanonAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_UNAVAILABLE"


def test_canon_agent_maps_schema_invalid_response() -> None:
    """模型响应不合 CanonOutput schema：映射为 LLM_RESPONSE_INVALID。"""
    provider = _StubProvider(result={"status": "bogus", "fact_candidates": []})
    agent = CanonAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_canon_agent_default_fake_keeps_deterministic_output() -> None:
    """未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = CanonAgent()
    output = agent.run(
        _envelope(
            snapshot_before={
                "candidates": [
                    {"candidate_type": "fact", "local_key": "f1", "claim": "主角是侦探"},
                    {"candidate_type": "timeline_event", "local_key": "e1", "claim": "第 3 章到达"},
                ]
            }
        )
    )
    assert output.status == "ready"
    assert [c.local_key for c in output.fact_candidates] == ["f1"]
    assert [c.local_key for c in output.timeline_event_candidates] == ["e1"]
