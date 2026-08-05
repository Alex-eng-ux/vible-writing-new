from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.interfaces import CommandContext
from ..domain.story_bible import upsert_canon_candidates


class FactCandidateService:
    """持久化并更新 FactCandidate / TimelineEventCandidate / PlotThreadUpdate。

    候选保留其来源版本，并按指纹幂等合并；本服务绝不直接写入正式 Canon。
    """

    def __init__(self, session: Session) -> None:
        """初始化事实候选服务。

        参数：session 为数据库会话。
        副作用：持有会话引用，事务边界由调用方管理。
        """
        self._session = session

    def upsert(self, generation_run_id: str, candidates: list[dict], ctx: CommandContext) -> list[dict]:
        """按 (来源, 类型, 指纹) 幂等持久化候选记录并返回投影字典列表。

        参数：generation_run_id 为生成运行 id；candidates 为候选列表；ctx 为
        命令上下文。
        返回：已持久化候选的投影字典列表（可能包含本次新创建或已存在的记录）。
        副作用：由领域函数 upsert_canon_candidates 写入候选；须在已通过 CommitGuard
        的事务内调用。
        失败条件：ctx 中的 generation_run_id 与参数不一致，或候选来源非恰好一个时，
        抛 COMMAND_CONTEXT_MISMATCH。
        幂等约束：已存在相同 (project, scope, source_identity, candidate_type,
        candidate_fingerprint) 的记录时直接返回既有记录，不重复插入。
        """
        return upsert_canon_candidates(self._session, generation_run_id, candidates, ctx)
