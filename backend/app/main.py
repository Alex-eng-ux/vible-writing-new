from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from .config import get_config
from .errors import AppError, ErrorEnvelope, build_envelope

SERVICE_NAME = "novel-studio-api"


def request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid.uuid4())


def _ping_database() -> None:
    """Run a minimal liveness query against the configured database."""
    cfg = get_config()
    engine = create_engine(cfg.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def _health_body(request: Request, status: str) -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "status": status,
        "request_id": request_id(request),
    }


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    envelope: ErrorEnvelope = build_envelope(
        exc.code,
        exc.message,
        run_id=exc.run_id,
        request_id=request_id(request),
        details=exc.details,
    )
    return JSONResponse(status_code=exc.http_status, content=envelope)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    envelope: ErrorEnvelope = build_envelope(
        "VALIDATION_ERROR",
        "request validation failed",
        request_id=request_id(request),
        details={"errors": exc.errors()},
    )
    return JSONResponse(status_code=422, content=envelope)


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    envelope: ErrorEnvelope = build_envelope(
        "INTERNAL_ERROR",
        "internal server error",
        request_id=request_id(request),
    )
    return JSONResponse(status_code=500, content=envelope)


def create_app() -> FastAPI:
    app = FastAPI(title="Continuous Novel Writing Studio", version="0.1.0")
    # Starlette 的 add_exception_handler 在类型上要求 Exception 签名，而业务
    # handler 使用具体异常子类；运行时行为正确，此处仅做类型窄化忽略。
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_error_handler)

    from .api import canon, chapters, projects, runs, scenes, volumes

    # Task 5A 资源与作者版本 API 路由：
    # projects/volumes/chapters/scenes 为资源层级，scenes.commit_router
    # 挂载 /api/changesets 的提交端点，均不调用 LangGraph。
    # Task 5B 追加运行/决策/恢复/SSE 路由（runs），不在 HTTP 请求中执行图。
    # Task 5C 追加 Canon 专用运行入口与决策路由（canon），不调用 WritingAgent。
    app.include_router(projects.router)
    app.include_router(volumes.router)
    app.include_router(chapters.router)
    app.include_router(scenes.router)
    app.include_router(scenes.commit_router)
    app.include_router(runs.router)
    app.include_router(canon.router)

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        # Liveness only: always 200 while the process is running.
        return _health_body(request, "ok")

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        # Readiness: fail-closed when the database dependency is unavailable.
        try:
            _ping_database()
        except Exception:
            return JSONResponse(status_code=503, content=_health_body(request, "unavailable"))
        return JSONResponse(content=_health_body(request, "ready"))

    return app


app = create_app()


def _bind_host() -> str:
    cfg = get_config()
    return "0.0.0.0" if cfg.api_bind_scope == "compose_private" else "127.0.0.1"


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=_bind_host(), port=8000)


if __name__ == "__main__":
    run()
