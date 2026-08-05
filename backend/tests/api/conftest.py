from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from app import main

_client = TestClient(main.app, raise_server_exceptions=False)
_counter = itertools.count(1)


@pytest.fixture()
def client() -> TestClient:
    return _client


def _key(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}"


def _create_project(client: TestClient, key: str | None = None) -> dict:
    resp = client.post(
        "/api/projects",
        json={"name": "P", "genre": "g", "target_reader": "r", "default_style": "s"},
        headers={"Idempotency-Key": key or _key("proj")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_volume(client: TestClient, project_id: str) -> dict:
    resp = client.post(
        f"/api/projects/{project_id}/volumes",
        json={"name": "V", "goal": "goal", "mainline": "main", "time_range": "range"},
        headers={"Idempotency-Key": _key("vol")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_chapter(client: TestClient, volume_id: str) -> dict:
    resp = client.post(
        f"/api/volumes/{volume_id}/chapters",
        json={"title": "C", "pov": "p", "chapter_intent": {"text": "intent"}},
        headers={"Idempotency-Key": _key("ch")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_scene(client: TestClient, chapter_id: str) -> dict:
    resp = client.post(
        f"/api/chapters/{chapter_id}/scenes",
        json={"title": "S", "pov": "p", "goal": "g"},
        headers={"Idempotency-Key": _key("sc")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


__all__ = ["client", "_create_project", "_create_volume", "_create_chapter", "_create_scene"]
