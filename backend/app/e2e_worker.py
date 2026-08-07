"""章节工作台 Playwright 专用 Worker 启动器。

Playwright 的 ``webServer`` 需要一个可探测的 URL。生产 Worker 本身只消费队列，
没有 HTTP 端口；本模块为测试进程增加独立的 ``/ready`` 探针，同时复用生产
``RunWorker`` 装配，避免把 API 的 8000 端口误当成 Worker 已启动。
"""

from __future__ import annotations

import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import get_config
from .db.session import get_session_factory
from .observability.wiring import make_wiring
from .runtime.run_worker import RunWorker
from .worker_main import _build_provider

logger = logging.getLogger("novel-studio.e2e-worker")

_DEFAULT_READY_HOST: Final[str] = "127.0.0.1"
_DEFAULT_READY_PORT: Final[int] = 8001
_SCHEMA_READY_TIMEOUT_SECONDS: Final[float] = 60.0
_SCHEMA_READY_POLL_SECONDS: Final[float] = 0.25


def _wait_for_database(
    session_factory,
    *,
    timeout: float = _SCHEMA_READY_TIMEOUT_SECONDS,
    interval: float = _SCHEMA_READY_POLL_SECONDS,
) -> None:
    """等待 E2E 数据库的运行时关键表可读，再启动 Worker 轮询线程。

    globalSetup 会重建数据库，Playwright 随后并行启动 API 与 Worker；Worker 若先执行
    首个 tick，会在表尚未建好时记录 ``UndefinedTable`` 并进入长轮询，导致就绪探针
    虚假通过。这里探测 outbox 表作为 schema 完成标志，避免把短暂的建库竞态暴露给测试。
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1 FROM run_outbox_records LIMIT 1"))
            return
        except SQLAlchemyError as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError("E2E database schema did not become ready") from exc
            time.sleep(max(interval, 0.0))
        except Exception as exc:
            # Fake/test session factories may surface the same transient startup failure
            # without SQLAlchemy wrapping it; keep the worker fail-closed in that case too.
            if time.monotonic() >= deadline:
                raise RuntimeError("E2E database schema did not become ready") from exc
            time.sleep(max(interval, 0.0))


def _env_flag(name: str, default: bool = False) -> bool:
    """读取测试 Worker 的布尔开关；非法值按默认值处理。"""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _ReadyHandler(BaseHTTPRequestHandler):
    """返回 Worker 健康状态的极小 HTTP handler。"""

    server_version = "NovelStudioE2EWorker/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定方法名
        if self.path.split("?", 1)[0] != "/ready":
            self.send_error(404)
            return
        body = json.dumps(
            {"status": "ready", "service": "chapter-worker"},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """测试探针请求不写标准错误，避免污染 Playwright webServer 日志。"""


def create_ready_server(host: str, port: int) -> ThreadingHTTPServer:
    """创建独立 Worker 健康探针服务器；``port=0`` 可供单元测试分配临时端口。"""

    return ThreadingHTTPServer((host, port), _ReadyHandler)


def _ready_port() -> int:
    """读取 E2E Worker 探针端口，非法配置时 fail-closed。"""

    raw = os.getenv("E2E_WORKER_READY_PORT", str(_DEFAULT_READY_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("E2E_WORKER_READY_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("E2E_WORKER_READY_PORT must be between 1 and 65535")
    return port


def main() -> None:
    """启动确定性 E2E Worker，并在独立端口提供 Playwright 就绪探针。"""

    cfg = get_config()
    wiring = make_wiring(cfg)
    provider = _build_provider(cfg, wiring)
    _wait_for_database(get_session_factory())
    worker = RunWorker(
        get_session_factory(),
        actor_id=cfg.actor_id,
        observability=wiring,
        provider=provider,
        auto_plan_execution=_env_flag("E2E_WORKER_AUTO_PLAN_EXECUTION"),
        process_queued_runs=_env_flag("E2E_WORKER_PROCESS_QUEUED_RUNS"),
    )
    worker_thread = Thread(
        target=worker.run_forever,
        kwargs={"interval": 1.0},
        name="e2e-run-worker",
        daemon=True,
    )
    worker_thread.start()

    server = create_ready_server(_DEFAULT_READY_HOST, _ready_port())
    logger.info("e2e_worker_ready", extra={"port": server.server_address[1]})
    try:
        server.serve_forever()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
