"""Task 5C Canon 专用运行 API 测试。

覆盖：章节/场景两个专用入口；chapter_revision.accepted 自动入队且重复消费
幂等；三类候选逐条确认/拒绝/暂缓；同键同请求重放、同键不同请求返回
IDEMPOTENCY_KEY_REUSE；过期来源/错误作用域/无效引用被拒绝；场景确认不更新
全局 Canon；作者决策 CAS、API fence 与旧 token 拒绝；API 不调用 WritingAgent。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import (
    CanonDecisionRecord,
    CanonFact,
    Chapter,
    FactCandidate,
    GenerationRun,
    RunOutboxRecord,
    Scene,
    SceneRevision,
)
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    create_chapter_plan_revision,
)
from app.services.canon_runs import handle_chapter_accepted_outbox

from .conftest import _create_chapter, _create_project, _create_scene, _create_volume


@pytest.fixture(autouse=True)
def _cleanup_canon_tables(db):
    """清理本文件通过 API 提交到共享库的 Canon 记录，避免污染领域测试的全局计数。

    API 请求经 get_db 提交事务，提交的数据会跨测试持久；domain 测试对
    CanonDecisionRecord/CanonFact 等做全局 count，因此本文件在每个测试后删除
    这些 Canon 相关表，保证测试隔离。
    """
    yield
    from app.db.models import (
        CanonDecisionRecord,
        CanonFact,
        FactCandidate,
        PlotThread,
        PlotThreadUpdate,
        TimelineEvent,
        TimelineEventCandidate,
    )

    for model in (
        CanonDecisionRecord,
        CanonFact,
        TimelineEvent,
        PlotThread,
        FactCandidate,
        TimelineEventCandidate,
        PlotThreadUpdate,
    ):
        db.query(model).delete()
    db.commit()


def _resource_ctx():
    return {"actor_id": "author-1", "idempotency_key": "key-1"}


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


def _setup_chapter(db, client, volume_id):
    """建章节 + 接受计划 + 置 in_sync + 接受章节版本，返回 (chapter_dict, accepted_rev_id)。"""
    chapter = _create_chapter(client, volume_id)
    plan = create_chapter_plan_revision(
        db, chapter["id"], None, {"c": 1}, "r", _author_ctx()
    )
    accept_chapter_plan_revision(db, chapter["id"], plan.id, None, 1, _author_ctx())
    row = db.get(Chapter, chapter["id"])
    row.chapter_sync_status = "in_sync"
    row.entry_handoff_status = "in_sync"
    db.flush()
    rev = aggregate_chapter_revision(db, chapter["id"], [], "r", _author_ctx())
    from app.domain.chapters import commit_chapter_version

    commit_chapter_version(db, rev.id, _author_ctx())
    db.commit()
    return chapter, rev.id


def _setup_scene(db, client, chapter_id):
    """建场景 + 接受场景版本，返回 (scene_dict, accepted_rev_id)。"""
    scene = _create_scene(client, chapter_id)
    srev = SceneRevision(
        scene_id=scene["id"],
        parent_revision_id=None,
        content="x",
        content_hash="h",
        reason="r",
        source_ref="s",
        status="accepted",
    )
    db.add(srev)
    db.flush()
    row = db.get(Scene, scene["id"])
    row.accepted_scene_revision_id = srev.id
    db.commit()
    return scene, srev.id


def _candidate_payload(project_id, chapter_id, rev_id, scope="chapter", ctype="fact",
                       local_key="l1", claim="claim", scene_id=None, content=None):
    default_content = {
        "claim": claim,
        "entity_id": None,
        "paragraph_ref": "p3",
        "effective_story_time": {"value": "第3章", "precision": "exact"},
        "narrative_knowledge": "objective",
        "state": "open",
        "planned_resolution": "第5章",
    }
    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "scene_id": scene_id,
        "scope": scope,
        "candidate_type": ctype,
        "fingerprint": None,
        "source_revision_id": rev_id,
        "content": content or default_content,
        "local_key": local_key,
    }


def _project_id(db, chapter_id):
    chapter = db.get(Chapter, chapter_id)
    from app.db.models import Volume

    return db.get(Volume, chapter.volume_id).project_id


def _make_canon_run(client, chapter_id, rev_id, key):
    resp = client.post(
        f"/api/chapters/{chapter_id}/canon-runs",
        json={"canon_scope": "chapter", "accepted_chapter_revision_id": rev_id},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_canon_run_scene(client, scene_id, rev_id, key):
    resp = client.post(
        f"/api/scenes/{scene_id}/canon-runs",
        json={"canon_scope": "scene", "accepted_scene_revision_id": rev_id},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _decision_body(run_id, key, decisions, canon_scope="chapter", expected_run_version=1):
    return {
        "idempotency_key": key,
        "expected_run_version": expected_run_version,
        "canon_scope": canon_scope,
        "candidate_decisions": decisions,
    }


def _cancel_body(run_id, key, cancel_scope, canon_scope="chapter", expected_run_version=1):
    return {
        "idempotency_key": key,
        "expected_run_version": expected_run_version,
        "canon_scope": canon_scope,
        "decision": "cancel",
        "cancel_scope": cancel_scope,
        "candidate_decisions": [],
    }


def test_chapter_and_scene_canon_run_endpoints(client, db):
    """章节与场景两个专用 canon-runs 入口都创建独立 canon 运行。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    scene, srev_id = _setup_scene(db, client, chapter["id"])

    chapter_run = _make_canon_run(client, chapter["id"], rev_id, "canon-ch")
    assert chapter_run["run_scope"] == "chapter"
    # Canon 运行统一使用 request_type=review + decision_target=canon，
    # 不新增/持久化 request_type=canon。
    assert chapter_run["request_type"] == "review"
    assert chapter_run["status"] == "queued"
    row = db.get(GenerationRun, chapter_run["run_id"])
    assert row.decision_target == "canon"
    assert row.canon_source_revision_id == rev_id
    assert row.request_type == "review"

    scene_run = _make_canon_run_scene(client, scene["id"], srev_id, "canon-sc")
    assert scene_run["run_scope"] == "scene"
    srow = db.get(GenerationRun, scene_run["run_id"])
    assert srow.decision_target == "canon"
    assert srow.canon_source_revision_id == srev_id


def test_chapter_accepted_event_auto_enqueue_and_idempotent_consume(client, db):
    """章节 accepted 事件自动入队，Canon 消费者按 (chapter_id, accepted 版本) 幂等。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, _ = _setup_chapter(db, client, volume["id"])
    row = db.get(Chapter, chapter["id"])
    row.chapter_sync_status = "in_sync"
    row.entry_handoff_status = "in_sync"
    db.commit()

    # 创建章节运行（new_chapter + 已接受 plan）。
    from app.db.models import ChapterPlanRevisionLink

    link = db.execute(
        select(ChapterPlanRevisionLink).where(ChapterPlanRevisionLink.chapter_id == chapter["id"])
    ).scalar_one()
    resp = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json={
            "run_scope": "chapter",
            "request_type": "new_chapter",
            "decision_target": "chapter",
            "plan_revision_id": link.plan_revision_id,
        },
        headers={"Idempotency-Key": "auto-run-key"},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    # 置 waiting_feedback 并提交作者 accept（target=chapter）。
    run = db.get(GenerationRun, run_id)
    run.status = "waiting_feedback"
    db.commit()
    staged = aggregate_chapter_revision(db, chapter["id"], [], "r", _author_ctx())
    db.commit()
    accept_resp = client.post(
        f"/api/runs/{run_id}/decisions",
        json={
            "idempotency_key": "auto-accept-key",
            "expected_run_version": 1,
            "target": "chapter",
            "decision": "accept",
            "chapter_revision_id": staged.id,
        },
        headers={"Idempotency-Key": "auto-accept-key"},
    )
    assert accept_resp.status_code == 200, accept_resp.text
    db.commit()
    db.expire_all()
    # 断言 chapter_revision.accepted outbox 消息自动入队。
    msg = db.execute(
        select(RunOutboxRecord).where(
            RunOutboxRecord.resource_type == "chapter_revision",
            RunOutboxRecord.resource_id == staged.id,
        )
    ).scalar_one_or_none()
    assert msg is not None
    assert msg.payload["event_type"] == "chapter_revision.accepted"
    assert msg.payload["chapter_id"] == chapter["id"]
    assert msg.payload["accepted_chapter_revision_id"] == staged.id

    # 消费者处理 -> 创建章节 Canon 运行。
    handle_chapter_accepted_outbox(db, msg.payload)
    db.commit()
    canon_runs = db.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter["id"],
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == staged.id,
        )
    ).scalars().all()
    assert len(canon_runs) == 1
    # 重复消费幂等：不产生重复运行。
    handle_chapter_accepted_outbox(db, msg.payload)
    db.commit()
    canon_runs = db.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter["id"],
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == staged.id,
        )
    ).scalars().all()
    assert len(canon_runs) == 1


def test_canon_decisions_three_types_confirm_reject_defer(client, db):
    """三类候选逐条确认/拒绝/暂缓，候选状态与决策记录正确。"""
    from app.db.models import PlotThreadUpdate, TimelineEventCandidate

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run(client, chapter["id"], rev_id, "dec3-key")
    run_id = run["run_id"]
    from app.domain.story_bible import upsert_canon_candidates

    cand_f = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], rev_id, local_key="lf", claim="f")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k1", "source": "agent"},
    )[0]
    cand_e = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], rev_id, ctype="timeline_event", local_key="le", claim="e",
                            content={"claim": "e", "entity_id": None, "paragraph_ref": "p3",
                                     "effective_story_time": {"value": "第3章", "precision": "exact"},
                                     "narrative_knowledge": "objective"})],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k2", "source": "agent"},
    )[0]
    cand_p = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], rev_id, ctype="plot_thread", local_key="lp", claim="p",
                            content={"claim": "p", "entity_id": None, "paragraph_ref": "p3",
                                     "effective_story_time": {"value": "第3章", "precision": "exact"},
                                     "narrative_knowledge": "objective",
                                     "state": "advanced", "planned_resolution": "第5章"})],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k3", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()

    body = _decision_body(run_id, "dec3", [
        {"candidate_id": cand_f["id"], "candidate_type": "fact", "decision": "confirm"},
        {"candidate_id": cand_e["id"], "candidate_type": "timeline_event", "decision": "reject"},
        {"candidate_id": cand_p["id"], "candidate_type": "plot_thread", "decision": "defer"},
    ])
    resp = client.post(
        f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "dec3"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["status"] == "accepted"
    assert db.get(FactCandidate, cand_f["id"]).status == "accepted"
    assert db.get(TimelineEventCandidate, cand_e["id"]).status == "rejected"
    assert db.get(PlotThreadUpdate, cand_p["id"]).status == "deferred"
    assert db.query(CanonDecisionRecord).filter(CanonDecisionRecord.candidate_id.in_([cand_f["id"], cand_e["id"], cand_p["id"]])).count() == 3
    # 章节级确认生成正式 CanonFact（fact），reject/defer 不生成。
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 1


def test_canon_decision_idempotent_replay_and_reuse(client, db):
    """同键同请求重放；同键不同请求返回 IDEMPOTENCY_KEY_REUSE。"""
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run(client, chapter["id"], rev_id, "idem-key")
    run_id = run["run_id"]
    cand = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], rev_id, local_key="li", claim="i")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    body = _decision_body(run_id, "idem", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
    ])
    headers = {"Idempotency-Key": "idem"}
    first = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers=headers)
    assert first.status_code == 200, first.text
    # 同键同请求重放 -> 幂等返回同一命令。
    second = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["command_id"] == first.json()["command_id"]
    # 同键不同请求（reject）-> IDEMPOTENCY_KEY_REUSE。
    body2 = _decision_body(run_id, "idem", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "reject"},
    ])
    third = client.post(f"/api/runs/{run_id}/canon-decisions", json=body2, headers=headers)
    assert third.status_code == 409
    assert third.json()["code"] == "IDEMPOTENCY_KEY_REUSE"
    assert db.get(FactCandidate, cand["id"]).status == "accepted"


def test_expired_source_wrong_scope_invalid_ref_rejected(client, db):
    """过期来源、错误作用域、无效引用被拒绝。"""
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run(client, chapter["id"], rev_id, "exp-key")
    run_id = run["run_id"]
    # 过期来源：旧 rev 来源候选。
    stale = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], "stale-rev", local_key="ls", claim="s")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k", "source": "agent"},
    )[0]
    # 错误作用域：场景级候选。
    scene, srev_id = _setup_scene(db, client, chapter["id"])
    wrong_scope = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], srev_id, scope="scene", scene_id=scene["id"], local_key="lw", claim="w")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k2", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    # 过期来源（stale-rev）确认 -> 拒绝。
    r1 = client.post(f"/api/runs/{run_id}/canon-decisions", json=_decision_body(run_id, "exp1", [
        {"candidate_id": stale["id"], "candidate_type": "fact", "decision": "confirm"},
    ]), headers={"Idempotency-Key": "exp1"})
    assert r1.status_code == 409
    assert r1.json()["code"] == "SCENE_STATE_INCOMPATIBLE"
    # 错误作用域（场景候选进入章节确认）-> 拒绝。
    r2 = client.post(f"/api/runs/{run_id}/canon-decisions", json=_decision_body(run_id, "exp2", [
        {"candidate_id": wrong_scope["id"], "candidate_type": "fact", "decision": "confirm"},
    ]), headers={"Idempotency-Key": "exp2"})
    assert r2.status_code == 409
    assert r2.json()["code"] == "SCENE_STATE_INCOMPATIBLE"
    # 无效引用（不存在的候选）-> 拒绝。
    r3 = client.post(f"/api/runs/{run_id}/canon-decisions", json=_decision_body(run_id, "exp3", [
        {"candidate_id": "nope", "candidate_type": "fact", "decision": "confirm"},
    ]), headers={"Idempotency-Key": "exp3"})
    assert r3.status_code == 409
    assert r3.json()["code"] == "SCENE_STATE_INCOMPATIBLE"


def test_chapter_canon_run_rejects_stale_entry_handoff(client, db):
    """章节 Canon 创建校验 entry_handoff_status：stale 入口被拒绝。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    # 上游版本变化使入口 handoff 失效。
    row = db.get(Chapter, chapter["id"])
    row.entry_handoff_status = "stale"
    db.commit()
    r = client.post(
        f"/api/chapters/{chapter['id']}/canon-runs",
        json={"canon_scope": "chapter", "accepted_chapter_revision_id": rev_id},
        headers={"Idempotency-Key": "stale-handoff"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "CHAPTER_HANDOFF_CONFLICT"


def test_chapter_canon_run_rejects_out_of_sync(client, db):
    """章节 Canon 创建校验 chapter_sync_status：out_of_sync 被拒绝。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    row = db.get(Chapter, chapter["id"])
    row.chapter_sync_status = "out_of_sync"
    db.commit()
    r = client.post(
        f"/api/chapters/{chapter['id']}/canon-runs",
        json={"canon_scope": "chapter", "accepted_chapter_revision_id": rev_id},
        headers={"Idempotency-Key": "oos-key"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "CHAPTER_OUT_OF_SYNC"


def test_scene_confirm_does_not_update_global_canon(client, db):
    """场景级确认不更新全局 Canon。"""
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, _ = _setup_chapter(db, client, volume["id"])
    scene, srev_id = _setup_scene(db, client, chapter["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run_scene(client, scene["id"], srev_id, "scene-canon-key")
    run_id = run["run_id"]
    cand = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], srev_id, scope="scene", scene_id=scene["id"], local_key="ls2", claim="sc")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    body = _decision_body(run_id, "scene-dec", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
    ], canon_scope="scene")
    resp = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "scene-dec"})
    assert resp.status_code == 200, resp.text
    assert db.get(FactCandidate, cand["id"]).status == "accepted"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0


def test_scene_canon_run_rejects_missing_accepted_revision(client, db):
    """场景 Canon 运行必须提供并匹配当前 accepted 场景版本。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, _ = _setup_chapter(db, client, volume["id"])
    scene, srev_id = _setup_scene(db, client, chapter["id"])
    # 缺来源版本 -> 拒绝。
    r = client.post(
        f"/api/scenes/{scene['id']}/canon-runs",
        json={"canon_scope": "scene"},
        headers={"Idempotency-Key": "scene-missing"},
    )
    assert r.status_code in (400, 409)
    assert r.json()["code"] in ("COMMAND_CONTEXT_MISMATCH", "SCENE_STATE_INCOMPATIBLE")
    # 过期来源 -> 拒绝。
    r2 = client.post(
        f"/api/scenes/{scene['id']}/canon-runs",
        json={"canon_scope": "scene", "accepted_scene_revision_id": "stale"},
        headers={"Idempotency-Key": "scene-stale"},
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "SCENE_STATE_INCOMPATIBLE"


def test_canon_content_validation_rejects_incomplete(client, db):
    """内容校验：按候选类型缺段落引用/故事时间/叙事认识状态/状态均被拒绝。"""
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])

    def _make_run(key):
        run = _make_canon_run(client, chapter["id"], rev_id, key)
        run_id = run["run_id"]
        row = db.get(GenerationRun, run_id)
        row.status = "waiting_feedback"
        db.commit()
        return run_id

    def _cand(run_id, content, local_key, ctype="fact"):
        cand = upsert_canon_candidates(
            db, run_id,
            [_candidate_payload(project_id, chapter["id"], rev_id, ctype=ctype, local_key=local_key,
                                claim="x", content=content)],
            {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": local_key, "source": "agent"},
        )[0]
        db.commit()
        return cand

    def _confirm(run_id, key, cand, ctype="fact"):
        return client.post(f"/api/runs/{run_id}/canon-decisions", json=_decision_body(run_id, key, [
            {"candidate_id": cand["id"], "candidate_type": ctype, "decision": "confirm"},
        ]), headers={"Idempotency-Key": key})

    # 缺来源段落引用。
    run_id = _make_run("cv-ref")
    cand = _cand(run_id, {"claim": "x", "entity_id": None,
                          "effective_story_time": {"value": "第3章", "precision": "exact"},
                          "narrative_knowledge": "objective"}, "cv-ref")
    r = _confirm(run_id, "cv-ref", cand)
    assert r.status_code == 409 and r.json()["code"] == "SCENE_STATE_INCOMPATIBLE"

    # 缺故事内有效时间。
    run_id = _make_run("cv-time")
    cand = _cand(run_id, {"claim": "x", "entity_id": None, "paragraph_ref": "p3",
                          "narrative_knowledge": "objective"}, "cv-time")
    r = _confirm(run_id, "cv-time", cand)
    assert r.status_code == 409 and r.json()["code"] == "SCENE_STATE_INCOMPATIBLE"

    # 缺叙事认识状态。
    run_id = _make_run("cv-nk")
    cand = _cand(run_id, {"claim": "x", "entity_id": None, "paragraph_ref": "p3",
                          "effective_story_time": {"value": "第3章", "precision": "exact"}}, "cv-nk")
    r = _confirm(run_id, "cv-nk", cand)
    assert r.status_code == 409 and r.json()["code"] == "SCENE_STATE_INCOMPATIBLE"

    # timeline_event 缺状态无关，但缺故事时间已在上面覆盖；此处验证合法 fact 通过。
    run_id = _make_run("cv-ok")
    cand = _cand(run_id, {"claim": "x", "entity_id": None, "paragraph_ref": "p3",
                          "effective_story_time": {"value": "第3章", "precision": "exact"},
                          "narrative_knowledge": "objective"}, "cv-ok")
    r = _confirm(run_id, "cv-ok", cand)
    assert r.status_code == 200, r.text


def test_author_decision_cas_api_fence_and_old_token(client, db):
    """作者决策 CAS、API fence 与旧 token 拒绝。"""
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run(client, chapter["id"], rev_id, "cas-key")
    run_id = run["run_id"]
    cand = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], rev_id, local_key="lc", claim="c")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    # CAS 冲突：expected_run_version 不匹配。
    bad = _decision_body(run_id, "cas-bad", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
    ], expected_run_version=99)
    r = client.post(f"/api/runs/{run_id}/canon-decisions", json=bad, headers={"Idempotency-Key": "cas-bad"})
    assert r.status_code == 409
    assert r.json()["code"] == "RUN_STATE_CONFLICT"
    # 正确 CAS 提交 -> 使用 API command fence。
    ok = _decision_body(run_id, "cas-ok", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
    ])
    r2 = client.post(f"/api/runs/{run_id}/canon-decisions", json=ok, headers={"Idempotency-Key": "cas-ok"})
    assert r2.status_code == 200, r2.text
    db.expire_all()
    run_row = db.get(GenerationRun, run_id)
    assert run_row.write_owner_kind == "api_command"
    assert run_row.write_owner_id == r2.json()["command_id"]
    # 旧 token 拒绝：直接用旧 fencing token 写事件。
    from app.runtime.run_events import PostgresRunEventStore

    with pytest.raises(Exception):
        PostgresRunEventStore(db).emit(
            run_id, "run_queued", {}, fencing_token=0, producer_command_id="old"
        )


def test_canon_api_does_not_call_writing_agent(client, db):
    """Canon 专用入口不调用 WritingAgent（仅创建运行，不触发 Agent/正文）。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    run = _make_canon_run(client, chapter["id"], rev_id, "no-wa-key")
    assert run["status"] == "queued"
    # 无任何 RunDecision 被写入（HTTP 只入队，不执行 Agent）。
    from app.db.models import RunDecision

    assert db.query(RunDecision).filter(RunDecision.generation_run_id == run["run_id"]).count() == 0
    # 运行没有执行 language graph 的痕迹（无 last_durable_node）。
    assert run["current_node"] is None


def _prepare_cancel_run(client, db, chapter_id, rev_id, key, claim="c", local_key="lc"):
    """建 Canon 运行 + 一个 pending 候选 + 置 waiting_feedback，返回 (run_id, cand)。

    claim/local_key 可覆盖，用于在同一测试内为不同运行构造指纹不同的候选，
    避免 upsert_canon_candidates 的 (来源, 类型, 指纹) 幂等去重复用同一条候选。
    """
    from app.domain.story_bible import upsert_canon_candidates

    project_id = _project_id(db, chapter_id)
    run = _make_canon_run(client, chapter_id, rev_id, key)
    run_id = run["run_id"]
    cand = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter_id, rev_id, local_key=local_key, claim=claim)],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    return run_id, cand


def test_cancel_confirm_keeps_pending_and_no_official_canon(client, db):
    """取消本次确认：不写正式 Canon，未决候选保留 pending，可后续处理。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run_id, cand = _prepare_cancel_run(client, db, chapter["id"], rev_id, "cc-key")
    # 取消本次确认（不携带 candidate_decisions）。
    resp = client.post(
        f"/api/runs/{run_id}/canon-decisions",
        json=_cancel_body(run_id, "cc", "confirm"),
        headers={"Idempotency-Key": "cc"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["status"] == "queued"
    # 未决候选保留 pending，不生成正式 Canon。
    assert db.get(FactCandidate, cand["id"]).status == "pending"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    # 候选可后续处理：再次进入 waiting_feedback 后确认成功（run_version 已为 2）。
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    resp2 = client.post(
        f"/api/runs/{run_id}/canon-decisions",
        json=_decision_body(run_id, "cc2", [
            {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
        ], expected_run_version=2),
        headers={"Idempotency-Key": "cc2"},
    )
    assert resp2.status_code == 200, resp2.text
    assert db.get(FactCandidate, cand["id"]).status == "accepted"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 1


def test_cancel_run_discards_pending_candidates(client, db):
    """取消整个运行：未决候选原子转 discarded，运行转 cancelled。"""
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run_id, cand = _prepare_cancel_run(client, db, chapter["id"], rev_id, "cr-key")
    # 追加一个 deferred 候选（已决策，不受 discard 影响）。
    deferred = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], rev_id, local_key="ld", claim="d")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k2", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    cand = db.get(FactCandidate, cand["id"])
    cand.status = "deferred"
    db.commit()
    # 取消整个运行。
    resp = client.post(
        f"/api/runs/{run_id}/canon-decisions",
        json=_cancel_body(run_id, "cr", "run"),
        headers={"Idempotency-Key": "cr"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["status"] == "cancelled"
    # 未决候选原子转 discarded；已决策候选保持 deferred。
    assert db.get(FactCandidate, cand.id).status == "deferred"
    assert db.get(FactCandidate, deferred["id"]).status == "discarded"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0


def test_cancel_request_must_not_carry_candidate_decisions(client, db):
    """取消请求携带 candidate_decisions 被拒绝。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    run_id, cand = _prepare_cancel_run(client, db, chapter["id"], rev_id, "ccw-key")
    body = _cancel_body(run_id, "ccw", "confirm")
    body["candidate_decisions"] = [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"}
    ]
    resp = client.post(
        f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "ccw"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "COMMAND_CONTEXT_MISMATCH"


def test_cancel_cas_api_fence_and_idempotent_replay(client, db):
    """取消使用 CAS、API fence，重复取消幂等。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    run_id, cand = _prepare_cancel_run(client, db, chapter["id"], rev_id, "ccas-key")
    # CAS 冲突：expected_run_version 不匹配。
    bad = _cancel_body(run_id, "ccas-bad", "confirm", expected_run_version=99)
    r = client.post(f"/api/runs/{run_id}/canon-decisions", json=bad, headers={"Idempotency-Key": "ccas-bad"})
    assert r.status_code == 409 and r.json()["code"] == "RUN_STATE_CONFLICT"
    # 正确取消 -> 使用 API command fence。
    ok = _cancel_body(run_id, "ccas", "run")
    r2 = client.post(f"/api/runs/{run_id}/canon-decisions", json=ok, headers={"Idempotency-Key": "ccas"})
    assert r2.status_code == 200, r2.text
    db.expire_all()
    run_row = db.get(GenerationRun, run_id)
    assert run_row.write_owner_kind == "api_command"
    assert run_row.write_owner_id == r2.json()["command_id"]
    # 重复取消幂等：同键同请求返回同一 command_id。
    r3 = client.post(f"/api/runs/{run_id}/canon-decisions", json=ok, headers={"Idempotency-Key": "ccas"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["command_id"] == r2.json()["command_id"]
    # 同键不同请求（cancel_scope=confirm）-> IDEMPOTENCY_KEY_REUSE。
    other = _cancel_body(run_id, "ccas", "confirm")
    r4 = client.post(f"/api/runs/{run_id}/canon-decisions", json=other, headers={"Idempotency-Key": "ccas"})
    assert r4.status_code == 409 and r4.json()["code"] == "IDEMPOTENCY_KEY_REUSE"


def test_decision_requires_at_least_one_candidate(client, db):
    """confirm/reject/defer 必须至少包含一条候选；只有 cancel 允许空候选列表。"""
    from app.db.models import RunDecision

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    run = _make_canon_run(client, chapter["id"], rev_id, "empty-key")
    run_id = run["run_id"]
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    # 默认 decision=confirm，空候选列表 -> 拒绝。
    body = _decision_body(run_id, "empty", [])
    resp = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "empty"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "COMMAND_CONTEXT_MISMATCH"
    # 无副作用：运行版本与状态不变，无决策记录。
    db.expire_all()
    run_row = db.get(GenerationRun, run_id)
    assert run_row.run_version == 1
    assert run_row.status == "waiting_feedback"
    assert db.query(RunDecision).filter(RunDecision.generation_run_id == run_id).count() == 0


def test_decision_rejects_duplicate_candidate_ids(client, db):
    """同一候选 ID 在决策列表中重复被拒绝，且不产生任何写入。"""
    from app.db.models import RunDecision

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run_id, cand = _prepare_cancel_run(client, db, chapter["id"], rev_id, "dup-key")
    body = _decision_body(run_id, "dup", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "reject"},
    ])
    resp = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "dup"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "COMMAND_CONTEXT_MISMATCH"
    db.expire_all()
    # 无副作用：候选保持 pending、无正式 Canon、运行版本与状态不变、无决策记录。
    assert db.get(FactCandidate, cand["id"]).status == "pending"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    run_row = db.get(GenerationRun, run_id)
    assert run_row.run_version == 1
    assert run_row.status == "waiting_feedback"
    assert db.query(RunDecision).filter(RunDecision.generation_run_id == run_id).count() == 0


def test_decision_rejects_candidate_from_other_run(client, db):
    """候选必须属于当前 Canon 运行；跨运行候选被拒绝且无副作用。"""
    from app.db.models import RunDecision

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run_a, cand_a = _prepare_cancel_run(client, db, chapter["id"], rev_id, "oa-key", claim="ca", local_key="oa")
    run_b, _ = _prepare_cancel_run(client, db, chapter["id"], rev_id, "ob-key", claim="cb", local_key="ob")
    # 对 run_b 提交 run_a 的候选 -> 拒绝。
    body = _decision_body(run_b, "ob", [
        {"candidate_id": cand_a["id"], "candidate_type": "fact", "decision": "confirm"},
    ])
    resp = client.post(f"/api/runs/{run_b}/canon-decisions", json=body, headers={"Idempotency-Key": "ob"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "SCENE_STATE_INCOMPATIBLE"
    db.expire_all()
    # 无副作用：候选保持 pending、无正式 Canon、运行版本与状态不变、无决策记录。
    assert db.get(FactCandidate, cand_a["id"]).status == "pending"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    run_b_row = db.get(GenerationRun, run_b)
    assert run_b_row.run_version == 1
    assert run_b_row.status == "waiting_feedback"
    assert db.query(RunDecision).filter(RunDecision.generation_run_id == run_b).count() == 0


def test_decision_rejects_candidate_type_mismatch(client, db):
    """请求声明类型与候选实际类型不一致被拒绝且无副作用。"""
    from app.db.models import RunDecision

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run_id, cand = _prepare_cancel_run(client, db, chapter["id"], rev_id, "tm-key")  # fact 候选
    body = _decision_body(run_id, "tm", [
        {"candidate_id": cand["id"], "candidate_type": "timeline_event", "decision": "confirm"},
    ])
    resp = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "tm"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "SCENE_STATE_INCOMPATIBLE"
    db.expire_all()
    assert db.get(FactCandidate, cand["id"]).status == "pending"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    run_row = db.get(GenerationRun, run_id)
    assert run_row.run_version == 1
    assert run_row.status == "waiting_feedback"
    assert db.query(RunDecision).filter(RunDecision.generation_run_id == run_id).count() == 0


def test_decision_rejects_candidate_source_mismatch(client, db):
    """候选来源版本与 Canon 运行消费的来源不一致被拒绝且无副作用。"""
    from app.db.models import RunDecision
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    run = _make_canon_run(client, chapter["id"], rev_id, "sm-key")
    run_id = run["run_id"]
    # 候选属于本运行但来源是过期版本（stale-rev != 运行消费的 rev_id）。
    cand = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], "stale-rev", local_key="sm", claim="sm")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    body = _decision_body(run_id, "sm", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
    ])
    resp = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "sm"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "SCENE_STATE_INCOMPATIBLE"
    db.expire_all()
    assert db.get(FactCandidate, cand["id"]).status == "pending"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    run_row = db.get(GenerationRun, run_id)
    assert run_row.run_version == 1
    assert run_row.status == "waiting_feedback"
    assert db.query(RunDecision).filter(RunDecision.generation_run_id == run_id).count() == 0


def test_decision_rejects_candidate_target_mismatch(client, db):
    """候选目标场景与运行目标不一致被拒绝且无副作用。"""
    from app.db.models import RunDecision
    from app.domain.story_bible import upsert_canon_candidates

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    project_id = _project_id(db, chapter["id"])
    scene1, srev1 = _setup_scene(db, client, chapter["id"])
    scene2, _ = _setup_scene(db, client, chapter["id"])
    run = _make_canon_run_scene(client, scene1["id"], srev1, "tm2-key")
    run_id = run["run_id"]
    # 候选场景指向 scene2，而运行目标是 scene1 -> 目标不匹配。
    cand = upsert_canon_candidates(
        db, run_id,
        [_candidate_payload(project_id, chapter["id"], srev1, scope="scene", scene_id=scene2["id"],
                            local_key="tm2", claim="tm2")],
        {"generation_run_id": run_id, "actor_id": "a", "idempotency_key": "k", "source": "agent"},
    )[0]
    db.commit()
    row = db.get(GenerationRun, run_id)
    row.status = "waiting_feedback"
    db.commit()
    body = _decision_body(run_id, "tm2", [
        {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "confirm"},
    ], canon_scope="scene")
    resp = client.post(f"/api/runs/{run_id}/canon-decisions", json=body, headers={"Idempotency-Key": "tm2"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "SCENE_STATE_INCOMPATIBLE"
    db.expire_all()
    assert db.get(FactCandidate, cand["id"]).status == "pending"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    run_row = db.get(GenerationRun, run_id)
    assert run_row.run_version == 1
    assert run_row.status == "waiting_feedback"
    assert db.query(RunDecision).filter(RunDecision.generation_run_id == run_id).count() == 0


def test_concurrent_consumers_create_single_canon_run(client, db):
    """真实双会话并发：两个消费者同时消费同一 accepted 事件，只创建一个 Canon 运行。"""
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from ..conftest import TEST_DATABASE_URL

    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter, rev_id = _setup_chapter(db, client, volume["id"])
    payload = {
        "event_type": "chapter_revision.accepted",
        "chapter_id": chapter["id"],
        "accepted_chapter_revision_id": rev_id,
    }

    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def consume() -> None:
        engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        s = factory()
        try:
            barrier.wait(timeout=5)
            handle_chapter_accepted_outbox(s, payload)
            s.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            s.rollback()
        finally:
            s.close()
            engine.dispose()

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert errors == [], errors

    db.expire_all()
    runs = db.execute(
        select(GenerationRun).where(
            GenerationRun.chapter_id == chapter["id"],
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == rev_id,
        )
    ).scalars().all()
    assert len(runs) == 1
