from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main

VALID_ENV = {
    "APP_ENV": "development",
    "DEPLOYMENT_MODE": "single_user_private",
    "API_BIND_SCOPE": "loopback",
    "INTERNAL_API_BASE_URL": "http://127.0.0.1:8000",
    "ACTOR_ID": "test-actor",
    "DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/novel",
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    return TestClient(main.app, raise_server_exceptions=False)


def test_health_returns_200_when_database_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_db_error() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main, "_ping_database", raise_db_error)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "novel-studio-api"
    assert body["status"] == "ok"
    assert body["request_id"]


def test_ready_returns_503_when_database_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_db_error() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main, "_ping_database", raise_db_error)
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["service"] == "novel-studio-api"
    assert body["status"] == "unavailable"
    assert body["request_id"]


def test_ready_returns_200_when_database_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "_ping_database", lambda: None)
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["request_id"]


def test_health_and_ready_do_not_leak_secrets(client: TestClient) -> None:
    resp = client.get("/health")
    assert "llm_api_key" not in resp.text
    assert "LANGSMITH_API_KEY" not in resp.text
    assert "postgres" not in resp.text


def test_health_returns_request_id_echo(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "req-123"})
    assert resp.json()["request_id"] == "req-123"


def test_unhandled_exception_does_not_leak_raw_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("connection refused for postgresql://user:supersecret@db:5432/x")

    original_routes = list(main.app.routes)
    main.app.include_router(router)
    try:
        resp = client.get("/_boom")
    finally:
        main.app.routes[:] = original_routes
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert "supersecret" not in body["message"]
    assert body["message"] == "internal server error"


def test_config_validation_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_config

    monkeypatch.setenv("ACTOR_ID", "")
    with pytest.raises(ValueError):
        get_config()


def test_config_accepts_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_config

    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = get_config()
    assert cfg.deployment_mode == "single_user_private"
    assert cfg.api_bind_scope == "loopback"

    # Ensure later tests are not affected by env leakage.
    for key in VALID_ENV:
        monkeypatch.delenv(key, raising=False)
