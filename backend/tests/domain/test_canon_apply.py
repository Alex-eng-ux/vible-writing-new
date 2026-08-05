"""Task 4C 正式 Canon 更新路由领域测试。

覆盖：候选来源必须是当前 accepted 版本；场景级确认不更新全局 Canon；章节级
三类候选确认生成正式 CanonFact；reject/defer 状态；discarded/过期来源/错误
作用域被拒绝；同幂等键重复决策只产生一次结果；并发决策不覆盖彼此。
"""

from __future__ import annotations

import pytest

from app.db.models import (
    CanonDecisionRecord,
    CanonFact,
    FactCandidate,
    Volume,
)
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    commit_chapter_version,
    create_chapter,
    create_chapter_plan_revision,
    create_scene,
)
from app.domain.story_bible import (
    apply_canon_decisions,
    confirm_canon_decisions,
    upsert_canon_candidates,
    validate_canon_candidate_sources,
)
from app.errors import AppError


def _resource_ctx():
    return {"actor_id": "author-1", "idempotency_key": "key-1"}


def _agent_ctx(run_id="run-1", key="key-a"):
    return {
        "generation_run_id": run_id,
        "agent_run_id": "agent-1",
        "manual_command_id": None,
        "source": "agent",
        "actor_id": "author-1",
        "idempotency_key": key,
        "expected_run_version": 1,
        "lease_context": None,
        "write_fence": None,
    }


def _make_chapter(db, volume, title="Ch1"):
    chapter = create_chapter(db, volume, title, "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _agent_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _agent_ctx())
    chapter.chapter_sync_status = "in_sync"
    chapter.entry_handoff_status = "in_sync"
    db.flush()
    return chapter


def _accept_chapter(db, chapter):
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", _agent_ctx())
    commit_chapter_version(db, rev.id, _agent_ctx())
    return rev


def _make_scene_with_accepted(db, chapter, key="s1"):
    from app.db.models import SceneRevision

    scene = create_scene(db, chapter.id, f"Scene {key}", {"client_key": key}, _resource_ctx())
    db.flush()
    srev = SceneRevision(
        scene_id=scene.id,
        parent_revision_id=None,
        content="x",
        content_hash="h",
        reason="r",
        source_ref="s",
        status="accepted",
    )
    db.add(srev)
    db.flush()
    scene.accepted_scene_revision_id = srev.id
    db.flush()
    return scene


def _candidate_payload(
    project_id,
    chapter_id,
    scope="chapter",
    ctype="fact",
    source_rev="rev-1",
    local_key="l1",
    claim="claim",
    scene_id=None,
    content=None,
):
    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "scene_id": scene_id,
        "scope": scope,
        "candidate_type": ctype,
        "fingerprint": None,
        "source_revision_id": source_rev,
        "content": content or {"claim": claim, "entity_id": None},
        "local_key": local_key,
    }


def _project_id(db, volume):
    return db.get(Volume, volume).project_id


def test_candidate_sources_must_be_current_accepted(db, volume):
    """只能使用 accepted 版本作为候选来源：过期/未接受来源被拒。"""
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = _project_id(db, volume)
    # 未接受/伪造来源被拒。
    with pytest.raises(AppError) as exc:
        validate_canon_candidate_sources(
            db,
            [_candidate_payload(project_id, chapter.id, source_rev="not-accepted")],
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"
    # 当前 accepted 来源通过。
    validate_canon_candidate_sources(
        db,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id)],
        canon_scope="chapter",
        chapter_id=chapter.id,
    )
    # 缺来源被拒。
    with pytest.raises(AppError) as exc:
        validate_canon_candidate_sources(
            db,
            [_candidate_payload(project_id, chapter.id, source_rev=None)],
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "COMMAND_CONTEXT_MISMATCH"


def test_scene_confirm_does_not_update_global_canon(db, volume):
    """场景级确认只保存作用域记录，绝不生成全局 CanonFact。"""
    chapter = _make_chapter(db, volume)
    scene = _make_scene_with_accepted(db, chapter)
    project_id = _project_id(db, volume)
    run_id = "run-s1"
    cand = upsert_canon_candidates(
        db,
        run_id,
        [
            _candidate_payload(
                project_id,
                chapter.id,
                scope="scene",
                source_rev=scene.accepted_scene_revision_id,
                scene_id=scene.id,
                local_key="ls1",
            )
        ],
        _agent_ctx(run_id),
    )[0]
    records = confirm_canon_decisions(
        db,
        run_id,
        [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "accepted"}],
        _agent_ctx(run_id),
        canon_scope="scene",
        scene_id=scene.id,
    )
    assert len(records) == 1
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0
    assert db.get(FactCandidate, cand["id"]).status == "accepted"


def test_chapter_confirm_three_types_update_official_canon(db, volume):
    """章节级三类候选确认写入各自正确的正式结构，并做字段级断言。"""
    from app.db.models import PlotThread, TimelineEvent

    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = _project_id(db, volume)
    run_id = "run-c3"
    payloads = {
        "fact": _candidate_payload(
            project_id, chapter.id, ctype="fact", source_rev=rev.id,
            local_key="lf", claim="主角是侦探",
        ),
        "timeline_event": _candidate_payload(
            project_id, chapter.id, ctype="timeline_event", source_rev=rev.id,
            local_key="le", claim="第3章主角到达现场",
            content={
                "claim": "第3章主角到达现场",
                "entity_id": None,
                "effective_story_time": {"value": "第3章", "precision": "exact"},
                "entities": ["主角", "现场"],
            },
        ),
        "plot_thread": _candidate_payload(
            project_id, chapter.id, ctype="plot_thread", source_rev=rev.id,
            local_key="lp", claim="开启复仇线",
            content={
                "claim": "开启复仇线",
                "entity_id": None,
                "state": "advanced",
                "planned_resolution": "第5章",
            },
        ),
    }
    for i, (ctype, payload) in enumerate(payloads.items()):
        cand = upsert_canon_candidates(
            db, run_id, [payload], _agent_ctx(run_id)
        )[0]
        records = confirm_canon_decisions(
            db,
            run_id,
            [
                {
                    "candidate_id": cand["id"],
                    "candidate_type": ctype,
                    "decision": "accepted",
                    "local_key": payload["local_key"],
                }
            ],
            _agent_ctx(run_id, key=f"key-{i}"),
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
        assert len(records) == 1
    # 字段级断言：三类候选分别落 CanonFact / TimelineEvent / PlotThread。
    fact = db.query(CanonFact).filter(CanonFact.project_id == project_id).one()
    assert fact.fact_text == "主角是侦探"
    event = db.query(TimelineEvent).filter(TimelineEvent.project_id == project_id).one()
    assert event.event_text == "第3章主角到达现场"
    assert event.story_time == {"value": "第3章", "precision": "exact"}
    assert event.entities == ["主角", "现场"]
    thread = db.query(PlotThread).filter(PlotThread.project_id == project_id).one()
    assert thread.thread_text == "开启复仇线"
    assert thread.state == "advanced"
    assert thread.planned_resolution == "第5章"
    assert db.query(CanonDecisionRecord).count() == 3


def test_reject_and_defer_status(db, volume):
    """reject/defer 更新候选状态与决策记录，且不生成正式 Canon。"""
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = _project_id(db, volume)
    run_id = "run-rd"
    cand_r = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="lr", claim="reject")],
        _agent_ctx(run_id),
    )[0]
    cand_d = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="ld", claim="defer")],
        _agent_ctx(run_id),
    )[0]
    confirm_canon_decisions(
        db,
        run_id,
        [{"candidate_id": cand_r["id"], "candidate_type": "fact", "decision": "rejected"}],
        _agent_ctx(run_id, key="key-r"),
        canon_scope="chapter",
        chapter_id=chapter.id,
    )
    confirm_canon_decisions(
        db,
        run_id,
        [{"candidate_id": cand_d["id"], "candidate_type": "fact", "decision": "deferred"}],
        _agent_ctx(run_id, key="key-d"),
        canon_scope="chapter",
        chapter_id=chapter.id,
    )
    assert db.get(FactCandidate, cand_r["id"]).status == "rejected"
    assert db.get(FactCandidate, cand_d["id"]).status == "deferred"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 0


def test_discarded_expired_and_wrong_scope_rejected(db, volume):
    """discarded 候选、过期来源候选和错误作用域候选都被拒绝。"""
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = _project_id(db, volume)
    run_id = "run-e1"

    # 1) discarded 候选不可确认。
    cand = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="ldisc", claim="x")],
        _agent_ctx(run_id),
    )[0]
    apply_canon_decisions(
        db,
        [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "discarded"}],
        _agent_ctx(run_id),
    )
    with pytest.raises(AppError) as exc:
        confirm_canon_decisions(
            db,
            run_id,
            [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "accepted"}],
            _agent_ctx(run_id),
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"

    # 2) 过期来源：章节 accepted 指针推进后，旧来源候选被拒。
    cand2 = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="lstale", claim="stale")],
        _agent_ctx(run_id),
    )[0]
    _accept_chapter(db, chapter)  # 推进 accepted 指针。
    with pytest.raises(AppError) as exc:
        confirm_canon_decisions(
            db,
            run_id,
            [{"candidate_id": cand2["id"], "candidate_type": "fact", "decision": "accepted"}],
            _agent_ctx(run_id, key="key-2"),
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"

    # 3) 错误作用域：场景级候选进入章节确认被拒。
    scene = _make_scene_with_accepted(db, chapter)
    cand_s = upsert_canon_candidates(
        db,
        run_id,
        [
            _candidate_payload(
                project_id,
                chapter.id,
                scope="scene",
                source_rev=scene.accepted_scene_revision_id,
                scene_id=scene.id,
                local_key="lscope",
                claim="scope",
            )
        ],
        _agent_ctx(run_id),
    )[0]
    with pytest.raises(AppError) as exc:
        confirm_canon_decisions(
            db,
            run_id,
            [{"candidate_id": cand_s["id"], "candidate_type": "fact", "decision": "accepted"}],
            _agent_ctx(run_id, key="key-3"),
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"


def test_same_key_same_fingerprint_replays_and_different_rejected(db, volume):
    """同一幂等键且请求指纹相同才允许重放；不同决策内容返回 IDEMPOTENCY_KEY_REUSE。"""
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = _project_id(db, volume)
    run_id = "run-idem"
    cand = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="li")],
        _agent_ctx(run_id),
    )[0]
    decision = {"candidate_id": cand["id"], "candidate_type": "fact", "decision": "accepted"}
    first = confirm_canon_decisions(
        db,
        run_id,
        [decision],
        _agent_ctx(run_id, key="same-key"),
        canon_scope="chapter",
        chapter_id=chapter.id,
    )
    # 同键同指纹 -> 幂等重放，不产生新结果。
    second = confirm_canon_decisions(
        db,
        run_id,
        [decision],
        _agent_ctx(run_id, key="same-key"),
        canon_scope="chapter",
        chapter_id=chapter.id,
    )
    assert len(first) == 1
    assert second == []
    assert db.query(CanonDecisionRecord).filter(CanonDecisionRecord.candidate_id == cand["id"]).count() == 1
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 1
    # 同键不同决策内容（reject）-> IDEMPOTENCY_KEY_REUSE，且不改变候选/正式 Canon。
    with pytest.raises(AppError) as exc:
        confirm_canon_decisions(
            db,
            run_id,
            [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "rejected"}],
            _agent_ctx(run_id, key="same-key"),
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSE"
    assert db.get(FactCandidate, cand["id"]).status == "accepted"
    assert db.query(CanonFact).filter(CanonFact.project_id == project_id).count() == 1
    # 同键不同候选（候选 id 变化）同样拒绝。
    cand2 = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="li2", claim="other")],
        _agent_ctx(run_id),
    )[0]
    with pytest.raises(AppError) as exc:
        confirm_canon_decisions(
            db,
            run_id,
            [{"candidate_id": cand2["id"], "candidate_type": "fact", "decision": "accepted"}],
            _agent_ctx(run_id, key="same-key"),
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSE"
    assert db.get(FactCandidate, cand2["id"]).status == "pending"


def test_concurrent_decisions_do_not_overwrite(db, volume):
    """顺序重放验证：已决策候选的再次决策（不同键）被拒绝，不覆盖。"""
    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = _project_id(db, volume)
    run_id = "run-conc"
    cand = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="lc")],
        _agent_ctx(run_id),
    )[0]
    confirm_canon_decisions(
        db,
        run_id,
        [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "accepted"}],
        _agent_ctx(run_id, key="key-1"),
        canon_scope="chapter",
        chapter_id=chapter.id,
    )
    # 第二个并发决策（不同键）到达时候选已 accepted -> 拒绝，不覆盖。
    with pytest.raises(AppError) as exc:
        confirm_canon_decisions(
            db,
            run_id,
            [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "rejected"}],
            _agent_ctx(run_id, key="key-2"),
            canon_scope="chapter",
            chapter_id=chapter.id,
        )
    assert exc.value.code == "SCENE_STATE_INCOMPATIBLE"
    assert db.get(FactCandidate, cand["id"]).status == "accepted"


def test_concurrent_decision_two_sessions_do_not_overwrite(db, volume):
    """真实双会话并发：会话 A 持有行锁并提交 accepted，会话 B 的完整决策在
    锁释放后读到已决策状态，被 SCENE_STATE_INCOMPATIBLE 拒绝，不覆盖。"""
    import os
    import threading
    import time

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    chapter = _make_chapter(db, volume)
    rev = _accept_chapter(db, chapter)
    project_id = _project_id(db, volume)
    run_id = "run-conc2"
    cand = upsert_canon_candidates(
        db,
        run_id,
        [_candidate_payload(project_id, chapter.id, source_rev=rev.id, local_key="lc2")],
        _agent_ctx(run_id),
    )[0]
    db.commit()

    url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/novel_test",
    )
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s1 = factory()
    s2 = factory()
    results: dict = {}

    def worker2() -> None:
        try:
            confirm_canon_decisions(
                s2,
                run_id,
                [{"candidate_id": cand["id"], "candidate_type": "fact", "decision": "rejected"}],
                _agent_ctx(run_id, key="key-conc-b"),
                canon_scope="chapter",
                chapter_id=chapter.id,
            )
            results["worker2"] = "ok"
        except AppError as exc:
            results["worker2"] = exc.code

    try:
        # 会话 A：锁定候选并置 accepted，不提交（模拟并发写入者持有行锁）。
        row = s1.execute(
            select(FactCandidate).where(FactCandidate.id == cand["id"]).with_for_update()
        ).scalar_one()
        row.status = "accepted"
        s1.flush()
        # 会话 B 在后台线程完整调用 confirm（rejected）-> 阻塞在 FOR UPDATE。
        t = threading.Thread(target=worker2)
        t.start()
        time.sleep(0.5)  # 等待 worker2 阻塞在候选行锁。
        s1.commit()  # 释放锁；worker2 随后读到已决策状态。
        t.join(timeout=5)
        assert results.get("worker2") == "SCENE_STATE_INCOMPATIBLE"
        final_row = s1.execute(
            select(FactCandidate).where(FactCandidate.id == cand["id"])
        ).scalar_one()
        assert final_row.status == "accepted"
    finally:
        s1.rollback()
        s1.close()
        s2.rollback()
        s2.close()
        engine.dispose()
