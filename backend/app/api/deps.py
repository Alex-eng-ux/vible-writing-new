"""API 依赖注入：事务边界、actor 身份与幂等键提取。

这里集中管理 HTTP 请求依赖的会话生命周期服务端身份解析，实现
“事务边界由 API 拥有、领域服务不自行开启外层事务”的约定。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Header
from sqlalchemy.orm import Session

from ..config import get_config
from ..db.session import get_session_factory
from ..errors import AppError


def get_db() -> Iterator[Session]:
    """在事务边界内产出 Session，成功时提交、异常时回滚。

    领域服务不得自行开启外层事务；API 通过本依赖拥有事务生命周期。
    一旦抛错便回滚整个命令，使校验、业务写入、幂等结果与记录要么全部
    提交、要么全部不提交，避免部分写入。
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_actor_id() -> str:
    """解析单用户私有模式下的配置 actor 身份。

    客户端不能覆盖该身份；actor 一律来自配置，防止请求正文伪造。
    """
    return get_config().actor_id


def get_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """要求每个非 GET 命令携带 Idempotency-Key 头。

    缺失或为空的键返回 COMMAND_CONTEXT_MISMATCH；返回去除首尾空白的键，
    供后续幂等 claim 使用。
    """
    if not idempotency_key or not idempotency_key.strip():
        raise AppError("COMMAND_CONTEXT_MISMATCH", "an Idempotency-Key header is required")
    return idempotency_key.strip()
