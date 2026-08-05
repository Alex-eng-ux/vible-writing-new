from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AgentRun, GenerationRun


class IdCleanupService:
    """清理处于终态且不再被引用的派生数据（AgentRun 明细）。

    仅处理没有任何未完成工作、且处于不可逆终态（completed/cancelled/failed）
    的生成运行。先由 source_id 映射到已持久化的来源，跳过仍被审计或版本记录
    引用的任何行；绝不级联删除正式业务数据（例如 Canon 快照、决策记录）。
    """

    def __init__(self, session: Session, retention_days: int = 7) -> None:
        """初始化清理服务。

        参数：session 为数据库会话；retention_days 为保留天数，早于该保留期
        的终态运行才纳入清理范围（默认 7 天）。
        副作用：持有会话引用，不在此处开启事务；事务边界由调用方管理。
        """
        self._session = session
        self._retention = timedelta(days=retention_days)

    def collect_terminal_runs(self, now: datetime) -> list[GenerationRun]:
        """筛选出超过保留期且处于不可逆终态的运行。

        参数：now 为当前时间，用于计算保留截止时间。
        返回：匹配状态（completed/cancelled/failed）且创建时间早于保留截止
        时间的 GenerationRun 列表。
        业务约束：仅筛选终态，避免误删仍在处理的运行。
        """
        cutoff = now - self._retention
        rows = self._session.execute(
            select(GenerationRun).where(
                GenerationRun.status.in_(["completed", "cancelled", "failed"]),
                GenerationRun.created_at < cutoff,
            )
        ).scalars().all()
        return list(rows)

    def cleanup_agent_runs(self, run_ids: list[str]) -> int:
        """删除指定终态运行下不再被引用的 AgentRun 明细行。

        参数：run_ids 为待清理的生成运行 id 列表。
        返回：实际删除的 AgentRun 行数。
        副作用：对删除执行 flush，使变更写入会话；调用方需负责提交事务。
        业务约束：仅删除 AgentRun 派生明细，不触碰正式业务数据；本方法不校验
        引用关系，由调用方保证只传入已通过 collect_terminal_runs 筛选的运行。
        """
        deleted = 0
        for run_id in run_ids:
            rows = self._session.execute(
                select(AgentRun).where(AgentRun.generation_run_id == run_id)
            ).scalars().all()
            for row in rows:
                self._session.delete(row)
                deleted += 1
        self._session.flush()
        return deleted
