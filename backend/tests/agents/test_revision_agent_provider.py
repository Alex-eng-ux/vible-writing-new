"""RevisionAgent 真实 Provider 接线测试（stub Provider，不访问网络）。

覆盖：注入 Provider 时生成并校验 RevisionOutput（Prompt 含基线版本与作者反馈）、
缺少基线/作者反馈时不调用模型返回 needs_clarification、Provider 错误原样上抛、
模型响应不合 schema 时映射 LLM_RESPONSE_INVALID、未注入 Provider 保持 Fake
确定性输出（默认测试不访问网络）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.revision_agent import RevisionAgent
from app.agents.schemas import AgentInputEnvelope, AuthorFeedback, RuntimeContext
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
        "base_scene_revision_id": "rev-1",
        "author_feedback": AuthorFeedback(text="把重逢写得更含蓄一些"),
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
            "base_scene_revision_id": "rev-1",
            "operation_format": "semantic_text",
            "operations": [
                {"op": "replace", "old_text": "", "new_text": "revised", "reason": "author feedback", "source": "author_feedback"}
            ],
            "candidate_facts": [],
            "remaining_risks": [],
            "clarification_questions": [],
            "evidence_refs": [],
        }


def test_revision_agent_with_provider_returns_validated_output() -> None:
    """注入 Provider：调用模型并返回经 RevisionOutput 校验的 ChangeSet。"""
    provider = _StubProvider()
    agent = RevisionAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.base_scene_revision_id == "rev-1"
    assert output.operations[0].new_text == "revised"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["generation_run_id"] == "g1"
    assert call["node_name"] == "revision"
    # Prompt 含基线版本、作者反馈与原稿，但不含任何密钥。
    assert "rev-1" in call["prompt"]
    assert "含蓄" in call["prompt"]
    assert "sk-" not in call["prompt"]


def test_revision_agent_skips_provider_when_no_base() -> None:
    """缺失合法基线：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = RevisionAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(base_scene_revision_id=None))
    assert output.status == "needs_clarification"
    assert output.clarification_questions
    assert provider.calls == []


def test_revision_agent_skips_provider_when_no_feedback() -> None:
    """缺少作者反馈/评审问题：不调用模型，直接返回 needs_clarification。"""
    provider = _StubProvider()
    agent = RevisionAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(author_feedback=AuthorFeedback()))
    assert output.status == "needs_clarification"
    assert output.clarification_questions
    assert provider.calls == []


def test_revision_agent_propagates_provider_error() -> None:
    """Provider 错误（如 LLM_UNAVAILABLE）原样上抛，不产生可提交输出。"""
    provider = _StubProvider(error=AppError("LLM_UNAVAILABLE", "model provider unavailable"))
    agent = RevisionAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_UNAVAILABLE"


def test_revision_agent_maps_schema_invalid_response() -> None:
    """模型响应不合 RevisionOutput schema：映射为 LLM_RESPONSE_INVALID。"""
    provider = _StubProvider(result={"status": "bogus", "base_scene_revision_id": "rev-1"})
    agent = RevisionAgent(provider=provider)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        agent.run(_envelope())
    assert exc.value.code == "LLM_RESPONSE_INVALID"


def test_revision_agent_default_fake_keeps_deterministic_output() -> None:
    """未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = RevisionAgent()
    output = agent.run(_envelope())
    assert output.status == "ready"
    assert output.base_scene_revision_id == "rev-1"
    assert output.operations[0].new_text == "revised"
