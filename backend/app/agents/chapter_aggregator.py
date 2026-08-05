"""ChapterAggregator 模块。

Task 4B 章节聚合器：按场景 accepted 版本组装 staged 章节版本并计算聚合资格。
边界：不绕过章节同步与 handoff 校验；聚合资格由 `compute_aggregation_eligibility`
判定，只有 eligible 时才能生成 staged 章节版本，只有 committable 时才能提交
accepted 章节版本。聚合/提交失败必须返回稳定阻断码和可见原因。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..domain.chapter_orchestration import (
    ChapterAggregationEligibility,
    _scene_accepted_revisions,
    compute_aggregation_eligibility,
)
from ..domain.chapters import aggregate_chapter_revision, commit_chapter_version
from ..domain.commit_guard import CommitGuard
from ..domain.interfaces import CommandContext
from ..errors import AppError


class ChapterAggregator:
    """按场景 accepted 版本组装 staged 章节版本。

    聚合前先校验聚合资格；资格不满足时抛稳定阻断码，不生成任何版本。
    """

    def __init__(self, session: Session) -> None:
        """构造 ChapterAggregator。

        参数：
            session: 数据库会话，用于章节聚合相关查询与写入。
        """
        self._session = session

    def eligibility(
        self,
        chapter_id: str,
        entry_handoff_id: str | None = None,
        entry_source_chapter_revision_id: str | None = None,
        entry_handoff_chain_hash: str | None = None,
    ) -> ChapterAggregationEligibility:
        """返回章节聚合资格判定结果。

        参数：chapter_id 为章节 id；entry_handoff_id / entry_source_chapter_revision_id
        / entry_handoff_chain_hash 为入口承接凭据（不再接受布尔值）。
        返回：`ChapterAggregationEligibility`。
        """
        return compute_aggregation_eligibility(
            self._session,
            chapter_id,
            entry_handoff_id,
            entry_source_chapter_revision_id,
            entry_handoff_chain_hash,
        )

    def aggregate(
        self,
        chapter_id: str,
        reason: str,
        ctx: CommandContext,
        entry_handoff_id: str | None = None,
        entry_source_chapter_revision_id: str | None = None,
        entry_handoff_chain_hash: str | None = None,
    ) -> str:
        """组装 staged 章节版本并返回其 id。

        参数：chapter_id 为章节 id；reason 为聚合原因；ctx 为命令上下文；
        entry_handoff_id / entry_source_chapter_revision_id / entry_handoff_chain_hash
        为入口承接凭据（不再接受布尔值）。
        返回：新 staged 章节修订 id。

        失败条件：聚合资格不满足时抛对应稳定阻断码（SCENE_NOT_ACCEPTED /
        STALE_ENTRY / CHAPTER_NOT_IN_SYNC）。
        """
        eligibility = compute_aggregation_eligibility(
            self._session,
            chapter_id,
            entry_handoff_id,
            entry_source_chapter_revision_id,
            entry_handoff_chain_hash,
        )
        if not eligibility.eligible:
            raise AppError(eligibility.status, eligibility.reason)
        CommitGuard(self._session).validate(
            "aggregate_chapter",
            ctx["actor_id"],
            ctx.get("base_chapter_revision_id"),
            ctx["idempotency_key"],
            ctx.get("context_source_refs") or [],
            generation_run_id=ctx.get("generation_run_id"),
            manual_command_id=ctx.get("manual_command_id"),
            expected_run_version=ctx.get("expected_run_version"),
            lease_context=ctx.get("lease_context"),
            write_fence=ctx.get("write_fence"),
        )
        accepted = _scene_accepted_revisions(self._session, chapter_id)
        scene_rev_ids = [accepted[sid] for sid in eligibility.scene_ids]
        rev = aggregate_chapter_revision(self._session, chapter_id, scene_rev_ids, reason, ctx)
        return rev.id

    def commit(self, chapter_revision_id: str, ctx: CommandContext) -> str:
        """提交已汇总的章节版本为 accepted。

        参数：chapter_revision_id 为 staged 章节修订 id；ctx 为命令上下文。
        返回：accepted 章节修订 id。

        失败条件：修订不存在或非 staged 时抛 CHAPTER_OUT_OF_SYNC。
        """
        rev = commit_chapter_version(self._session, chapter_revision_id, ctx)
        return rev.id
