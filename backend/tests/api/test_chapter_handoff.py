from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.chapters import aggregate_chapter_revision, commit_chapter_version
from app.domain.handoff import create_chapter_handoff
from app.domain.interfaces import CommandContext

from .conftest import _create_chapter, _create_project, _create_volume


def _command_ctx() -> CommandContext:
    # 测试辅助构造最小命令上下文（author 身份），沿用 e2e_fixtures 的 cast 约定。
    return cast(CommandContext, {
        "actor_id": "test-actor",
        "idempotency_key": "seed-key",
        "author_decision": "accept",
    })


def _seed_accepted_revision_and_handoff(db: Session, chapter_id: str) -> str:
    rev = aggregate_chapter_revision(db, chapter_id, [], "seed", _command_ctx())
    commit_chapter_version(db, rev.id, _command_ctx())
    handoff = create_chapter_handoff(db, rev.id, "chain-hash", _command_ctx())
    db.commit()
    return handoff["id"]


def test_new_chapter_has_no_accepted_pointer(client: TestClient) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])

    resp = client.get(f"/api/chapters/{chapter['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_chapter_revision_id"] is None
    assert body["entry_handoff_id"] is None


def test_chapter_read_returns_accepted_pointer_and_handoff(
    client: TestClient, db: Session
) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    handoff_id = _seed_accepted_revision_and_handoff(db, chapter["id"])

    resp = client.get(f"/api/chapters/{chapter['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_chapter_revision_id"] is not None
    assert body["entry_handoff_id"] == handoff_id


def test_handoff_read_returns_valid_entry(client: TestClient, db: Session) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    handoff_id = _seed_accepted_revision_and_handoff(db, chapter["id"])

    resp = client.get(f"/api/chapters/{chapter['id']}/handoff")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == handoff_id
    assert body["entry_handoff_status"] == "in_sync"
    assert body["status"] == "active"


def test_handoff_read_empty_when_no_handoff(client: TestClient) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])

    resp = client.get(f"/api/chapters/{chapter['id']}/handoff")
    assert resp.status_code == 200
    assert resp.json() is None


def test_handoff_read_requires_source_matches_current_accepted(
    client: TestClient, db: Session
) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    # 先建一个旧 accepted 版本并挂一个 active+in_sync 的 handoff。
    rev1 = aggregate_chapter_revision(db, chapter["id"], [], "seed-1", _command_ctx())
    commit_chapter_version(db, rev1.id, _command_ctx())
    create_chapter_handoff(db, rev1.id, "chain-hash", _command_ctx())
    # 再推进 accepted 指针到 rev2（不重建 handoff）。
    rev2 = aggregate_chapter_revision(db, chapter["id"], [], "seed-2", _command_ctx())
    commit_chapter_version(db, rev2.id, _command_ctx())
    db.commit()

    # 旧 handoff 的 source(rev1) 不再匹配当前 accepted(rev2)，必须返回 None。
    resp = client.get(f"/api/chapters/{chapter['id']}/handoff")
    assert resp.status_code == 200
    assert resp.json() is None

    # 章节读取同样不暴露旧 handoff。
    read = client.get(f"/api/chapters/{chapter['id']}")
    assert read.status_code == 200
    assert read.json()["entry_handoff_id"] is None


def test_chapter_revisions_list_returns_accepted(client: TestClient, db: Session) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    _seed_accepted_revision_and_handoff(db, chapter["id"])

    resp = client.get(f"/api/chapters/{chapter['id']}/revisions")
    assert resp.status_code == 200
    revisions = resp.json()
    assert len(revisions) == 1
    assert revisions[0]["status"] == "accepted"
