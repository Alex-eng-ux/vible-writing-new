"""E2E 测试数据库引导（Playwright globalSetup 调用）。

创建/重置独立 E2E 数据库 ``novel_e2e`` 并建表，避免污染开发库与 pytest
测试库。Playwright webServer 启动的后端进程使用该库。
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text

from app.db.models import Base


def reset() -> None:
    """创建（若不存在）并重置 E2E 数据库，然后按模型建表。

    库名从 E2E_DATABASE_URL 解析；管理连接使用 E2E_ADMIN_URL。均为环境
    变量常量，不接收用户输入。
    """
    admin_url = os.environ.get(
        "E2E_ADMIN_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
    )
    db_url = os.environ.get(
        "E2E_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/novel_e2e",
    )
    dbname = db_url.rsplit("/", 1)[-1]
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    engine = create_engine(db_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()


if __name__ == "__main__":
    reset()
