from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import _create_chapter, _create_project, _create_volume


def test_new_chapter_run_requires_and_persists_natural_language_intent(client: TestClient):
    project = _create_project(client)
    volume = _create_volume(client, project["id"])
    chapter = _create_chapter(client, volume["id"])
    response = client.post(
        f"/api/chapters/{chapter['id']}/runs",
        json={
            "run_scope": "chapter",
            "request_type": "new_chapter",
            "decision_target": "plan",
            "chapter_intent": {"text": "主角在暴雨夜发现一条关键线索"},
        },
        headers={"Idempotency-Key": "intent-run-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"

    workflow = client.get(f"/api/chapters/{chapter['id']}/workflow")
    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["chapter_id"] == chapter["id"]
    assert workflow.json()["phase"] in {"planning", "plan_feedback", "blocked"}
    assert workflow.json()["intent"]["text"] == "主角在暴雨夜发现一条关键线索"
