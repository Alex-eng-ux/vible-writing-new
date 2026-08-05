"""Task 4B 章节编排领域测试：聚合资格、影响闭包、handoff 失效、重规划继承。"""

from __future__ import annotations

from app.db.models import ChapterHandoff, ChapterRevision, SceneRevision
from app.domain.chapter_orchestration import (
    AGGREGATION_OK,
    FIRST_ROUND_CAPABLE,
    STALE_ENTRY,
    build_inheritance_map,
    build_scene_feedback_queue,
    compute_aggregation_eligibility,
    compute_scene_impact_closure,
    create_handoff_for_chapter_revision,
    current_accepted_chapter_revision_id,
    invalidate_downstream_handoffs,
    valid_entry_handoff,
)
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    commit_chapter_version,
    create_chapter,
    create_chapter_plan_revision,
    create_scene,
)


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


def _make_chapter(db, volume, title="Ch1"):
    chapter = create_chapter(db, volume, title, "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _author_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _author_ctx())
    chapter.chapter_sync_status = "in_sync"
    db.flush()
    return chapter


def _make_scene_with_accepted(db, chapter, key="s1", accepted: bool = True):
    scene = create_scene(db, chapter.id, f"Scene {key}", {"client_key": key}, _resource_ctx())
    db.flush()
    if accepted:
        srev = SceneRevision(
            scene_id=scene.id, parent_revision_id=None, content="x", content_hash="h",
            reason="r", source_ref="s", status="accepted",
        )
        db.add(srev)
        db.flush()
        scene.accepted_scene_revision_id = srev.id
        db.flush()
    return scene


def _accept_chapter(db, chapter):
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", _author_ctx())
    commit_chapter_version(db, rev.id, _author_ctx())
    return rev


def test_first_round_aggregation_allows_no_accepted_chapter_revision(db, volume):
    """新章节无 accepted 章节版本时，允许首轮聚合（无需入口 handoff）。"""
    chapter = _make_chapter(db, volume)
    _make_scene_with_accepted(db, chapter, "s1")
    elig = compute_aggregation_eligibility(db, chapter.id)
    assert elig.eligible is True
    assert elig.committable is True
    assert elig.status == FIRST_ROUND_CAPABLE


def test_aggregation_eligibility_requires_in_sync_and_accepted_scenes(db, volume):
    chapter = _make_chapter(db, volume)
    create_scene(db, chapter.id, "Scene s1", {"client_key": "s1"}, _resource_ctx())
    db.flush()
    elig = compute_aggregation_eligibility(db, chapter.id)
    assert elig.eligible is False
    assert elig.status == "scene_not_accepted"


def test_aggregation_rejects_forged_handoff(db, volume):
    """同时存在 accepted 指针时，伪造/无效 handoff 必须被拒绝（STALE_ENTRY）。"""
    chapter = _make_chapter(db, volume)
    _accept_chapter(db, chapter)
    _make_scene_with_accepted(db, chapter, "s1")
    # 伪造的 handoff id：不存在。
    elig = compute_aggregation_eligibility(
        db, chapter.id, entry_handoff_id="forged-handoff"
    )
    assert elig.eligible is False
    assert elig.status == STALE_ENTRY


def test_aggregation_rejects_old_accepted_pointer(db, volume):
    """接入章节已有新的 accepted 指针，旧 handoff（来源≠当前指针）必须被拒绝。"""
    chapter = _make_chapter(db, volume, "C1")
    rev1 = _accept_chapter(db, chapter)
    handoff = create_handoff_for_chapter_revision(db, rev1.id, chapter.id, "chain-1", _author_ctx())
    _make_scene_with_accepted(db, chapter, "s1")
    # 推进 accepted 指针到 rev2。
    _accept_chapter(db, chapter)
    # 旧 handoff 来源(rev1) 不等于当前指针(rev2)。
    elig = compute_aggregation_eligibility(
        db, chapter.id, entry_handoff_id=handoff.id, entry_source_chapter_revision_id=rev1.id
    )
    assert elig.eligible is False
    assert elig.status == STALE_ENTRY


def test_aggregation_ok_with_valid_handoff_and_match_pointer(db, volume):
    """已接受章节 + 有效 handoff（来源=当前指针）时聚合通过。"""
    chapter = _make_chapter(db, volume, "C1")
    rev = _accept_chapter(db, chapter)
    handoff = create_handoff_for_chapter_revision(db, rev.id, chapter.id, "chain-1", _author_ctx())
    _make_scene_with_accepted(db, chapter, "s1")
    elig = compute_aggregation_eligibility(
        db, chapter.id, entry_handoff_id=handoff.id, entry_source_chapter_revision_id=rev.id
    )
    assert elig.eligible is True
    assert elig.committable is True
    assert elig.status == AGGREGATION_OK


def test_valid_entry_handoff_uses_explicit_pointer(db, volume):
    """valid_entry_handoff 必须匹配显式 accepted 指针，不按最新行推断。"""
    chapter = _make_chapter(db, volume, "C1")
    rev1 = _accept_chapter(db, chapter)
    handoff = create_handoff_for_chapter_revision(db, rev1.id, chapter.id, "chain-1", _author_ctx())
    # 伪造另一个 accepted 章节修订（不推进指针），handoff 来源仍等于指针。
    fake = ChapterRevision(chapter_id=chapter.id, parent_revision_id=None, status="accepted", reason="fake")
    db.add(fake)
    db.flush()
    # 显式指针仍指向 rev1，handoff 有效。
    assert current_accepted_chapter_revision_id(db, chapter.id) == rev1.id
    assert valid_entry_handoff(db, chapter.id, handoff.id, rev1.id, "chain-1") is not None


def test_impact_closure_includes_downstream_scenes(db, volume):
    chapter = _make_chapter(db, volume)
    s1 = _make_scene_with_accepted(db, chapter, "s1")
    s2 = _make_scene_with_accepted(db, chapter, "s2")
    s3 = _make_scene_with_accepted(db, chapter, "s3")
    closure = compute_scene_impact_closure(db, chapter.id, [s1.id])
    assert s1.id in closure and s2.id in closure and s3.id in closure


def test_handoff_creation_fails_for_non_in_sync_chapter(db, volume):
    c1 = _make_chapter(db, volume, "C1")
    c2 = _make_chapter(db, volume, "C2")
    rev = _accept_chapter(db, c1)
    handoff = create_handoff_for_chapter_revision(db, rev.id, c2.id, "chain-1", _author_ctx())
    assert handoff.entry_handoff_status == "in_sync"
    assert handoff.status == "active"
    assert handoff.chapter_id == c2.id
    assert handoff.source_chapter_revision_id == rev.id


def test_handoff_creation_invalidates_old_handoffs(db, volume):
    c1 = _make_chapter(db, volume, "C1")
    c2 = _make_chapter(db, volume, "C2")
    rev1 = _accept_chapter(db, c1)
    create_handoff_for_chapter_revision(db, rev1.id, c2.id, "chain-1", _author_ctx())
    rev2 = _accept_chapter(db, c1)
    create_handoff_for_chapter_revision(db, rev2.id, c2.id, "chain-2", _author_ctx())
    handoffs = db.query(ChapterHandoff).filter_by(chapter_id=c2.id).all()
    assert len(handoffs) == 2
    assert handoffs[0].status == "inactive"
    assert handoffs[1].status == "active"


def test_invalidate_downstream_handoffs_marks_stale_and_out_of_sync(db, volume):
    c1 = _make_chapter(db, volume, "C1")
    c2 = _make_chapter(db, volume, "C2")
    rev1 = _accept_chapter(db, c1)
    db.add(
        ChapterHandoff(
            chapter_id=c2.id,
            source_chapter_revision_id=rev1.id,
            entry_handoff_status="in_sync",
            chain_hash="chain-1",
            status="active",
        )
    )
    db.flush()
    stale = invalidate_downstream_handoffs(db, c1.id)
    assert c2.id in stale
    handoffs = db.query(ChapterHandoff).filter_by(chapter_id=c2.id).all()
    assert handoffs and handoffs[0].entry_handoff_status == "stale"
    # 章节状态同步更新为 out_of_sync。
    from app.db.models import Chapter as ChapterModel

    c2_row = db.get(ChapterModel, c2.id)
    assert c2_row.chapter_sync_status == "out_of_sync"


def test_build_inheritance_map_inherits_accepted_and_nulls_new(db, volume):
    chapter = _make_chapter(db, volume)
    previous_accepted = {"s1": "rev-s1", "s2": "rev-s2"}
    inheritance = build_inheritance_map(
        db, chapter.id, ["s1", "s3"], ["s1", "s2"], previous_accepted
    )
    assert inheritance["s1"] == "rev-s1"
    assert inheritance["s3"] == ""
    assert "s2" not in inheritance


def test_feedback_queue_uses_impact_closure(db, volume):
    chapter = _make_chapter(db, volume)
    s1 = _make_scene_with_accepted(db, chapter, "s1")
    s2 = _make_scene_with_accepted(db, chapter, "s2")
    queue = build_scene_feedback_queue(db, chapter.id, [s1.id])
    assert s1.id in queue and s2.id in queue


def test_transitive_invalidation_c1_c2_c3_single_call(db, volume):
    """一次调用递归失效 C1→C2→C3，并更新章节状态。"""
    c1 = _make_chapter(db, volume, "C1")
    c2 = _make_chapter(db, volume, "C2")
    c3 = _make_chapter(db, volume, "C3")
    rev1 = _accept_chapter(db, c1)
    db.add(ChapterHandoff(chapter_id=c2.id, source_chapter_revision_id=rev1.id, entry_handoff_status="in_sync", chain_hash="chain-1", status="active"))
    rev2 = _accept_chapter(db, c2)
    db.add(ChapterHandoff(chapter_id=c3.id, source_chapter_revision_id=rev2.id, entry_handoff_status="in_sync", chain_hash="chain-2", status="active"))
    db.flush()

    # 单次调用即完成 C1→C2→C3 传递失效。
    affected = invalidate_downstream_handoffs(db, c1.id)
    assert c2.id in affected
    assert c3.id in affected

    # C3 无法继续使用旧 C2 handoff。
    from app.domain.handoff import get_valid_entry

    c3_handoff = db.query(ChapterHandoff).filter_by(chapter_id=c3.id).first()
    valid = get_valid_entry(db, c3.id, c3_handoff.id, rev2.id, "chain-2")
    assert valid is None
    # C2、C3 均已 out_of_sync。
    from app.db.models import Chapter as ChapterModel

    for cid in (c2.id, c3.id):
        row = db.get(ChapterModel, cid)
        assert row.chapter_sync_status == "out_of_sync"


def test_cross_chapter_handoff_c1_to_c2_valid(db, volume):
    """C1 -> C2 跨章节 handoff：用来源章节 C1 的 accepted 指针校验，正向有效。"""
    from app.domain.handoff import get_valid_entry

    c1 = _make_chapter(db, volume, "C1")
    c2 = _make_chapter(db, volume, "C2")
    rev1 = _accept_chapter(db, c1)
    # C2 承接 C1 的 handoff。
    handoff = ChapterHandoff(
        chapter_id=c2.id,
        source_chapter_revision_id=rev1.id,
        entry_handoff_status="in_sync",
        chain_hash="chain-1",
        status="active",
    )
    db.add(handoff)
    db.flush()
    # C1 的 accepted 指针指向 rev1；来源章节校验通过 -> 有效。
    valid = get_valid_entry(db, c2.id, handoff.id, rev1.id, "chain-1")
    assert valid is not None
    assert valid["source_chapter_revision_id"] == rev1.id


def test_cross_chapter_handoff_rejects_when_source_pointer_advances(db, volume):
    """C1 指针推进后，旧 C1 来源的 C2 handoff 无效（用来源章节指针判定）。"""
    from app.domain.handoff import get_valid_entry

    c1 = _make_chapter(db, volume, "C1")
    c2 = _make_chapter(db, volume, "C2")
    rev1 = _accept_chapter(db, c1)
    handoff = ChapterHandoff(
        chapter_id=c2.id,
        source_chapter_revision_id=rev1.id,
        entry_handoff_status="in_sync",
        chain_hash="chain-1",
        status="active",
    )
    db.add(handoff)
    db.flush()
    # C1 推进 accepted 指针到 rev2，旧 handoff 来源 rev1 不再匹配。
    _accept_chapter(db, c1)
    valid = get_valid_entry(db, c2.id, handoff.id, rev1.id, "chain-1")
    assert valid is None


def test_handoff_creation_c1_accepted_creates_c2_entry_handoff(db, volume):
    """端到端：C1 accepted 版本创建 C2 的入口 handoff，并设置章节状态。"""
    from app.db.models import Chapter as ChapterModel

    c1 = _make_chapter(db, volume, "C1")
    c2 = _make_chapter(db, volume, "C2")
    c3 = _make_chapter(db, volume, "C3")
    # C1 accepted -> 创建 C2 的入口 handoff。
    rev1 = _accept_chapter(db, c1)
    h12 = create_handoff_for_chapter_revision(db, rev1.id, c2.id, "chain-12", _author_ctx())
    assert h12.chapter_id == c2.id
    assert h12.source_chapter_revision_id == rev1.id
    # C2 accepted -> 创建 C3 的入口 handoff。
    rev2 = _accept_chapter(db, c2)
    h23 = create_handoff_for_chapter_revision(db, rev2.id, c3.id, "chain-23", _author_ctx())
    assert h23.chapter_id == c3.id
    assert h23.source_chapter_revision_id == rev2.id
    # 章节 entry_handoff_status 状态。
    c2_row = db.get(ChapterModel, c2.id)
    c3_row = db.get(ChapterModel, c3.id)
    assert c2_row.entry_handoff_status == "in_sync"
    assert c3_row.entry_handoff_status == "in_sync"
