"""Task 9 Worker 驱动章节链路测试（默认 HTTP mock，不访问网络）。

覆盖修复 worker 层章节信封缺 `chapter_contract` 的问题：
- 成功链路：创建已接受章节计划 + 章节运行 -> Worker 经真实 Provider（MockTransport）
  ChapterPlannerAgent -> ChapterReviewAgent 依次返回结构化结果 -> 聚合节点因未装配
  aggregator 而暂停（不产生任何版本/Canon/handoff 写入）；
- 缺失契约：章节无已接受计划 -> chapter_contract 为空 -> planner 进入
  needs_clarification（不触达模型），运行 pending_clarification；
- 失败不写库：Provider 401 -> 运行 failed + LLM_AUTH_ERROR，不产生任何业务写入；
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
from app.db.models import Chapter, ChapterPlanRevision, GenerationRun, RunEvent, Volume
from app.domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter,
    create_chapter_plan_revision,
)
from app.domain.resources import create_project, create_volume
from app.observability.sink import LocalSink
from app.observability.wiring import ObservabilityWiring
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_worker import RunWorker

_SYSTEM_PLANNER = "You are a chapter planning agent"
_SYSTEM_REVIEW = "You are a chapter review agent"

# 章节运行在 worker 下不会创建的业务表（聚合未装配，不写版本/handoff/Canon）。
_WRITE_TABLES = (
    "chapter_revisions",
    "chapter_handoffs",
    "fact_candidates",
    "canon_facts",
)

_CTX = {"actor_id": "author-1", "idempotency_key": "worker-ch"}


@pytest.fixture(autouse=True)
def _cleanup_committed_runs(db):
    """清除 worker 测试提交到共享测试库的运行与写入数据（保证隔离）。"""
    db.execute(text("DELETE FROM run_events"))
    db.execute(text("DELETE FROM run_leases"))
    db.execute(text("DELETE FROM generation_runs"))
    db.commit()
    yield


def _counts(db) -> dict[str, int]:
    return {
        t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
        for t in _WRITE_TABLES
    }


def _assert_no_writes(db, before: dict[str, int]) -> None:
    after = _counts(db)
    changed = {t: (before[t], after[t]) for t in _WRITE_TABLES if before[t] != after[t]}
    assert not changed, f"unexpected write-table deltas: {changed}"


def _make_chapter_with_plan(db, *, contract: dict | None = None) -> str:
    """创建项目/卷/章并接受带章节契约的计划，返回 chapter_id。"""
    project = create_project(db, "P", "g", "r", "s", _CTX)
    volume = create_volume(db, project.id, "V", "g", "m", "r", _CTX)
    chapter = create_chapter(db, volume.id, "Ch1", "pov", {"intent": 1}, _CTX)
    if contract is not None:
        plan = create_chapter_plan_revision(db, chapter.id, None, contract, "r", _CTX)
        accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _CTX)
    db.flush()
    return chapter.id


def _create_chapter_run(db, run_id: str, chapter_id: str, *, plan_revision_id: str | None = None) -> None:
    """创建带章节身份、无场景的 queued 章节运行（触发 ChapterGraph）。"""
    chapter = db.get(Chapter, chapter_id)
    volume = db.get(Volume, chapter.volume_id)
    run = GenerationRun(
        id=run_id,
        project_id=volume.project_id,
        chapter_id=chapter_id,
        status="queued",
        run_version=1,
        request_type="new_chapter",
        decision_target="plan",
        plan_revision_id=plan_revision_id,
        normalized_input={"run_scope": "chapter", "request_type": "new_chapter", "decision_target": "plan"},
    )
    db.add(run)
    db.flush()
    PostgresRunEventStore(db).emit(run_id, "run_queued", {"run_scope": "chapter", "request_type": "new_chapter"}, fencing_token=0)
    db.commit()


def _events(db, run_id: str) -> list[str]:
    rows = db.execute(
        RunEvent.__table__.select()
        .where(RunEvent.__table__.c.generation_run_id == run_id)
        .order_by(RunEvent.__table__.c.sequence)
    ).all()
    return [r.event_type for r in rows]


def _real_provider(transport: httpx.MockTransport, *, wiring: ObservabilityWiring | None = None) -> DeepSeekModelProvider:
    client = httpx.Client(transport=transport, timeout=httpx.Timeout(5))
    return DeepSeekModelProvider(
        base_url="https://api.deepseek.com",
        api_key="sk-test-key",
        model_name="deepseek-v4-flash",
        wiring=wiring,
        http_client=client,
        max_retries=0,
    )


def _system_prompt_of(req: httpx.Request) -> str:
    return json.loads(req.content)["messages"][0]["content"]


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


def _worker(factory, *, wiring=None, provider=None):
    return RunWorker(factory, actor_id="worker-1", observability=wiring, provider=provider)


def test_worker_chapter_chain_success_persists_pending_plan_and_pauses(db) -> None:
    """new_chapter 规划只持久化 pending 候选并暂停，不进入章节审校。"""
    chapter_id = _make_chapter_with_plan(db, contract={"pov": "p", "scene_keys": ["s1", "s2"]})
    run_id = "g-ch-worker-1"
    _create_chapter_run(db, run_id, chapter_id)
    seen_nodes: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sp = _system_prompt_of(req)
        seen_nodes.append("planner" if _SYSTEM_PLANNER in sp else "review")
        return _ok_response(sp)

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    worker = _worker(factory, wiring=wiring, provider=_real_provider(httpx.MockTransport(handler), wiring=wiring))
    before = _counts(db)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    # 聚合节点未装配 aggregator -> 暂停（带澄清问题 -> pending_clarification）。
    assert row.status in ("waiting_feedback", "pending_clarification")
    events = _events(db, run_id)
    assert events[0] == "run_queued"
    assert len(events) == 2
    # 首次规划阶段只调用 Planner；章节审校必须等待计划接受和场景队列完成。
    assert seen_nodes == ["planner"]
    candidate = db.query(ChapterPlanRevision).filter_by(source_run_id=run_id).one()
    assert candidate.status == "pending"
    # 未生成章节版本/Canon/handoff。
    _assert_no_writes(db, before)
    # 观测自动埋点：Planner 节点挂载 LLM 观测；章节审校尚未启动。
    assert wiring.local is not None
    node_names = [str(r.get("node_name")) for r in wiring.local.records]
    assert any(n.startswith("chapter_planner:llm:") for n in node_names)
    assert not any(n.startswith("chapter_review:llm:") for n in node_names)


def test_worker_chapter_chain_missing_contract_never_calls_model(db) -> None:
    """缺失契约：章节无已接受计划 -> chapter_contract 为空 -> planner 澄清，不触达模型。"""
    chapter_id = _make_chapter_with_plan(db, contract=None)
    run_id = "g-ch-worker-2"
    _create_chapter_run(db, run_id, chapter_id)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok_response(_system_prompt_of(req))

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = _worker(factory, provider=_real_provider(httpx.MockTransport(handler)))
    before = _counts(db)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    # 缺契约 -> planner needs_clarification -> 运行 pending_clarification。
    assert row.status == "pending_clarification"
    assert row.pending_node == "chapter_planner"
    assert row.clarification_questions
    # 模型从未被调用（缺契约不触达模型）。
    assert seen == []
    _assert_no_writes(db, before)


def test_worker_chapter_chain_401_fails_without_writes(db) -> None:
    """失败不写库：Provider 401 -> 运行 failed + LLM_AUTH_ERROR，无任何业务写入。"""
    chapter_id = _make_chapter_with_plan(db, contract={"pov": "p", "scene_keys": ["s1"]})
    run_id = "g-ch-worker-3"
    _create_chapter_run(db, run_id, chapter_id)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "auth"}})

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = _worker(factory, provider=_real_provider(httpx.MockTransport(handler)))
    before = _counts(db)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    assert row.status == "failed"
    assert row.last_error_code == "LLM_AUTH_ERROR"
    assert _events(db, run_id) == ["run_queued", "run_failed"]
    _assert_no_writes(db, before)


@pytest.mark.skipif(
    not os.environ.get("REAL_MODEL_SMOKE")
    or not (os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY") and os.environ.get("MODEL_NAME")),
    reason="REAL_MODEL_SMOKE=1 且 LLM_BASE_URL/LLM_API_KEY/MODEL_NAME 齐备时才执行（默认不访问网络）",
)
def test_worker_chapter_chain_with_real_deepseek(db) -> None:
    """真实模型 smoke：Worker 用真实 DeepSeek 走章节链路（门控，默认跳过）。"""
    chapter_id = _make_chapter_with_plan(db, contract={"pov": "p", "scene_keys": ["s1", "s2"]})
    run_id = "g-ch-worker-live"
    _create_chapter_run(db, run_id, chapter_id)
    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = DeepSeekModelProvider(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model_name=os.environ["MODEL_NAME"],
        wiring=wiring,
    )
    try:
        worker = _worker(factory, wiring=wiring, provider=provider)
        before = _counts(db)
        assert worker.tick() == 1
        db.expire_all()
        row = db.get(GenerationRun, run_id)
        assert row.status in ("waiting_feedback", "pending_clarification")
        assert _events(db, run_id)[0] == "run_queued"
        _assert_no_writes(db, before)
        assert wiring.local is not None
        node_names = [str(r.get("node_name")) for r in wiring.local.records]
        # 真实模型非确定性：planner 可能返回 needs_clarification 而暂停，
        # 但要求 schema 校验通过（run 未 failed）且模型调用被观测到。
        assert "run_failed" not in _events(db, run_id)
        assert any(":llm:" in n for n in node_names)
    finally:
        provider.close()
