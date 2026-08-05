"""Task 9 权威/审计哈希测试。

覆盖：双哈希确定性、数据变化引起哈希变化、派生/临时表不影响哈希、
fixture 哈希稳定、authority 与 audit 覆盖不同表集合。
"""
from __future__ import annotations

import json

from sqlalchemy import text

from app.acceptance.hashes import (
    AUDIT_TABLES,
    AUTHORITY_TABLES,
    EXCLUDED_TABLES,
    compute_fixture_hash,
    snapshot_hashes,
)
from app.db.models import SceneRevision
from app.domain.chapters import create_chapter, create_scene
from app.domain.resources import create_project, create_volume

_CTX = {"actor_id": "author-1", "idempotency_key": "hash-fixture"}


def _hierarchy(db):
    """创建项目/卷/章/场景层级（经领域服务，供哈希变化测试）。"""
    project = create_project(db, "P", "g", "r", "s", _CTX)
    volume = create_volume(db, project.id, "V", "g", "m", "r", _CTX)
    chapter = create_chapter(db, volume.id, "C", "p", {"text": ""}, _CTX)
    scene = create_scene(db, chapter.id, "S", {"goal": "x"}, _CTX)
    db.flush()
    return project, scene


def test_hashes_are_deterministic(db) -> None:
    """同一数据库两次计算哈希完全一致（稳定排序与规范化）。"""
    first = snapshot_hashes(db)
    second = snapshot_hashes(db)
    assert first == second
    assert len(first["authority_hash"]) == 64
    assert len(first["audit_hash"]) == 64


def test_authority_and_audit_hashes_change_on_authoritative_write(db) -> None:
    """权威写入（场景版本）改变 authority 哈希；未触审计表时 audit 哈希不变。"""
    _, scene = _hierarchy(db)
    before = snapshot_hashes(db)
    db.add(
        SceneRevision(
            scene_id=scene.id,
            content="{}",
            content_hash="abc",
            reason="hash fixture",
            source_ref="src",
            status="accepted",
        )
    )
    db.flush()
    after = snapshot_hashes(db)
    assert after["authority_hash"] != before["authority_hash"]
    # 场景版本属权威表，不属于审计表集合 → audit 哈希不变。
    assert after["audit_hash"] == before["audit_hash"]


def test_audit_only_tables_change_only_audit_hash(db) -> None:
    """只写审计表（RunEvent）时 authority 哈希不变、audit 哈希变化。"""
    project, _ = _hierarchy(db)
    db.execute(
        text(
            "INSERT INTO generation_runs (id, project_id, status, run_version, write_fencing_token, created_at, updated_at) "
            "VALUES ('g-h-1', :pid, 'running', 1, 1, now(), now())"
        ),
        {"pid": project.id},
    )
    db.flush()
    before = snapshot_hashes(db)
    db.execute(
        text(
            "INSERT INTO run_events (event_id, generation_run_id, sequence, event_type, payload, created_at) "
            "VALUES ('ev-h-1', 'g-h-1', 1, 'run_queued', '{}', now())"
        )
    )
    db.flush()
    after = snapshot_hashes(db)
    assert after["audit_hash"] != before["audit_hash"]
    assert after["authority_hash"] == before["authority_hash"]


def test_excluded_temp_tables_do_not_affect_hashes(db) -> None:
    """临时/派生数据（outbox、幂等记录、租约）不进入两种哈希。"""
    project, _ = _hierarchy(db)
    db.flush()
    before = snapshot_hashes(db)
    db.execute(
        text(
            "INSERT INTO run_outbox_records (outbox_id, resource_type, resource_id, payload_schema, payload, "
            "delivery_status, attempt_count, producer_command_id, created_at) "
            "VALUES ('o-h-1', 'run', 'r', 'run-event.v1', '{}', 'pending', 0, 'cmd', now())"
        )
    )
    db.flush()
    after = snapshot_hashes(db)
    assert after == before


def test_hash_tables_are_disjoint_and_cover_spec(db) -> None:
    """authority 与 audit 表集合互斥，且明确排除派生/临时表。"""
    assert set(AUTHORITY_TABLES).isdisjoint(set(AUDIT_TABLES))
    assert set(AUTHORITY_TABLES).isdisjoint(set(EXCLUDED_TABLES))
    assert set(AUDIT_TABLES).isdisjoint(set(EXCLUDED_TABLES))
    # 关键表归属符合规范。
    assert "scene_revisions" in AUTHORITY_TABLES
    assert "canon_facts" in AUTHORITY_TABLES
    assert "run_events" in AUDIT_TABLES
    assert "run_decisions" in AUDIT_TABLES


def test_fixture_hash_is_stable() -> None:
    """clean fixture 哈希只依赖 fixture 内容，不依赖随机正式 ID。"""
    fixture = {"project": {"name": "P"}, "scenes": [{"local_key": "s1"}]}
    assert compute_fixture_hash(fixture) == compute_fixture_hash(json.loads(json.dumps(fixture)))
    assert compute_fixture_hash(fixture) != compute_fixture_hash(
        {"project": {"name": "P"}, "scenes": [{"local_key": "s2"}]}
    )
