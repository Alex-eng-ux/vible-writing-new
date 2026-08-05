from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import _create_chapter, _create_project, _create_scene, _create_volume


def test_plan_init_creates_and_accepts_plan(client: TestClient) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    scene = _create_scene(client, chapter["id"])

    # 初始无 accepted plan。
    resp = client.get(f"/api/chapters/{chapter['id']}/plan")
    assert resp.status_code == 200
    assert resp.json()["plan_revision_id"] is None

    # 初始化并接受计划。
    resp = client.post(
        f"/api/chapters/{chapter['id']}/plan",
        headers={"Idempotency-Key": "plan-init-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_revision_id"] is not None
    assert body["plan_status"] == "accepted"
    assert body["plan_version"] == 1
    assert body["chapter_contract"]["outline"] == "init-plan"
    assert body["chapter_contract"]["scenes"][0]["scene_id"] == scene["id"]

    # 幂等重放：同键返回同一 plan id。
    resp = client.post(
        f"/api/chapters/{chapter['id']}/plan",
        headers={"Idempotency-Key": "plan-init-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["plan_revision_id"] == body["plan_revision_id"]

    # 现有场景被映射进计划（scene_id 复用，不重复建场景）。
    resp = client.get(f"/api/chapters/{chapter['id']}/scenes")
    assert resp.status_code == 200
    scenes = resp.json()
    assert [s["id"] for s in scenes] == [scene["id"]]


def test_plan_init_is_idempotent_across_keys(client: TestClient) -> None:
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])

    resp = client.post(
        f"/api/chapters/{chapter['id']}/plan",
        headers={"Idempotency-Key": "plan-init-a"},
    )
    assert resp.status_code == 200, resp.text
    first_id = resp.json()["plan_revision_id"]

    # 新键再次调用：已存在 accepted plan，直接返回当前指针。
    resp = client.post(
        f"/api/chapters/{chapter['id']}/plan",
        headers={"Idempotency-Key": "plan-init-b"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan_revision_id"] == first_id


def test_plan_init_missing_chapter_404(client: TestClient) -> None:
    resp = client.post(
        "/api/chapters/does-not-exist/plan",
        headers={"Idempotency-Key": "plan-init-x"},
    )
    assert resp.status_code in (404, 409, 422)
