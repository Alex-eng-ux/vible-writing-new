"""Task 9 真实 CanonAgent 链路测试（默认 HTTP mock，不访问网络）。

覆盖：
- 成功链路：CanonAgent 经真实 Provider（MockTransport）返回结构化 CanonOutput
  -> 候选幂等持久化且暂停等待作者确认（pending_node=canon_confirmation），
  不生成任何正式 Canon；
- 失败链路：Provider 401 / 超时 / 非法结构化输出 -> ``AppError(LLM_*)`` 上抛
  （运行层据此把运行置 failed），不产生任何候选/正式 Canon 写入；
- 真实模型 smoke（门控）：设置 REAL_MODEL_SMOKE=1 且 LLM_BASE_URL /
  LLM_API_KEY / MODEL_NAME 齐备时，用真实 DeepSeek 跑同一条链路（默认跳过，
  保证普通测试不访问网络）。
"""
from __future__ import annotations

import json
import os

import httpx
import pytest
from sqlalchemy import text

from app.agents.canon_agent import CanonAgent
from app.agents.canon_graph import CanonGraph
from app.agents.hook_registry import HookRegistry
from app.agents.model_provider import DeepSeekModelProvider
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, RuntimeContext
from app.db.models import CanonFact, FactCandidate, Volume
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    commit_chapter_version,
    create_chapter,
    create_chapter_plan_revision,
)
from app.errors import AppError
from app.observability.sink import LocalSink
from app.observability.wiring import ObservabilityWiring

_SYSTEM_CANON = "You are a canon extraction agent"

_CANON_WRITE_TABLES = ("fact_candidates", "canon_facts")


def _author_ctx():
    return {
        "generation_run_id": None,
        "write_fence": None,
        "manual_command_id": "manual-1",
        "source": "author",
        "actor_id": "author-1",
        "idempotency_key": "key-1",
        "expected_run_version": None,
    }


def _make_chapter(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, {"actor_id": "author-1", "idempotency_key": "key-1"})
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _author_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _author_ctx())
    chapter.chapter_sync_status = "in_sync"
    chapter.entry_handoff_status = "in_sync"
    return chapter


def _accept_chapter(db, chapter):
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", _author_ctx())
    commit_chapter_version(db, rev.id, _author_ctx())
    return rev


def _envelope(project_id, chapter_id, accepted_chapter_revision_id) -> AgentInputEnvelope:
    return AgentInputEnvelope(
        runtime_context=RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="chapter",
            decision_target="canon",
            chapter_id=chapter_id,
        ),
        project={"id": project_id, "name": "测试项目"},
        request_type="continue",
        canon_scope="chapter",
        accepted_chapter_revision_id=accepted_chapter_revision_id,
        accepted_text="雨夜咖啡馆，林默与旧友重逢，主角是侦探。",
    )


def _state() -> dict:
    return {
        "generation_run_id": "g1",
        "run_version": 1,
        "project_id": "p1",
        "chapter_id": "ch1",
        "scene_ids": [],
        "run_status": "running",
        "last_durable_node": None,
        "pending_node": None,
        "clarification_questions": [],
        "scene_auto_revision_counts": {},
        "inheritance_map": {},
    }


def _valid_content() -> str:
    return json.dumps(
        {
            "status": "ready",
            "fact_candidates": [
                {
                    "candidate_id": None,
                    "candidate_type": "fact",
                    "local_key": "f1",
                    "claim": "主角是侦探",
                    "status": "pending_author_confirmation",
                    "scope": "chapter",
                    "source": {"chapter_id": "ch1", "source_id": "rev-ch1"},
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
    )


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


def _graph(*, provider, session) -> CanonGraph:
    return CanonGraph(
        session=session,
        registry=HookRegistry(),
        router=AgentResultRouter(),
        agent=CanonAgent(provider=provider),
    )


def _canon_counts(db) -> dict[str, int]:
    """返回候选/正式 Canon 写入表的行数（用于断言失败时无任何数据落库）。"""
    return {
        t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
        for t in _CANON_WRITE_TABLES
    }


def _assert_canon_no_delta(db, before: dict[str, int]) -> None:
    """断言候选/正式 Canon 表行数与运行前一致（模型失败不得写入候选/正式 Canon）。"""
    after = _canon_counts(db)
    changed = {t: (before[t], after[t]) for t in _CANON_WRITE_TABLES if before[t] != after[t]}
    assert not changed, f"unexpected canon write-table deltas: {changed}"


def _setup_accepted_chapter(db, volume) -> tuple[str, str, str]:
    """创建并接受章节版本，返回 (project_id, chapter_id, accepted_chapter_revision_id)。"""
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    return db.get(Volume, volume).project_id, chapter.id, rev.id


def test_real_canon_chain_success_persists_candidates_without_official_write(db, volume) -> None:
    """成功链路：CanonAgent 经 provider 返回候选 -> 持久化并暂停确认，无正式 Canon 写入。"""
    seen_nodes: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_nodes.append("canon")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": _valid_content()}}]})

    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = _real_provider(httpx.MockTransport(handler), wiring=wiring)
    project_id, chapter_id, rev_id = _setup_accepted_chapter(db, volume)
    graph = _graph(provider=provider, session=db)
    result = graph.invoke(_state(), _envelope(project_id, chapter_id, rev_id), thread_id="t-canon-success")
    # CanonAgent 被真实 Provider（mock）调用一次，候选已持久化，暂停等待作者确认。
    assert seen_nodes == ["canon"]
    assert result["run_status"] == "paused"
    assert result["pending_node"] == "canon_confirmation"
    assert db.query(FactCandidate).filter(FactCandidate.project_id == project_id).count() == 1
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    # 观测自动埋点：canon 节点挂载 llm 节点。
    assert wiring.local is not None
    node_names = [str(r.get("node_name")) for r in wiring.local.records]
    assert any(n.startswith("canon:llm:") for n in node_names)


def test_real_canon_chain_401_fails_without_writes(db, volume) -> None:
    """失败链路：Provider 401 -> 抛 LLM_AUTH_ERROR，不产生候选/正式 Canon 写入。"""
    before = _canon_counts(db)
    project_id, chapter_id, rev_id = _setup_accepted_chapter(db, volume)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "auth"}})

    provider = _real_provider(httpx.MockTransport(handler))
    graph = _graph(provider=provider, session=db)
    with pytest.raises(AppError) as exc:
        graph.invoke(_state(), _envelope(project_id, chapter_id, rev_id), thread_id="t-canon-401")
    assert exc.value.code == "LLM_AUTH_ERROR"
    _assert_canon_no_delta(db, before)


def test_real_canon_chain_timeout_fails_without_writes(db, volume) -> None:
    """失败链路：Provider 超时 -> 抛 LLM_UNAVAILABLE，不产生候选/正式 Canon 写入。"""
    before = _canon_counts(db)
    project_id, chapter_id, rev_id = _setup_accepted_chapter(db, volume)

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=req)

    provider = _real_provider(httpx.MockTransport(handler))
    graph = _graph(provider=provider, session=db)
    with pytest.raises(AppError) as exc:
        graph.invoke(_state(), _envelope(project_id, chapter_id, rev_id), thread_id="t-canon-timeout")
    assert exc.value.code == "LLM_UNAVAILABLE"
    _assert_canon_no_delta(db, before)


def test_real_canon_chain_invalid_output_fails_without_writes(db, volume) -> None:
    """失败链路：非法结构化输出 -> 抛 LLM_RESPONSE_INVALID，不产生候选/正式 Canon 写入。"""
    before = _canon_counts(db)
    project_id, chapter_id, rev_id = _setup_accepted_chapter(db, volume)

    def handler(req: httpx.Request) -> httpx.Response:
        content = json.dumps({"status": "bogus", "fact_candidates": []})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})

    provider = _real_provider(httpx.MockTransport(handler))
    graph = _graph(provider=provider, session=db)
    with pytest.raises(AppError) as exc:
        graph.invoke(_state(), _envelope(project_id, chapter_id, rev_id), thread_id="t-canon-invalid")
    assert exc.value.code == "LLM_RESPONSE_INVALID"
    _assert_canon_no_delta(db, before)


@pytest.mark.skipif(
    not os.environ.get("REAL_MODEL_SMOKE")
    or not (os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY") and os.environ.get("MODEL_NAME")),
    reason="REAL_MODEL_SMOKE=1 且 LLM_BASE_URL/LLM_API_KEY/MODEL_NAME 齐备时才执行（默认不访问网络）",
)
def test_real_canon_chain_with_real_deepseek(db, volume) -> None:
    """真实模型 smoke：用真实 DeepSeek 走 Canon 提取链路（门控，默认跳过）。"""
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = DeepSeekModelProvider(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model_name=os.environ["MODEL_NAME"],
        wiring=wiring,
    )
    try:
        project_id, chapter_id, rev_id = _setup_accepted_chapter(db, volume)
        graph = CanonGraph(
            session=db,
            registry=HookRegistry(),
            router=AgentResultRouter(),
            agent=CanonAgent(provider=provider),
        )
        result = graph.invoke(_state(), _envelope(project_id, chapter_id, rev_id), thread_id="t-canon-live")
        assert result["run_status"] == "paused"
        assert result["pending_node"] == "canon_confirmation"
        assert wiring.local is not None
        node_names = [str(r.get("node_name")) for r in wiring.local.records]
        assert any(n.startswith("canon:llm:") for n in node_names)
    finally:
        provider.close()
