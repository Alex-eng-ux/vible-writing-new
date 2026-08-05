from __future__ import annotations

from app.domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter,
    create_chapter_plan_revision,
)
from app.domain.handoff import create_chapter_handoff, get_valid_entry


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


def test_get_valid_entry_returns_only_in_sync_matching_handoff(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _author_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _author_ctx())
    # Create an accepted chapter revision via accepted plan materialization path.
    from app.domain.chapters import aggregate_chapter_revision, commit_chapter_version
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", _author_ctx())
    commit_chapter_version(db, rev.id, _author_ctx())
    handoff = create_chapter_handoff(db, rev.id, "chain-hash-1", _author_ctx())
    result = get_valid_entry(db, chapter.id, handoff["id"], rev.id, "chain-hash-1")
    assert result is not None
    assert result["entry_handoff_status"] == "in_sync"


def test_get_valid_entry_rejects_mismatched_chain_hash(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _author_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _author_ctx())
    from app.domain.chapters import aggregate_chapter_revision, commit_chapter_version
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", _author_ctx())
    commit_chapter_version(db, rev.id, _author_ctx())
    handoff = create_chapter_handoff(db, rev.id, "chain-hash-1", _author_ctx())
    result = get_valid_entry(db, chapter.id, handoff["id"], rev.id, "wrong-hash")
    assert result is None


def test_get_valid_entry_returns_none_for_missing_handoff(db, volume):
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    assert get_valid_entry(db, chapter.id, None, None, None) is None


def test_get_valid_entry_rejects_stale_source_not_current_accepted(db, volume):
    """来源修订已不是当前 accepted 时，视为无效交接。"""
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _author_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _author_ctx())
    from app.domain.chapters import aggregate_chapter_revision, commit_chapter_version
    rev1 = aggregate_chapter_revision(db, chapter.id, [], "r1", _author_ctx())
    commit_chapter_version(db, rev1.id, _author_ctx())
    # 在 rev1 上挂一个 active+in_sync 的 handoff。
    handoff = create_chapter_handoff(db, rev1.id, "chain-hash-1", _author_ctx())
    # 再推进 accepted 指针到 rev2。
    rev2 = aggregate_chapter_revision(db, chapter.id, [], "r2", _author_ctx())
    commit_chapter_version(db, rev2.id, _author_ctx())
    # 旧 handoff 的来源(rev1)不再是当前 accepted，必须返回 None。
    result = get_valid_entry(db, chapter.id, handoff["id"], rev1.id, "chain-hash-1")
    assert result is None


def test_get_valid_entry_rejects_non_accepted_source(db, volume):
    """来源修订未 accepted 时，即使 handoff 状态匹配也视为无效交接。"""
    chapter = create_chapter(db, volume, "Ch1", "pov", {"intent": 1}, _resource_ctx())
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", _author_ctx())
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _author_ctx())
    from app.domain.chapters import aggregate_chapter_revision
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", _author_ctx())
    # rev 未 commit，仍为 staged；handoff 创建会因非 accepted 失败，这里直接构造
    # 一个 active+in_sync 但来源非 accepted 的交接记录验证 get_valid_entry。
    from app.db.models import ChapterHandoff as ChapterHandoffModel
    handoff = ChapterHandoffModel(
        chapter_id=chapter.id,
        source_chapter_revision_id=rev.id,
        entry_handoff_status="in_sync",
        chain_hash="chain-hash-1",
        status="active",
    )
    db.add(handoff)
    db.flush()
    result = get_valid_entry(db, chapter.id, handoff.id, rev.id, "chain-hash-1")
    assert result is None
