"""Task 5B 运行 API 生命周期测试。

覆盖运行创建幂等、幂等键复用、运行版本 CAS、作者决策 API command fence、
暂停/澄清恢复、SSE `Last-Event-ID` 顺序重放。HTTP 请求只写入运行记录/事件/
outbox，不在请求线程执行 LangGraph（本测试不启动任何 Worker/图）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import cast

import httpx
import pytest
import uvicorn

from app.api.runs import _parse_last_event_id, _sse_frame
from app.db.models import Chapter, GenerationRun
from app.domain.chapter_orchestration import create_handoff_for_chapter_revision
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    commit_chapter_version,
    create_chapter_plan_revision,
)
from app.domain.interfaces import CommandContext
from app.services.generation_runs import get_run_input_envelope, replay_run_events

from .conftest import _create_chapter, _create_project, _create_scene, _create_volume


@pytest.fixture(scope="module")
def sse_base_url() -> Iterator[str]:
    """真实 uvicorn 服务器：TestClient 对无限 SSE 流会整体缓冲，故用真服务器+httpx。"""
    from app import main as app_main

    server = uvicorn.Server(uvicorn.Config(app_main.app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started and server.servers:
            break
        time.sleep(0.05)
    assert server.servers, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _create_run(client, chapter_id: str, key: str, **overrides) -> dict:
    body = {
        "run_scope": "chapter",
        "request_type": "new_chapter",
        "decision_target": "plan",
        "chapter_intent": {"text": "intent"},
    }
    body.update(overrides)
    resp = client.post(
        f"/api/chapters/{chapter_id}/runs",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_first_plan_accept_api_allows_missing_current_pointer(client, db):
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    run = _create_run(
        client,
        chapter["id"],
        "first-plan-api",
        chapter_intent={"text": "A storm reveals a hidden clue."},
    )
    plan = create_chapter_plan_revision(
        db,
        chapter["id"],
        None,
        {"scenes": []},
        "planner candidate",
        {"actor_id": "planner", "idempotency_key": "first-plan-seed"},
    )
    db.get(GenerationRun, run["run_id"]).status = "waiting_feedback"
    db.commit()

    response = client.post(
        f"/api/runs/{run['run_id']}/decisions",
        json={
            "idempotency_key": "first-plan-accept",
            "expected_run_version": 1,
            "target": "plan",
            "decision": "accept",
            "plan_revision_id": plan.id,
            "expected_plan_version": 1,
        },
        headers={"Idempotency-Key": "first-plan-accept"},
    )
    assert response.status_code == 200, response.text
    workflow = client.get(f"/api/chapters/{chapter['id']}/workflow")
    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["plan"]["accepted_revision_id"] == plan.id


def _decision_body(key: str, expected_run_version: int = 1, decision: str = "cancel") -> dict:
    return {
        "idempotency_key": key,
        "expected_run_version": expected_run_version,
        "target": "scene",
        "decision": decision,
    }


def test_run_create_and_repeat_is_idempotent(client):
    """同一幂等键重复创建运行返回完全相同的 RunSnapshot（单次真正写入）。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    key = "run-dup-key"
    first = _create_run(client, chapter["id"], key)
    second = _create_run(client, chapter["id"], key)
    assert second == first
    assert first["run_id"] == first["thread_id"]
    assert first["status"] == "queued"
    assert first["run_version"] == 1
    assert first["run_scope"] == "chapter"
    assert first["target_id"] == chapter["id"]
    assert first["last_event_sequence"] == 1  # run_queued 事件已持久化


def test_run_create_same_key_different_request_reuse(client):
    """同键不同请求体（不同指纹）返回 IDEMPOTENCY_KEY_REUSE，不创建第二条运行。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    key = "run-reuse-key"
    _create_run(client, chapter["id"], key)
    resp = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json={"run_scope": "chapter", "request_type": "continue", "decision_target": "scene"},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "IDEMPOTENCY_KEY_REUSE"


def test_chapter_feedback_rejects_stale_base_revision(client, db):
    """章节反馈必须以当前 accepted 章节版本为基线，拒绝过期工作台提交。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    ctx = {"actor_id": "author", "idempotency_key": "chapter-feedback-base-1"}
    plan = create_chapter_plan_revision(
        db, chapter["id"], None, {"scenes": []}, "plan", ctx
    )
    accept_chapter_plan_revision(db, chapter["id"], plan.id, None, 1, ctx)
    accepted = aggregate_chapter_revision(db, chapter["id"], [], "first", ctx)
    commit_chapter_version(db, accepted.id, ctx)
    db.commit()

    run = _create_run(
        client,
        chapter["id"],
        "chapter-feedback-run",
        request_type="review",
        decision_target="chapter",
        plan_revision_id=plan.id,
        base_chapter_revision_id=accepted.id,
    )
    db.get(GenerationRun, run["run_id"]).status = "waiting_feedback"
    db.commit()

    newer = aggregate_chapter_revision(db, chapter["id"], [], "second", ctx)
    commit_chapter_version(db, newer.id, {**ctx, "idempotency_key": "chapter-feedback-base-2"})
    db.commit()

    response = client.post(
        f"/api/runs/{run['run_id']}/decisions",
        json={
            "idempotency_key": "chapter-feedback-stale",
            "expected_run_version": 1,
            "target": "chapter",
            "decision": "feedback",
            "chapter_revision_id": accepted.id,
            "base_chapter_revision_id": accepted.id,
            "text": "please revise",
        },
        headers={"Idempotency-Key": "chapter-feedback-stale"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CHAPTER_OUT_OF_SYNC"


def test_decision_run_version_cas_conflict(client):
    """expected_run_version 不匹配时拒绝决策（RUN_STATE_CONFLICT），不写任何决策。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    run = _create_run(client, chapter["id"], "cas-run-key")
    body = _decision_body(key="cas-key", expected_run_version=99)
    resp = client.post(
        f"/api/runs/{run['run_id']}/decisions",
        json=body,
        headers={"Idempotency-Key": "cas-key"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "RUN_STATE_CONFLICT"


def test_author_decision_uses_api_command_fence(client, db):
    """作者决策后运行写入所有者切换为 api_command，令牌随命令推进且幂等重放复用。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    run = _create_run(client, chapter["id"], "fence-run-key")
    body = _decision_body(key="fence-key", expected_run_version=1, decision="cancel")
    resp = client.post(
        f"/api/runs/{run['run_id']}/decisions",
        json=body,
        headers={"Idempotency-Key": "fence-key"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["run"]["status"] == "cancelled"
    assert data["run"]["run_version"] == 2
    # API command fence：写入所有者是 manual_command_id，不是 Worker。
    row = db.get(GenerationRun, run["run_id"])
    assert row.write_owner_kind == "api_command"
    assert row.write_owner_id == data["command_id"]
    assert row.write_fencing_token == 1
    # 同键重放返回完全相同的第一次响应（复用原 manual_command_id）。
    again = client.post(
        f"/api/runs/{run['run_id']}/decisions",
        json=body,
        headers={"Idempotency-Key": "fence-key"},
    )
    assert again.status_code == 200
    assert again.json() == data


def test_pause_resume_and_clarification_state_contract(client, db):
    """paused 只能 resume；pending_clarification 只能以 feedback 回答，不能 accept/resume。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    run = _create_run(client, chapter["id"], "pause-run-key")
    run_id = run["run_id"]

    # 模拟 Worker 将运行置为 paused。
    row = db.get(GenerationRun, run_id)
    row.status = "paused"
    db.commit()

    # paused 不接受决策（只能 resume）。
    body = _decision_body(key="pause-dec-key", expected_run_version=1, decision="cancel")
    resp = client.post(
        f"/api/runs/{run_id}/decisions",
        json=body,
        headers={"Idempotency-Key": "pause-dec-key"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "RUN_STATE_CONFLICT"

    # resume 成功：状态回到 running，版本 CAS 递增。
    resume_body = {
        "idempotency_key": "resume-key",
        "expected_run_version": 1,
        "expected_pause_reason": "manual",
    }
    resp = client.post(
        f"/api/runs/{run_id}/resume",
        json=resume_body,
        headers={"Idempotency-Key": "resume-key"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["status"] == "running"
    assert resp.json()["run"]["run_version"] == 2

    # pending_clarification：resume 拒绝，accept 拒绝，feedback 可回答。
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    row.status = "pending_clarification"
    db.commit()
    resume_body2 = {
        "idempotency_key": "resume-key-2",
        "expected_run_version": 2,
        "expected_pause_reason": "clarify",
    }
    resp = client.post(
        f"/api/runs/{run_id}/resume",
        json=resume_body2,
        headers={"Idempotency-Key": "resume-key-2"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "RUN_STATE_CONFLICT"
    accept_body = _decision_body(key="clar-accept-key", expected_run_version=2, decision="accept")
    resp = client.post(
        f"/api/runs/{run_id}/decisions",
        json=accept_body,
        headers={"Idempotency-Key": "clar-accept-key"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "RUN_STATE_CONFLICT"
    feedback_body = _decision_body(key="clar-fb-key", expected_run_version=2, decision="feedback")
    resp = client.post(
        f"/api/runs/{run_id}/decisions",
        json=feedback_body,
        headers={"Idempotency-Key": "clar-fb-key"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["status"] == "waiting_feedback"
    assert resp.json()["run"]["run_version"] == 3


def test_sse_replay_after_last_event_id(client, db, sse_base_url):
    """SSE 按 Last-Event-ID 重放：从头返回全部事件，断线后从下一序号补发。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    run = _create_run(client, chapter["id"], "sse-run-key")
    run_id = run["run_id"]
    body = _decision_body(key="sse-dec-key", expected_run_version=1, decision="cancel")
    resp = client.post(
        f"/api/runs/{run_id}/decisions",
        json=body,
        headers={"Idempotency-Key": "sse-dec-key"},
    )
    assert resp.status_code == 200, resp.text

    def _read_ids(headers: dict | None, expected: int) -> list[str]:
        # 读到预期数量的事件 id 即停止，避免等待 15s heartbeat 造成挂起。
        with httpx.Client(timeout=5) as hx:
            with hx.stream("GET", f"{sse_base_url}/api/runs/{run_id}/events", headers=headers or {}) as r:
                assert r.status_code == 200
                ids: list[str] = []
                for line in r.iter_lines():
                    if line.startswith("id: "):
                        ids.append(line[4:])
                    if len(ids) == expected:
                        break
                return ids

    # 无 Last-Event-ID：从头按升序重放两个事件。
    assert _read_ids({}, expected=2) == [f"{run_id}:1", f"{run_id}:2"]
    # Last-Event-ID=run_id:1：从下一序号 2 开始补发。
    assert _read_ids({"Last-Event-ID": f"{run_id}:1"}, expected=1) == [f"{run_id}:2"]

    # Last-Event-ID 解析：`run-id:42` 与裸序号 `42` 都支持。
    assert _parse_last_event_id(f"{run_id}:1") == 1
    assert _parse_last_event_id("42") == 42
    assert _parse_last_event_id(None) == 0

    # 核心重放逻辑独立可测：after_sequence=1 只返回序号 2 的事件（升序）。
    db.expire_all()
    events = replay_run_events(db, run_id, after_sequence=1)
    assert [e["sequence"] for e in events] == [2]
    assert events[0]["run_id"] == run_id
    # SSE 帧格式携带稳定事件 id。
    frame = _sse_frame(events[0])
    assert f"id: {run_id}:2" in frame
    assert "event: run_cancelled" in frame
    # HTTP 入口：非法 Last-Event-ID 在流式前同步返回 COMMAND_CONTEXT_MISMATCH。
    resp = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "bad-id"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "COMMAND_CONTEXT_MISMATCH"


def test_sse_pushes_new_events_after_connect(client, db, sse_base_url):
    """连接建立后新产生的 RunEvent 会被实时推送，无需重连或 Last-Event-ID。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    run = _create_run(client, chapter["id"], "sse-live-key")
    run_id = run["run_id"]

    ids: list[str] = []
    stop = threading.Event()

    def reader():
        with httpx.Client(timeout=10) as hx:
            with hx.stream("GET", f"{sse_base_url}/api/runs/{run_id}/events") as r:
                assert r.status_code == 200
                for line in r.iter_lines():
                    if line.startswith("id: "):
                        ids.append(line[4:])
                    if stop.is_set():
                        break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    # 连接建立后先收到初始 run_queued 事件。
    for _ in range(100):
        if f"{run_id}:1" in ids:
            break
        time.sleep(0.05)
    assert f"{run_id}:1" in ids
    # 连接保持期间新产生事件（作者决策）。
    body = _decision_body(key="live-key", expected_run_version=1, decision="cancel")
    resp = client.post(
        f"/api/runs/{run_id}/decisions",
        json=body,
        headers={"Idempotency-Key": "live-key"},
    )
    assert resp.status_code == 200, resp.text
    # 实时推送新事件（不断线即可收到）。
    for _ in range(100):
        if f"{run_id}:2" in ids:
            break
        time.sleep(0.05)
    assert f"{run_id}:2" in ids
    stop.set()
    t.join(timeout=5)


def test_run_snapshot_returns_real_state_fields(client, db):
    """GET /runs/{id} 返回真实 pending_node/pause_reason/clarification_questions/错误码。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    run = _create_run(client, chapter["id"], "snap-key")
    run_id = run["run_id"]
    # Worker 写入可恢复运行状态。
    row = db.get(GenerationRun, run_id)
    row.status = "pending_clarification"
    row.pending_node = "scene_draft_review"
    row.pause_reason = "needs_author_input"
    row.clarification_questions = ["what is the tone?"]
    row.last_error_code = "RUN_LEASE_LOST"
    db.commit()
    resp = client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending_node"] == "scene_draft_review"
    assert data["pause_reason"] == "needs_author_input"
    assert data["clarification_questions"] == ["what is the tone?"]
    assert data["last_error_code"] == "RUN_LEASE_LOST"


def test_run_create_validation_rules(client, db):
    """运行创建前统一校验：plan 指针规则、拒绝 Canon 字段、非当前 plan 拒绝。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    scene = _create_scene(client, chapter["id"])

    # 场景运行缺少 plan_revision_id -> PLAN_REVISION_CONFLICT。
    resp = client.post(
        f"/api/scenes/{scene['id']}/runs",
        json={"run_scope": "scene", "request_type": "continue", "decision_target": "scene"},
        headers={"Idempotency-Key": "v-scene"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "PLAN_REVISION_CONFLICT"

    # 章节 continue 缺少 plan_revision_id -> PLAN_REVISION_CONFLICT。
    resp = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json={"run_scope": "chapter", "request_type": "continue", "decision_target": "scene"},
        headers={"Idempotency-Key": "v-continue"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "PLAN_REVISION_CONFLICT"

    # 普通运行携带 Canon 字段 -> CANON_USE_DEDICATED_ENDPOINT。
    resp = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json={
            "run_scope": "chapter",
            "request_type": "new_chapter",
            "decision_target": "plan",
            "canon_scope": "chapter",
        },
        headers={"Idempotency-Key": "v-canon"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "CANON_USE_DEDICATED_ENDPOINT"

    # plan_revision_id 不是当前 accepted plan -> PLAN_REVISION_CONFLICT。
    plan = create_chapter_plan_revision(
        db,
        chapter["id"],
        None,
        {"scenes": []},
        "reason",
        {"actor_id": "a", "idempotency_key": "plan-key"},
    )
    db.commit()
    resp = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json={
            "run_scope": "chapter",
            "request_type": "continue",
            "decision_target": "scene",
            "plan_revision_id": plan.id,
        },
        headers={"Idempotency-Key": "v-plan"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "PLAN_REVISION_CONFLICT"


def _seed_command_ctx() -> CommandContext:
    # 测试辅助构造最小命令上下文（author 身份），沿用 e2e_fixtures 的 cast 约定。
    return cast(CommandContext, {
        "actor_id": "test-actor",
        "idempotency_key": "seed-key",
        "author_decision": "accept",
    })


def _seed_first_chapter_accepted(db, chapter_id: str) -> str:
    """接受章节首个版本并置为 in_sync，返回该版本 id（供跨章节 handoff 使用）。"""
    rev = aggregate_chapter_revision(db, chapter_id, [], "seed", _seed_command_ctx())
    commit_chapter_version(db, rev.id, _seed_command_ctx())
    row = db.get(Chapter, chapter_id)
    row.chapter_sync_status = "in_sync"
    db.commit()
    return rev.id


def _seed_cross_handoff(db, rev1_id: str, target_chapter_id: str) -> str:
    """为下游章节创建指向源章节 accepted 版本的入口 handoff。"""
    handoff = create_handoff_for_chapter_revision(
        db, rev1_id, target_chapter_id, "chain-hash", _seed_command_ctx()
    )
    db.commit()
    return handoff.id


def test_cross_chapter_entry_validation(client, db):
    """跨章节入口严格校验：首章无字段、非首章五字段、前置章、来源版本、旧 handoff。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter1 = _create_chapter(client, volume["id"])
    chapter2 = _create_chapter(client, volume["id"])
    rev1_id = _seed_first_chapter_accepted(db, chapter1["id"])
    handoff_id = _seed_cross_handoff(db, rev1_id, chapter2["id"])

    def _run_for(chapter_id: str, key: str, **overrides):
        body = {
            "run_scope": "chapter",
            "request_type": "new_chapter",
            "decision_target": "plan",
            "chapter_intent": {"text": "intent"},
        }
        body.update(overrides)
        return client.post(
            f"/api/chapters/{chapter_id}/runs",
            json=body,
            headers={"Idempotency-Key": key},
        )

    # 首章携带 preceding 字段 -> 拒绝。
    resp = _run_for(chapter1["id"], "cc-first", preceding_chapter_id="x")
    assert resp.status_code == 409
    assert resp.json()["code"] == "CHAPTER_HANDOFF_CONFLICT"

    # 非首章缺字段 -> 拒绝。
    resp = _run_for(chapter2["id"], "cc-missing")
    assert resp.status_code == 409
    assert resp.json()["code"] == "CHAPTER_HANDOFF_CONFLICT"

    # 非首章错前置章节 -> 拒绝。
    resp = _run_for(
        chapter2["id"],
        "cc-wrong-prev",
        preceding_chapter_id="wrong-chapter",
        preceding_accepted_chapter_revision_id=rev1_id,
        entry_handoff_id=handoff_id,
        entry_source_chapter_revision_id=rev1_id,
        entry_handoff_chain_hash="chain-hash",
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CHAPTER_HANDOFF_CONFLICT"

    # 非首章来源版本不是当前 accepted（旧 handoff）-> 拒绝。
    resp = _run_for(
        chapter2["id"],
        "cc-wrong-src",
        preceding_chapter_id=chapter1["id"],
        preceding_accepted_chapter_revision_id=rev1_id,
        entry_handoff_id=handoff_id,
        entry_source_chapter_revision_id="stale-rev",
        entry_handoff_chain_hash="chain-hash",
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CHAPTER_HANDOFF_CONFLICT"

    # 前置字段完全正确 -> 成功。
    resp = _run_for(
        chapter2["id"],
        "cc-ok",
        preceding_chapter_id=chapter1["id"],
        preceding_accepted_chapter_revision_id=rev1_id,
        entry_handoff_id=handoff_id,
        entry_source_chapter_revision_id=rev1_id,
        entry_handoff_chain_hash="chain-hash",
    )
    assert resp.status_code == 200, resp.text

    # 源章节 accepted 指针推进后，旧 handoff 失效 -> 拒绝。
    rev2_id = _seed_first_chapter_accepted(db, chapter1["id"])
    assert rev2_id != rev1_id
    resp = _run_for(
        chapter2["id"],
        "cc-stale",
        preceding_chapter_id=chapter1["id"],
        preceding_accepted_chapter_revision_id=rev1_id,
        entry_handoff_id=handoff_id,
        entry_source_chapter_revision_id=rev1_id,
        entry_handoff_chain_hash="chain-hash",
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CHAPTER_HANDOFF_CONFLICT"


def test_run_input_envelope_persisted_and_immutable(client, db):
    """规范化运行输入持久化且不可变：Worker 仅凭 run_id 重建与首次请求一致的输入。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    body = {
        "run_scope": "chapter",
        "request_type": "new_chapter",
        "decision_target": "plan",
        "chapter_intent": {"text": "intent"},
        "author_feedback": {"note": "go"},
        "base_chapter_revision_id": None,
        "scene_base_revision_ids": {},
    }
    resp = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json=body,
        headers={"Idempotency-Key": "input-key"},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    envelope = get_run_input_envelope(db, run_id)
    assert envelope["chapter_intent"] == {"text": "intent"}
    assert envelope["author_feedback"] == {"note": "go"}
    assert envelope["run_scope"] == "chapter"
    assert envelope["request_type"] == "new_chapter"
    assert envelope["decision_target"] == "plan"
    assert envelope["scene_base_revision_ids"] == {}
    assert envelope["plan_revision_id"] is None
    # 不可变：修改返回的副本不影响持久化输入。
    envelope["chapter_intent"] = {"text": "mutated"}
    db.expire_all()
    row = db.get(GenerationRun, run_id)
    assert row.normalized_input["chapter_intent"] == {"text": "intent"}


def test_decision_state_restrictions(client, db):
    """决策状态限制：queued/running 不能直接 accept 或 feedback，等待状态才可 accept。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    # 先建立 accepted plan 供 accept 动作使用。
    plan = create_chapter_plan_revision(
        db, chapter["id"], None, {"scenes": []}, "reason", {"actor_id": "a", "idempotency_key": "plan-key"}
    )
    accept_chapter_plan_revision(
        db, chapter["id"], plan.id, None, 1, {"actor_id": "a", "idempotency_key": "accept-key"}
    )
    db.commit()
    resp = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json={
            "run_scope": "chapter",
            "request_type": "new_chapter",
            "decision_target": "plan",
            "plan_revision_id": plan.id,
        },
        headers={"Idempotency-Key": "state-run-key"},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    accept_body = {
        "idempotency_key": "st-accept",
        "expected_run_version": 1,
        "target": "plan",
        "decision": "accept",
        "plan_revision_id": plan.id,
        "expected_current_plan_revision_id": plan.id,
        "expected_plan_version": 1,
    }

    def _accept(key: str):
        payload = dict(accept_body)
        payload["idempotency_key"] = key
        return client.post(
            f"/api/runs/{run_id}/decisions", json=payload, headers={"Idempotency-Key": key}
        )

    # queued 不能直接 accept（绕过 Agent 输出/等待节点）。
    r = _accept("st-accept")
    assert r.status_code == 409
    assert r.json()["code"] == "RUN_STATE_CONFLICT"

    # queued 不能直接 feedback。
    fb_body = _decision_body(key="st-fb", expected_run_version=1, decision="feedback")
    r = client.post(
        f"/api/runs/{run_id}/decisions", json=fb_body, headers={"Idempotency-Key": "st-fb"}
    )
    assert r.status_code == 409
    assert r.json()["code"] == "RUN_STATE_CONFLICT"

    # running 不能直接 accept。
    row = db.get(GenerationRun, run_id)
    row.status = "running"
    db.commit()
    r = _accept("st-accept2")
    assert r.status_code == 409
    assert r.json()["code"] == "RUN_STATE_CONFLICT"

    # waiting_feedback 才能 accept。
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    r = _accept("st-accept3")
    assert r.status_code == 200, r.text
    assert r.json()["run"]["status"] == "accepted"


def test_plan_feedback_child_supersedes_parent_only(client, db):
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    plan = create_chapter_plan_revision(
        db,
        chapter["id"],
        None,
        {"scenes": []},
        "seed",
        {"actor_id": "planner", "idempotency_key": "feedback-plan"},
    )
    accept_chapter_plan_revision(
        db,
        chapter["id"],
        plan.id,
        None,
        1,
        {"actor_id": "planner", "idempotency_key": "feedback-accept"},
    )
    db.commit()
    run = _create_run(client, chapter["id"], "feedback-run", plan_revision_id=plan.id)
    parent = db.get(GenerationRun, run["run_id"])
    parent.status = "waiting_feedback"
    db.commit()

    response = client.post(
        f"/api/runs/{parent.id}/decisions",
        json={
            "idempotency_key": "feedback-decision",
            "expected_run_version": 1,
            "target": "plan",
            "decision": "feedback",
            "text": "revise the plan",
        },
        headers={"Idempotency-Key": "feedback-decision"},
    )
    assert response.status_code == 200, response.text
    child_id = response.json()["run"]["run_id"]
    child = db.get(GenerationRun, child_id)
    db.refresh(parent)
    assert child.parent_generation_run_id == parent.id
    assert child.supersedes_run_id == parent.id
    assert parent.status == "superseded"
    assert parent.supersedes_run_id is None
