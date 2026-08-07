from __future__ import annotations

import json
from threading import Thread
from urllib.request import urlopen

from app.e2e_worker import create_ready_server


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
