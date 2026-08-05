"""Task 9 真实 WritingAgent 链路测试（默认 HTTP mock，不访问网络）。

覆盖：
- 成功链路：创建场景运行 -> WritingAgent 经真实 Provider（MockTransport）
  返回结构化草稿 -> 运行进入作者确认态（waiting_feedback / pending_clarification），
  不创建任何版本/候选/Canon 数据；
- 失败链路：Provider 401 -> 运行 failed + last_error_code=LLM_AUTH_ERROR，
  不创建任何版本/候选/Canon 数据；
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
from sqlalchemy.orm import sessionmaker

from app.agents.model_provider import DeepSeekModelProvider
from app.db.models import GenerationRun, RunEvent
from app.observability.sink import LocalSink
from app.observability.wiring import ObservabilityWiring
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_worker import RunWorker
from tests.acceptance.test_hashes import _hierarchy

_WRITE_TABLES = (
    "scene_revisions",
    "scene_draft_artifacts",
    "canon_facts",
    "timeline_events",
    "plot_threads",
    "fact_candidates",
)


@pytest.fixture(autouse=True)
def _cleanup_committed_runs(db):
    """清除 worker 测试提交到共享测试库的运行与写入数据（保证隔离）。"""
    db.execute(text("DELETE FROM run_events"))
    db.execute(text("DELETE FROM run_leases"))
    db.execute(text("DELETE FROM generation_runs"))
    db.commit()
    yield


def _counts(db) -> dict[str, int]:
    """返回各权威/候选写入表的行数（用于断言失败时无任何数据落库）。"""
    return {
        t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
        for t in _WRITE_TABLES
    }


def _assert_no_writes(db, before: dict[str, int]) -> None:
    """断言权威/候选写入表行数与运行前一致（真实模型不得创建版本/候选/Canon）。"""
    after = _counts(db)
    changed = {t: (before[t], after[t]) for t in _WRITE_TABLES if before[t] != after[t]}
    assert not changed, f"unexpected write-table deltas: {changed}"


def _create_scene_run_with_brief(db, run_id: str) -> str:
    """创建带场景简介的 queued 场景运行（scene_brief 非空保证 WritingAgent 调模型）。"""
    project, scene = _hierarchy(db)
    scene.scene_brief = {"goal": "重逢", "summary": "雨夜咖啡馆，林默与旧友重逢", "target": "角色" }
    db.flush()
    run = GenerationRun(
        id=run_id,
        project_id=project.id,
        chapter_id=scene.chapter_id,
        scene_id=scene.id,
        status="queued",
        run_version=1,
        request_type="continue",
        decision_target="scene",
        normalized_input={"run_scope": "scene", "request_type": "continue", "decision_target": "scene"},
    )
    db.add(run)
    db.flush()
    PostgresRunEventStore(db).emit(run_id, "run_queued", {"run_scope": "scene", "request_type": "continue"}, fencing_token=0)
    db.commit()
    return scene.id


def _events(db, run_id: str) -> list[str]:
    rows = db.execute(
        RunEvent.__table__.select()
        .where(RunEvent.__table__.c.generation_run_id == run_id)
        .order_by(RunEvent.__table__.c.sequence)
    ).all()
    return [r.event_type for r in rows]


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


def _valid_content() -> str:
    return json.dumps(
        {
            "status": "ready",
            "mode": "continue",
            "content": "雨夜咖啡馆的灯光昏黄，林默推门而入，与旧友重逢。",
            "candidate_facts": [],
            "unresolved_assumptions": [],
            "context_source_refs": [],
            "evidence_refs": [],
            "clarification_questions": [],
        }
    )


def test_real_writing_chain_success_reaches_author_confirmation(db) -> None:
    """成功链路：创建场景运行 -> WritingAgent(真实 Provider/mock) -> 作者确认态，无版本写入。"""
    before = _counts(db)
    run_id = "g-real-1"
    _create_scene_run_with_brief(db, run_id)
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("authorization", ""))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": _valid_content()}}]})

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = _real_provider(httpx.MockTransport(handler), wiring=wiring)
    worker = RunWorker(factory, actor_id="worker-1", observability=wiring, provider=provider)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    # 进入作者确认态（等待反馈或澄清）；事件序列 run_queued -> 结果事件。
    assert row.status in ("waiting_feedback", "pending_clarification")
    events = _events(db, run_id)
    assert events[0] == "run_queued"
    assert len(events) == 2
    # Provider 被调用一次且携带 Bearer Key；无任何版本/候选/Canon 落库。
    assert len(seen) == 1 and seen[0] == "Bearer sk-test-key"
    _assert_no_writes(db, before)
    # 观测自动埋点：writing 节点存在（含 llm 调用层级节点）。
    assert wiring.local is not None
    node_names = [r.get("node_name") for r in wiring.local.records]
    assert any(str(n).startswith("writing:llm:") for n in node_names)


def test_real_writing_chain_401_fails_without_writes(db) -> None:
    """失败链路：Provider 401 -> 运行 failed + LLM_AUTH_ERROR，无任何版本/候选/Canon 落库。"""
    before = _counts(db)
    run_id = "g-real-2"
    _create_scene_run_with_brief(db, run_id)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "auth"}})

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = _real_provider(httpx.MockTransport(handler), wiring=wiring)
    worker = RunWorker(factory, actor_id="worker-1", observability=wiring, provider=provider)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    assert row.status == "failed"
    assert row.last_error_code == "LLM_AUTH_ERROR"
    assert _events(db, run_id) == ["run_queued", "run_failed"]
    _assert_no_writes(db, before)


def test_real_writing_chain_timeout_fails_without_writes(db) -> None:
    """失败链路：Provider 超时 -> 运行 failed + LLM_UNAVAILABLE，无任何版本落库。"""
    before = _counts(db)
    run_id = "g-real-3"
    _create_scene_run_with_brief(db, run_id)

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=req)

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    provider = _real_provider(httpx.MockTransport(handler))
    worker = RunWorker(factory, actor_id="worker-1", provider=provider)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    assert row.status == "failed"
    assert row.last_error_code == "LLM_UNAVAILABLE"
    _assert_no_writes(db, before)


def test_real_writing_chain_retries_then_succeeds_without_duplicate_writes(db) -> None:
    """重试幂等：Provider 先 429 后成功 -> 自动重试一次成功，运行进入作者确认态，无重复写入。"""
    before = _counts(db)
    run_id = "g-real-retry"
    _create_scene_run_with_brief(db, run_id)
    attempts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "limit"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": _valid_content()}}]})

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5))
    provider = DeepSeekModelProvider(
        base_url="https://api.deepseek.com",
        api_key="sk-test-key",
        model_name="deepseek-v4-flash",
        wiring=wiring,
        http_client=client,
        max_retries=2,
        sleep=lambda d: None,  # 测试注入：不做真实休眠，仅验证重试次数与幂等。
    )
    try:
        worker = RunWorker(factory, actor_id="worker-1", observability=wiring, provider=provider)
        assert worker.tick() == 1
        db.expire_all()
        row = db.get(GenerationRun, run_id)
        # 重试后成功：进入作者确认态，出站调用两次（首次 429 + 重试 200）。
        assert row.status in ("waiting_feedback", "pending_clarification")
        assert attempts["n"] == 2
        events = _events(db, run_id)
        # 重试只发生在模型出站调用层：事件序列无失败、无重复写入事件。
        assert events[0] == "run_queued"
        assert "run_failed" not in events
        assert len(events) == len(set(events))
        # 重试不产生重复版本/候选/Canon 写入。
        _assert_no_writes(db, before)
    finally:
        provider.close()


@pytest.mark.skipif(
    not os.environ.get("REAL_MODEL_SMOKE")
    or not (os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY") and os.environ.get("MODEL_NAME")),
    reason="REAL_MODEL_SMOKE=1 且 LLM_BASE_URL/LLM_API_KEY/MODEL_NAME 齐备时才执行（默认不访问网络）",
)
def test_real_writing_chain_with_real_deepseek(db) -> None:
    """真实模型 smoke：用真实 DeepSeek 走完整链路（门控，默认跳过）。"""
    before = _counts(db)
    run_id = "g-real-live"
    _create_scene_run_with_brief(db, run_id)
    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = DeepSeekModelProvider(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model_name=os.environ["MODEL_NAME"],
        wiring=wiring,
    )
    try:
        worker = RunWorker(factory, actor_id="worker-1", observability=wiring, provider=provider)
        assert worker.tick() == 1
        db.expire_all()
        row = db.get(GenerationRun, run_id)
        assert row.status in ("waiting_feedback", "pending_clarification")
        assert _events(db, run_id)[0] == "run_queued"
        _assert_no_writes(db, before)
        assert wiring.local is not None
        node_names = [r.get("node_name") for r in wiring.local.records]
        assert any(str(n).startswith("writing:llm:") for n in node_names)
    finally:
        provider.close()
