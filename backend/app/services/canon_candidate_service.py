from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import CanonDecisionRecord
from ..domain.interfaces import CommandContext
from ..domain.story_bible import apply_canon_decisions, confirm_canon_decisions


class CanonCandidateService:
    """提供 Canon 决策事务端口、候选锁定与正式 Canon 更新路由。

    Task 4C 边界：本服务是 Canon 分支提交节点的领域入口，负责锁定候选、
    校验来源版本/作用域并在章节级 confirm 时生成正式 `CanonFact`。它绝不
    作为 Agent 或普通正文节点的公共写入口，正式写入必须已通过 CommitGuard。
    """

    def __init__(self, session: Session) -> None:
        """初始化 Canon 决策服务。

        参数：session 为数据库会话。
        副作用：持有会话引用，事务边界由调用方管理。
        """
        self._session = session

    def apply_decisions(self, candidate_decisions: list[dict], ctx: CommandContext) -> list[CanonDecisionRecord]:
        """针对已锁定的候选记录应用 Canon 决策并返回新建的决策记录。

        参数：candidate_decisions 为决策列表；ctx 为命令上下文（actor_id 用于
        记录操作者）。
        返回：新建的 CanonDecisionRecord 列表。
        副作用：由领域函数 apply_canon_decisions 以 FOR UPDATE 锁定候选、更新
        候选状态并新增、flush 决策记录；须在已通过 CommitGuard 的事务内调用。
        失败条件：候选不存在、已决策或状态为 discarded 时抛
        SCENE_STATE_INCOMPATIBLE；拒绝重复/已丢弃的候选。
        """
        return apply_canon_decisions(self._session, candidate_decisions, ctx)

    def confirm_decisions(
        self,
        generation_run_id: str,
        candidate_decisions: list[dict],
        ctx: CommandContext,
        *,
        canon_scope: str,
        chapter_id: str | None = None,
        scene_id: str | None = None,
    ) -> list[CanonDecisionRecord]:
        """作者逐条确认/拒绝/暂缓候选的正式 Canon 更新路由。

        参数：generation_run_id 为 Canon 运行 id；candidate_decisions 为决策
        列表；ctx 为命令上下文；canon_scope 为 chapter|scene；chapter_id /
        scene_id 为对应作用域目标。
        返回：本次新写入的 CanonDecisionRecord 列表；幂等命中返回空列表。

        规则：场景级只保存作用域记录、不更新全局 Canon；章节级 confirm 在
        同一事务内生成正式 CanonFact；来源版本/作用域/幂等均由领域函数
        confirm_canon_decisions 校验。调用方必须先通过 CommitGuard。
        """
        return confirm_canon_decisions(
            self._session,
            generation_run_id,
            candidate_decisions,
            ctx,
            canon_scope=canon_scope,
            chapter_id=chapter_id,
            scene_id=scene_id,
        )
