from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import _create_chapter, _create_project, _create_volume


def test_chapter_plan_read_alias_remains_available_but_legacy_init_post_is_absent(
    client: TestClient,
) -> None:
    """迁移完成后保留只读计划查询，删除旧的计划初始化命令。"""
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])

    read_response = client.get(f"/api/chapters/{chapter['id']}/plan")
    assert read_response.status_code == 200
    assert read_response.json()["plan_revision_id"] is None

    post_response = client.post(
        f"/api/chapters/{chapter['id']}/plan",
        headers={"Idempotency-Key": "plan-init-removed"},
    )
    assert post_response.status_code == 405
