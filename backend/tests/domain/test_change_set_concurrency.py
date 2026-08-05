"""Task 5A 复核：首稿创建与提交的并发一致性测试。

验证场景行锁在首稿创建/物化时真正生效：并发拿锁会阻塞，锁释放后已提交
的首稿会使后续首稿创建被拒（SCENE_STATE_INCOMPATIBLE），保证“没有 accepted
版本”的判定在并发下仍成立。
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.domain.change_sets import create_author_change_set, empty_doc_hash
from app.domain.chapters import create_chapter, create_scene
from app.domain.interfaces import ManualChangeSetContext
from tests.conftest import TEST_DATABASE_URL


def _author_ctx(manual_command_id: str) -> ManualChangeSetContext:
    return {
        "generation_run_id": None,
        "write_fence": None,
        "manual_command_id": manual_command_id,
        "source": "author",
        "actor_id": "author-1",
        "idempotency_key": f"key-{manual_command_id}",
        "expected_run_version": None,
    }


def _make_scene(db, volume: str, scene_key: str):
    chapter = create_chapter(
        db, volume, "Ch1", "pov", {"intent": 1},
        {"actor_id": "author-1", "idempotency_key": f"ch-{scene_key}"},
    )
    return create_scene(
        db, chapter.id, "S1", {"pov": "p"},
        {"actor_id": "author-1", "idempotency_key": scene_key},
    )


def test_first_draft_creation_serialized_by_scene_lock(volume, db):
    """用第二个会话先持有场景行锁，验证首稿创建会被锁阻塞（fail-fast 超时）。"""
    scene = _make_scene(db, volume, "sc-key")
    db.commit()

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    holder = factory()
    try:
        # 持有场景行锁，模拟并发的首稿创建者。
        holder.execute(
            text("SELECT id FROM scenes WHERE id = :sid FOR UPDATE"),
            {"sid": scene.id},
        )
        # 让 db 会话在拿到锁后快速失败，而不是无限等待。
        db.execute(text("SET statement_timeout = '500ms'"))
        try:
            create_author_change_set(
                db,
                scene.id,
                None,
                "prosemirror_step",
                [{"op": "insert", "value": "x"}],
                empty_doc_hash(),
                _author_ctx("manual-blocked"),
            )
            db.commit()
            raise AssertionError("expected concurrency block to raise")
        except Exception as exc:  # noqa: BLE001 - 锁阻塞导致超时即视为通过
            msg = str(exc).lower()
            # 场景行锁被占用时语句超时取消（query canceled / 超时 / 锁定关系）。
            assert "querycanceled" in msg or "timeout" in msg or "锁定关系" in msg
    finally:
        holder.rollback()
        holder.close()
        engine.dispose()


def test_second_first_draft_rejected_after_commit(volume, db):
    """已提交首稿后，再创建首稿必须被拒绝（场景已有 accepted 版本）。"""
    scene = _make_scene(db, volume, "sc-key2")
    db.commit()

    from app.domain.change_sets import commit_change_set

    cs, _ = create_author_change_set(
        db,
        scene.id,
        None,
        "prosemirror_step",
        [{"op": "insert", "value": "first"}],
        empty_doc_hash(),
        _author_ctx("manual-first"),
    )
    commit_change_set(db, cs.id, {**_author_ctx("manual-first"), "author_decision": "accept"})
    db.commit()

    from app.errors import AppError

    try:
        create_author_change_set(
            db,
            scene.id,
            None,
            "prosemirror_step",
            [{"op": "insert", "value": "second"}],
            empty_doc_hash(),
            _author_ctx("manual-second"),
        )
        raise AssertionError("expected SCENE_STATE_INCOMPATIBLE")
    except AppError as exc:
        assert exc.code == "SCENE_STATE_INCOMPATIBLE"
