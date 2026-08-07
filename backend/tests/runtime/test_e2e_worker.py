from __future__ import annotations

import json
from threading import Thread
from urllib.request import urlopen

from sqlalchemy.exc import SQLAlchemyError

from app.e2e_worker import _wait_for_database, create_ready_server


def test_e2e_worker_ready_probe_is_independent_from_api() -> None:
    """E2E Worker 必须提供独立健康探针，不能复用 API 的 8000/ready。"""
    server = create_ready_server("127.0.0.1", 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/ready", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload == {"status": "ready", "service": "chapter-worker"}
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_e2e_worker_waits_for_required_schema_before_starting() -> None:
    """Worker 就绪前必须确认关键表已创建，避免 bootstrap 与首个 tick 竞态。"""
    attempts = 0

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise SQLAlchemyError("undefined table: run_outbox_records")

    _wait_for_database(lambda: Session(), timeout=0.2, interval=0)
    assert attempts == 2
