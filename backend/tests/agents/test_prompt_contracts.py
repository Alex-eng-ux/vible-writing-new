"""Agent 契约提示词测试（Task 9 真实 Agent 接线契约）。

验证目标：
1. 每个接入 Provider 的 Agent 都使用与自身 Pydantic 输出 schema 对齐的独立
   system prompt（`app.agents.prompts`），提示词包含全部字段名与关键枚举；
2. 对每个提示词补「合法响应 / 非法响应」测试：合法响应通过 schema 校验，
   非法响应被 Pydantic 拒绝并映射为 `LLM_RESPONSE_INVALID`——确认 schema 校验
   仍是最终边界（提示词只引导、不兜底）；
3. CanonAgent 已接入 Provider，`CANON_SYSTEM_PROMPT` 与其输出 schema `CanonOutput`
   对齐（合法响应可解析、非法响应被拒），并验证注入 provider 时使用该提示词；
4. ChapterAggregator 是确定性聚合逻辑，不添加模型提示词（不在本测试范围）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.agents import prompts
from app.agents.canon_agent import CanonAgent
from app.agents.chapter_planner import ChapterPlannerAgent
from app.agents.chapter_review_agent import ChapterReviewAgent
from app.agents.continuity_agent import ContinuityAgent
from app.agents.review_agent import ReviewAgent
from app.agents.revision_agent import RevisionAgent
from app.agents.schemas import (
    AgentInputEnvelope,
    CanonOutput,
    RuntimeContext,
)
from app.agents.writing_agent import WritingAgent
from app.errors import AppError

# ----------------------------------------------------------------------
# 桩 Provider：返回固定结果或抛错，并记录收到的 system_prompt
# ----------------------------------------------------------------------


class _StubProvider:
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.system_prompt: str | None = None

    def invoke_structured(
        self,
        *,
        prompt: str,
        generation_run_id: str,
        agent_run_id: str,
        node_name: str,
        system_prompt: str,
    ) -> dict:
        self.system_prompt = system_prompt
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        raise AssertionError("stub provider requires a result")


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
        "scene_brief": {"goal": "重逢"},
        "accepted_text": "雨夜咖啡馆，林默与旧友重逢。",
    }
    base.update(overrides)
    return AgentInputEnvelope(**base)


# ----------------------------------------------------------------------
# 各 Agent 的构造函数与合法/非法响应
# ----------------------------------------------------------------------


def _valid_writing() -> dict:
    return {
        "status": "ready",
        "mode": "continue",
        "content": "雨夜咖啡馆，林默推门而入。",
        "candidate_facts": [],
        "unresolved_assumptions": [],
        "context_source_refs": [],
        "evidence_refs": [],
        "clarification_questions": [],
    }


def _invalid_writing() -> dict:
    # 枚举非法：mode 必须是 draft|continue|rewrite。
    return {"status": "ready", "mode": "bogus", "content": "x"}


def _valid_continuity() -> dict:
    return {"status": "pass", "scene_snapshot_delta": {}, "issues": [], "clarification_questions": []}


def _invalid_continuity() -> dict:
    # 枚举非法：status 必须是 pass|issues|needs_author_confirmation|needs_clarification。
    return {"status": "bogus", "scene_snapshot_delta": {}}


def _valid_review() -> dict:
    return {
        "status": "ready",
        "review_issues": [],
        "overall_rating": "pass",
        "submitted": False,
        "clarification_questions": [],
    }


def _invalid_review() -> dict:
    # 枚举非法：status 必须是 ready|needs_clarification。
    return {"status": "bogus", "overall_rating": "pass"}


def _valid_revision() -> dict:
    return {
        "status": "ready",
        "base_scene_revision_id": "rev-1",
        "operation_format": "semantic_text",
        "operations": [
            {"op": "replace", "old_text": "", "new_text": "修订", "reason": "r", "source": "author_feedback"}
        ],
        "candidate_facts": [],
        "remaining_risks": [],
        "clarification_questions": [],
        "evidence_refs": [],
    }


def _invalid_revision() -> dict:
    # 枚举非法：operation_format 必须是 semantic_text。
    return {"status": "ready", "base_scene_revision_id": "rev-1", "operation_format": "markdown"}


def _valid_chapter_plan() -> dict:
    return {
        "status": "ready",
        "chapter_contract": {"title": "第一章"},
        "scene_contracts": [{"client_key": "s1", "title": "Scene", "scene_brief": {}}],
        "reason": "planned",
        "clarification_questions": [],
    }


def _invalid_chapter_plan() -> dict:
    # 枚举非法：status 必须是 ready|needs_clarification。
    return {"status": "bogus", "reason": "x"}


def _valid_chapter_review() -> dict:
    return {
        "status": "ready",
        "review_issues": [],
        "overall_rating": "pass",
        "submitted": True,
        "clarification_questions": [],
    }


def _invalid_chapter_review() -> dict:
    # 枚举非法：status 必须是 ready|needs_clarification。
    return {"status": "bogus", "overall_rating": "pass"}


def _valid_canon() -> dict:
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
                "source": {"source_id": "src-1"},
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


def _invalid_canon() -> dict:
    # 枚举非法：candidate_type 必须是 fact|timeline_event|plot_thread。
    return {
        "status": "ready",
        "fact_candidates": [
            {
                "candidate_type": "bogus",
                "local_key": "f1",
                "claim": "x",
                "status": "pending_author_confirmation",
                "scope": "chapter",
            }
        ],
    }


# 每个 Agent：构造器 + 能够触达模型的信封。
_AGENTS: list[tuple[str, Callable[[Any], Any], Callable[[], AgentInputEnvelope], Callable[[], dict], Callable[[], dict] | None]] = [
    ("writing", lambda p: WritingAgent(provider=p), _envelope, _valid_writing, _invalid_writing),
    ("continuity", lambda p: ContinuityAgent(provider=p), _envelope, _valid_continuity, _invalid_continuity),
    ("review", lambda p: ReviewAgent(provider=p), _envelope, _valid_review, _invalid_review),
    (
        "revision",
        lambda p: RevisionAgent(provider=p),
        lambda: _envelope(base_scene_revision_id="rev-1", author_feedback={"text": "改含蓄些"}),
        _valid_revision,
        _invalid_revision,
    ),
    (
        "chapter_planner",
        lambda p: ChapterPlannerAgent(provider=p),
        lambda: _envelope(chapter_contract={"pov": "p", "scene_keys": ["s1"]}),
        _valid_chapter_plan,
        _invalid_chapter_plan,
    ),
    (
        "chapter_review",
        lambda p: ChapterReviewAgent(provider=p),
        lambda: _envelope(chapter_contract={"pov": "p", "scene_keys": ["s1"]}),
        _valid_chapter_review,
        _invalid_chapter_review,
    ),
    (
        "canon",
        lambda p: CanonAgent(provider=p),
        lambda: _envelope(canon_scope="chapter", accepted_chapter_revision_id="rev-c1"),
        _valid_canon,
        _invalid_canon,
    ),
]

_ALL_PROMPTS = {
    "WRITING_SYSTEM_PROMPT": prompts.WRITING_SYSTEM_PROMPT,
    "CONTINUITY_SYSTEM_PROMPT": prompts.CONTINUITY_SYSTEM_PROMPT,
    "REVIEW_SYSTEM_PROMPT": prompts.REVIEW_SYSTEM_PROMPT,
    "REVISION_SYSTEM_PROMPT": prompts.REVISION_SYSTEM_PROMPT,
    "CHAPTER_PLAN_SYSTEM_PROMPT": prompts.CHAPTER_PLAN_SYSTEM_PROMPT,
    "CHAPTER_REVIEW_SYSTEM_PROMPT": prompts.CHAPTER_REVIEW_SYSTEM_PROMPT,
    "CANON_SYSTEM_PROMPT": prompts.CANON_SYSTEM_PROMPT,
}


# ----------------------------------------------------------------------
# 1. 每个接入 Provider 的 Agent 使用独立提示词，且提示词覆盖其字段/枚举
# ----------------------------------------------------------------------


def test_each_agent_uses_distinct_system_prompt() -> None:
    """七个提示词互不相同，且均非空。"""
    seen = set()
    for name, text in _ALL_PROMPTS.items():
        assert text.strip(), f"{name} must not be empty"
        assert text not in seen, f"{name} duplicates another prompt"
        seen.add(text)


@pytest.mark.parametrize("prompt_name", list(_ALL_PROMPTS))
def test_prompt_contains_status_enum(prompt_name: str) -> None:
    """每个提示词都声明 status 字段及其合法枚举。"""
    text = _ALL_PROMPTS[prompt_name]
    assert '"status"' in text
    assert "needs_clarification" in text


@pytest.mark.parametrize(
    "prompt_name,required_tokens",
    [
        ("WRITING_SYSTEM_PROMPT", ['"mode"', '"draft"', '"continue"', '"rewrite"', '"content"', '"candidate_facts"']),
        ("CONTINUITY_SYSTEM_PROMPT", ['"pass"', '"issues"', '"needs_author_confirmation"', '"scene_snapshot_delta"', '"issues"']),
        ("REVIEW_SYSTEM_PROMPT", ['"review_issues"', '"overall_rating"', '"submitted"', '"issue_type"', '"severity"']),
        ("REVISION_SYSTEM_PROMPT", ['"base_scene_revision_id"', '"operation_format"', '"semantic_text"', '"operations"', '"op"', '"replace"', '"insert"', '"delete"']),
        ("CHAPTER_PLAN_SYSTEM_PROMPT", ['"scene_contracts"', '"client_key"', '"title"', '"scene_brief"', '"reason"']),
        ("CHAPTER_REVIEW_SYSTEM_PROMPT", ['"review_issues"', '"overall_rating"', '"submitted"', '"issue_type"', '"severity"']),
        ("CANON_SYSTEM_PROMPT", ['"fact_candidates"', '"timeline_event_candidates"', '"plot_thread_updates"', '"candidate_type"', '"fact"', '"timeline_event"', '"plot_thread"', '"scope"', '"chapter"', '"scene"', '"pending_author_confirmation"', '"effective_story_time"', '"precision"', '"exact"', '"range"', '"relative"', '"unknown"', '"narrative_knowledge"', '"resolution_action"']),
    ],
)
def test_prompt_covers_schema_fields_and_enums(prompt_name: str, required_tokens: list[str]) -> None:
    """提示词逐项覆盖输出 schema 的关键字段与枚举。"""
    text = _ALL_PROMPTS[prompt_name]
    missing = [t for t in required_tokens if t not in text]
    assert not missing, f"{prompt_name} 缺少 schema 元素: {missing}"


# ----------------------------------------------------------------------
# 2. 每个接线 Agent：合法响应通过 schema，非法响应被拒（schema 是最终边界）
# ----------------------------------------------------------------------


def test_valid_responses_pass_schema() -> None:
    """对每个接线 Agent：合法响应通过 schema 校验并返回结构化输出。"""
    for name, make_agent, make_envelope, valid, _ in _AGENTS:
        provider = _StubProvider(result=valid())
        output = make_agent(provider).run(make_envelope())
        assert output.status in {"ready", "pass"}, f"{name} 合法响应未通过 schema"
        # 该 Agent 确实注入了与自身对齐的提示词。
        assert provider.system_prompt is not None and provider.system_prompt.strip()


def test_invalid_responses_are_rejected_as_final_boundary() -> None:
    """对每个接入 Provider 的 Agent：非法响应被 Pydantic 拒绝并映射为 LLM_RESPONSE_INVALID。"""
    for name, make_agent, make_envelope, _, invalid in _AGENTS:
        assert invalid is not None
        provider = _StubProvider(result=invalid())
        with pytest.raises(AppError) as exc:
            make_agent(provider).run(make_envelope())
        assert exc.value.code == "LLM_RESPONSE_INVALID", f"{name} 非法响应未被 schema 拒绝"


# ----------------------------------------------------------------------
# 3. CanonAgent（尚未接 Provider）：提示词与 CanonOutput 对齐的测试设计
# ----------------------------------------------------------------------


def test_canon_prompt_aligned_with_canon_output_valid() -> None:
    """Canon：合法响应可直接通过 CanonOutput schema 校验（边界在 schema）。"""
    output = CanonOutput(**_valid_canon())
    assert output.status == "ready"
    assert output.fact_candidates[0].candidate_type == "fact"
    assert output.fact_candidates[0].scope == "chapter"


def test_canon_prompt_aligned_with_canon_output_invalid() -> None:
    """Canon：非法响应（枚举越界）被 CanonOutput schema 拒绝，无法被当作合法结果。"""
    with pytest.raises(Exception):
        CanonOutput(**_invalid_canon())


def test_canon_agent_uses_canon_system_prompt_when_provider_injected() -> None:
    """CanonAgent 已接入 Provider：注入 provider 时使用 CANON_SYSTEM_PROMPT 且经 schema 校验。"""
    provider = _StubProvider(result=_valid_canon())
    agent = CanonAgent(provider=provider)  # type: ignore[arg-type]
    output = agent.run(_envelope(canon_scope="chapter", accepted_chapter_revision_id="rev-c1"))
    assert output.status == "ready"
    assert provider.system_prompt == prompts.CANON_SYSTEM_PROMPT


def test_canon_agent_default_fake_keeps_deterministic_output() -> None:
    """CanonAgent 未注入 Provider：保持 Fake 确定性输出（默认测试不访问网络）。"""
    agent = CanonAgent()
    assert agent._fake is True  # 未注入模型 Provider 时仍为占位实现。
    assert prompts.CANON_SYSTEM_PROMPT.strip()
