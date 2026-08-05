from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import _create_chapter, _create_project, _create_scene, _create_volume


def test_create_full_hierarchy(client: TestClient) -> None:
    project = _create_project(client)
    assert project["type"] == "project"
    assert project["parent_id"] is None
    assert project["version"] == 1

    volume = _create_volume(client, project["id"])
    assert volume["type"] == "volume"
    assert volume["parent_id"] == project["id"]

    chapter = _create_chapter(client, volume["id"])
    assert chapter["type"] == "chapter"
    assert chapter["parent_id"] == volume["id"]

    scene = _create_scene(client, chapter["id"])
    assert scene["type"] == "scene"
    assert scene["parent_id"] == chapter["id"]


def test_get_project_and_list_volumes(client: TestClient) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])

    resp = client.get(f"/api/projects/{project['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "P"

    resp = client.get(f"/api/projects/{project['id']}/volumes")
    assert resp.status_code == 200
    assert [v["id"] for v in resp.json()] == [volume["id"]]


def test_list_chapters_and_scenes(client: TestClient) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    scene = _create_scene(client, chapter["id"])

    resp = client.get(f"/api/volumes/{volume['id']}/chapters")
    assert resp.status_code == 200
    assert [c["id"] for c in resp.json()] == [chapter["id"]]

    resp = client.get(f"/api/chapters/{chapter['id']}/scenes")
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()] == [scene["id"]]


def test_idempotent_creation_replays_same_result(client: TestClient) -> None:
    body = {"name": "P", "genre": "g", "target_reader": "r", "default_style": "s"}
    first = client.post(
        "/api/projects", json=body, headers={"Idempotency-Key": "dup-key"}
    )
    second = client.post(
        "/api/projects", json=body, headers={"Idempotency-Key": "dup-key"}
    )
    assert first.status_code == 201 and second.status_code == 201
    assert first.json() == second.json()


def test_idempotency_key_reuse_returns_conflict(client: TestClient) -> None:
    first = client.post(
        "/api/projects",
        json={"name": "P", "genre": "g", "target_reader": "r", "default_style": "s"},
        headers={"Idempotency-Key": "reuse-key"},
    )
    assert first.status_code == 201
    reuse = client.post(
        "/api/projects",
        json={"name": "DIFFERENT", "genre": "g", "target_reader": "r", "default_style": "s"},
        headers={"Idempotency-Key": "reuse-key"},
    )
    assert reuse.status_code == 409
    assert reuse.json()["code"] == "IDEMPOTENCY_KEY_REUSE"


def test_missing_idempotency_key_is_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/projects",
        json={"name": "P", "genre": "g", "target_reader": "r", "default_style": "s"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "COMMAND_CONTEXT_MISMATCH"


def test_missing_parent_returns_envelope_with_null_run_id(client: TestClient) -> None:
    resp = client.post(
        "/api/projects/does-not-exist/volumes",
        json={"name": "V", "goal": "g", "mainline": "m", "time_range": "r"},
        headers={"Idempotency-Key": "k"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "CONTEXT_SOURCE_UNAVAILABLE"
    assert body["run_id"] is None


def test_get_missing_project_returns_null_run_id(client: TestClient) -> None:
    resp = client.get("/api/projects/nope")
    assert resp.status_code == 404
    assert resp.json()["run_id"] is None
