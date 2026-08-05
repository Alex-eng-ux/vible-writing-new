from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_config

# Engine 依赖当前环境惰性创建，使测试可以在首次使用前通过覆盖 DATABASE_URL
# 指向一次性数据库。
_engine = None
_session_factory: sessionmaker[Session] | None = None


def get_engine():
    """返回进程级 SQLAlchemy engine（首次使用时创建）。

    若 engine 尚未创建，则从当前环境配置读取 database_url 并构造 engine
    （启用 pool_pre_ping 以检测失效连接）。返回进程级缓存的 engine。
    """
    global _engine
    if _engine is None:
        cfg = get_config()
        _engine = create_engine(cfg.database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """返回进程级 sessionmaker 工厂（首次使用时创建）。

    以 get_engine() 为绑定，并设置 expire_on_commit=False（提交后对象属性不会
    过期）。返回进程级缓存的 sessionmaker。
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def session_scope() -> Iterator[Session]:
    """在事务边界内产生一个 Session；成功时提交，异常时回滚。

    领域服务不得自行开启外层事务；API/Worker 调用方通过本辅助函数拥有事务
    生命周期。成功时提交，抛出异常时回滚并重新抛出，finally 中关闭会话。
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


def reset_engine() -> None:
    """丢弃缓存的 engine 与 sessionmaker（供测试重新绑定 DATABASE_URL 使用）。

    副作用：将 _engine 与 _session_factory 置空，使下一次 get_engine()/
    get_session_factory() 基于新环境重新创建。
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None
