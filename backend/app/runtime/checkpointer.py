"""运行时 Postgres checkpoint 基座。

本模块负责管理 langgraph 的 ``PostgresSaver`` 实例及其 psycopg 连接：
- 将 SQLAlchemy 风格的 URL 归一化为 psycopg 可直接使用的格式；
- 提供 ``PostgresCheckpointer`` 封装，持有 saver 与连接，并负责连接释放；
- 提供 ``build_postgres_checkpointer`` / ``setup_checkpoint_tables`` 作为 checkpoint
  表的唯一初始化入口，表由 langgraph 幂等创建（属 langgraph 所有，不经应用
  alembic 管理）。

关键约束：checkpoint 表由 langgraph 自主创建和管理，应用侧 alembic 只负责领域
表；``close()`` 必须释放连接以便跨实例重建图/执行器（checkpoint 恢复场景）。
"""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver


def _normalize_url(database_url: str) -> str:
    """将 SQLAlchemy URL（``postgresql+psycopg://``）转换为 psycopg 格式。

    参数:
        database_url: 形如 ``postgresql+psycopg://...`` 的 SQLAlchemy URL。

    返回:
        形如 ``postgresql://...`` 的 psycopg 兼容 URL；若输入已不是该前缀则原样返回。
    """
    prefix = "postgresql+psycopg://"
    if database_url.startswith(prefix):
        return "postgresql://" + database_url[len(prefix) :]
    return database_url


class PostgresCheckpointer:
    """持有 langgraph ``PostgresSaver`` 及其 psycopg 连接。

    ``PostgresSaver`` 会在 worker 存活期间保持自身连接；``close()`` 释放该连接，
    使图/执行器实例可以被销毁并重建，这正是跨实例恢复（checkpoint 恢复）的场景。
    """

    def __init__(self, saver: PostgresSaver, conn: Any) -> None:
        """初始化封装。

        参数:
            saver: 已绑定连接的 langgraph ``PostgresSaver``。
            conn: 底层 psycopg 连接，供 ``close()`` 释放。
        """
        self._saver = saver
        self._conn = conn

    @property
    def saver(self) -> PostgresSaver:
        """返回底层 ``PostgresSaver``，供图执行时读写 checkpoint。"""
        return self._saver

    def close(self) -> None:
        """关闭底层 psycopg 连接以释放资源。

        副作用：连接关闭后本实例不可再使用；需在重建图/执行器前调用。
        """
        self._conn.close()


def build_postgres_checkpointer(database_url: str) -> PostgresCheckpointer:
    """创建基于 Postgres 的 checkpointer 并初始化其表。

    参数:
        database_url: SQLAlchemy 风格的数据库 URL。

    返回:
        已初始化（表已就绪）的 ``PostgresCheckpointer``。

    约束: checkpoint 表由 ``PostgresSaver.setup()`` 幂等创建，属 langgraph 所有，
    不由应用 alembic 迁移管理；应用 alembic 仅管理领域表（Task 2）。
    """
    from psycopg import Connection
    from psycopg.rows import dict_row

    conn = Connection.connect(
        _normalize_url(database_url),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    saver = PostgresSaver(conn)
    saver.setup()
    return PostgresCheckpointer(saver, conn)


def setup_checkpoint_tables(database_url: str) -> None:
    """幂等创建 checkpoint 表（迁移/初始化边界）。

    参数:
        database_url: SQLAlchemy 风格的数据库 URL。

    副作用: 创建 checkpoint 表；可重复调用，且不会删除或重置已有 checkpoint。
    这是 Postgres checkpointer 的唯一初始化入口。
    """
    build_postgres_checkpointer(database_url).close()
