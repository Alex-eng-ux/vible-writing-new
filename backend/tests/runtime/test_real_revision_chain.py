"""Task 9 真实 RevisionAgent 链路测试（默认 HTTP mock，不访问网络）。

覆盖：
- 成功链路：创建带已接受基线 + base_scene_revision_id + 作者反馈的场景运行 ->
  经真实 Provider（MockTransport）Writing/Continuity/Review/Revision 依次返回
  结构化结果 -> 运行进入等待反馈终态（revision 返回 ready 后图结束），不创建
  任何版本/候选/Canon 数据；
- 失败链路：Revision Provider 401 / 超时 / 非法结构化输出 -> 运行 failed +
  对应 LLM_* 错误码，不创建任何版本/候选/Canon 数据；
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
from app.db.models import GenerationRun, RunEvent, SceneRevision
from app.observability.sink import LocalSink
from app.observability.wiring import ObservabilityWiring
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_worker import RunWorker
from tests.acceptance.test_hashes import _hierarchy

_SYSTEM_WRITING = "You are a novel-writing agent"
_SYSTEM_CONTINUITY = "You are a continuity-check agent"
_SYSTEM_REVIEW = "You are a scene review agent"
_SYSTEM_REVISION = "You are a revision agent"

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
    """断言权威/候选写入表行数与 Worker 执行前一致（真实模型不得创建版本/候选/Canon）。"""
    after = _counts(db)
    changed = {t: (before[t], after[t]) for t in _WRITE_TABLES if before[t] != after[t]}
    assert not changed, f"unexpected write-table deltas: {changed}"


def _create_revision_run(db, run_id: str) -> None:
    """创建带已接受基线 + base_scene_revision_id + 作者反馈的 queued 场景运行。

    设置 scene_brief（保证 WritingAgent 调模型）、已接受版本（保证
    Continuity/ReviewAgent 拿到 accepted_text）、base_scene_revision_id 与作者
    反馈（保证 RevisionAgent 调模型）。
    """
    project, scene = _hierarchy(db)
    scene.scene_brief = {
        "goal": "重逢",
        "summary": "雨夜咖啡馆，林默与旧友重逢",
        "target": "角色",
    }
    rev = SceneRevision(
        scene_id=scene.id,
        content="雨夜咖啡馆，林默与旧友重逢。",
        content_hash="base-hash",
        reason="baseline",
        source_ref="manual",
        status="accepted",
    )
    db.add(rev)
    db.flush()
    scene.accepted_scene_revision_id = rev.id
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
        normalized_input={
            "run_scope": "scene",
            "request_type": "continue",
            "decision_target": "scene",
            "base_scene_revision_id": rev.id,
            "author_feedback": {"text": "把重逢写得更含蓄一些", "target": "scene"},
        },
    )
    db.add(run)
    db.flush()
    PostgresRunEventStore(db).emit(run_id, "run_queued", {"run_scope": "scene", "request_type": "continue"}, fencing_token=0)
    db.commit()


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


def _writing_content() -> str:
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


def _continuity_content() -> str:
    return json.dumps(
        {"status": "pass", "scene_snapshot_delta": {}, "issues": [], "clarification_questions": []}
    )


def _review_content() -> str:
    return json.dumps(
        {
            "status": "ready",
            "review_issues": [],
            "overall_rating": "pass",
            "submitted": False,
            "clarification_questions": [],
        }
    )


def _revision_content() -> str:
    return json.dumps(
        {
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
    )


def _ok_response(system_prompt: str) -> httpx.Response:
    """按系统提示词返回对应 Agent 的合法结构化输出。"""
    if _SYSTEM_CONTINUITY in system_prompt:
        content = _continuity_content()
    elif _SYSTEM_REVIEW in system_prompt:
        content = _review_content()
    elif _SYSTEM_REVISION in system_prompt:
        content = _revision_content()
    else:
        content = _writing_content()
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def _system_prompt_of(req: httpx.Request) -> str:
    """从请求体中提取系统提示词（用于按节点分发不同的 mock 响应）。"""
    body = json.loads(req.content)
    return body["messages"][0]["content"]


def test_real_revision_chain_success_runs_all_agents(db) -> None:
    """成功链路：Writing->Continuity->Review->Revision 均经 provider 返回，无版本写入。"""
    run_id = "g-rev-1"
    _create_revision_run(db, run_id)
    before = _counts(db)
    seen_nodes: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sp = _system_prompt_of(req)
        if _SYSTEM_CONTINUITY in sp:
            seen_nodes.append("continuity")
        elif _SYSTEM_REVIEW in sp:
            seen_nodes.append("review")
        elif _SYSTEM_REVISION in sp:
            seen_nodes.append("revision")
        else:
            seen_nodes.append("writing")
        return _ok_response(sp)

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    provider = _real_provider(httpx.MockTransport(handler), wiring=wiring)
    worker = RunWorker(factory, actor_id="worker-1", observability=wiring, provider=provider)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    # Revision 返回 ready 后图结束；_persist_outcome 把 running 归为等待作者处理。
    assert row.status in ("waiting_feedback", "pending_clarification")
    assert _events(db, run_id)[0] == "run_queued"
    # 四个场景 Agent 均被真实 Provider（mock）调用一次。
    assert seen_nodes == ["writing", "continuity", "review", "revision"]
    _assert_no_writes(db, before)
    # 观测自动埋点：revision 挂载 llm 节点。
    assert wiring.local is not None
    node_names = [str(r.get("node_name")) for r in wiring.local.records]
    assert any(n.startswith("revision:llm:") for n in node_names)


def test_real_revision_chain_401_fails_without_writes(db) -> None:
    """失败链路：Revision Provider 401 -> 运行 failed + LLM_AUTH_ERROR，无写入。"""
    run_id = "g-rev-2"
    _create_revision_run(db, run_id)
    before = _counts(db)

    def handler(req: httpx.Request) -> httpx.Response:
        sp = _system_prompt_of(req)
        if _SYSTEM_REVISION in sp:
            return httpx.Response(401, json={"error": {"message": "auth"}})
        return _ok_response(sp)

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    provider = _real_provider(httpx.MockTransport(handler))
    worker = RunWorker(factory, actor_id="worker-1", provider=provider)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    assert row.status == "failed"
    assert row.last_error_code == "LLM_AUTH_ERROR"
    assert _events(db, run_id) == ["run_queued", "run_failed"]
    _assert_no_writes(db, before)


def test_real_revision_chain_timeout_fails_without_writes(db) -> None:
    """失败链路：Revision Provider 超时 -> 运行 failed + LLM_UNAVAILABLE，无写入。"""
    run_id = "g-rev-3"
    _create_revision_run(db, run_id)
    before = _counts(db)

    def handler(req: httpx.Request) -> httpx.Response:
        sp = _system_prompt_of(req)
        if _SYSTEM_REVISION in sp:
            raise httpx.ConnectTimeout("connect timed out", request=req)
        return _ok_response(sp)

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    provider = _real_provider(httpx.MockTransport(handler))
    worker = RunWorker(factory, actor_id="worker-1", provider=provider)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    assert row.status == "failed"
    assert row.last_error_code == "LLM_UNAVAILABLE"
    _assert_no_writes(db, before)


def test_real_revision_chain_invalid_output_fails(db) -> None:
    """失败链路：Revision 非法结构化输出 -> 运行 failed + LLM_RESPONSE_INVALID。"""
    run_id = "g-rev-4"
    _create_revision_run(db, run_id)
    before = _counts(db)

    def handler(req: httpx.Request) -> httpx.Response:
        sp = _system_prompt_of(req)
        if _SYSTEM_REVISION in sp:
            content = json.dumps({"status": "bogus"})
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})
        return _ok_response(sp)

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    provider = _real_provider(httpx.MockTransport(handler))
    worker = RunWorker(factory, actor_id="worker-1", provider=provider)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    assert row.status == "failed"
    assert row.last_error_code == "LLM_RESPONSE_INVALID"
    assert _events(db, run_id) == ["run_queued", "run_failed"]
    _assert_no_writes(db, before)


@pytest.mark.skipif(
    not os.environ.get("REAL_MODEL_SMOKE")
    or not (os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY") and os.environ.get("MODEL_NAME")),
    reason="REAL_MODEL_SMOKE=1 且 LLM_BASE_URL/LLM_API_KEY/MODEL_NAME 齐备时才执行（默认不访问网络）",
)
def test_real_revision_chain_with_real_deepseek(db) -> None:
    """真实模型 smoke：用真实 DeepSeek 走 Writing/Continuity/Review/Revision 完整链路（门控）。"""
    run_id = "g-rev-live"
    _create_revision_run(db, run_id)
    before = _counts(db)
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
        node_names = [str(r.get("node_name")) for r in wiring.local.records]
        # 真实模型非确定性：可能在任何节点返回 needs_clarification 而提前暂停，
        # 但要求 schema 校验通过（run 未 failed）且模型调用被观测到。
        assert "run_failed" not in _events(db, run_id)
        assert any(":llm:" in n for n in node_names)
    finally:
        provider.close()
