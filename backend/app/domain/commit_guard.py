"""权威写入前的提交守卫：身份、操作者、基线、幂等与 fencing 校验。

这是领域层对“权威写入”的唯一咽喉点。所有触碰版本、草稿、ChangeSet、
回滚或 Canon 领域对象的写事务在落库前都必须先调用本模块的校验逻辑。
本模块本身从不创建任何版本或领域对象，只负责校验；校验失败时抛出
AppError，调用方不得继续写库。
"""

from __future__ import annotations

from typing import cast

from sqlalchemy.orm import Session

from ..errors import AppError
from .interfaces import (
    ChangeSetCommandContext,
    LeaseContext,
    ManualChangeSetContext,
    RunWriteFence,
)
from .lease import validate_lease, validate_write_fence


class CommitGuard:
    """在写入前校验身份、操作者、基线、幂等性与 fencing。

    这是权威写入的唯一咽喉点。任何版本/草稿/ChangeSet/回滚/Canon 事务在
    触碰数据库之前都必须调用本类。它自身从不创建版本。
    """

    def __init__(self, session: Session) -> None:
        """用当前会话构造守卫；后续校验均基于该会话读取领域状态。"""
        self._session = session

    def validate(
        self,
        operation: str,
        actor_id: str,
        base_revision_id: str | None,
        idempotency_key: str,
        source_refs: list[str],
        generation_run_id: str | None = None,
        manual_command_id: str | None = None,
        expected_run_version: int | None = None,
        operation_format: str | None = None,
        base_content_hash: str | None = None,
        lease_context: LeaseContext | None = None,
        write_fence: RunWriteFence | None = None,
    ) -> None:
        """校验一次权威写入的上下文合法性。

        参数：
            operation: 操作类型；仅 ``resource_create`` 允许不携带任何运行身份。
            actor_id: 操作者身份，必须非空，否则抛 ACTOR_OVERRIDE_FORBIDDEN。
            base_revision_id: 本次写入所依据的基线修订 id（本方法仅透传，不单独校验）。
            idempotency_key: 幂等键，必须非空，否则抛 COMMAND_CONTEXT_MISMATCH。
            source_refs: 来源引用列表（本方法不直接使用）。
            generation_run_id: 生成运行 id，与 manual_command_id 互斥。
            manual_command_id: 手动命令 id，与 generation_run_id 互斥。
            expected_run_version: 期望的运行版本号（本方法不直接使用）。
            operation_format: 操作格式（本方法不直接使用）。
            base_content_hash: 基线内容哈希（本方法不直接使用）。
            lease_context: 工作进程租约；若提供则必须携带 generation_run_id。
            write_fence: 写入 fencing；若提供则校验其与运行的所有者/令牌一致。

        失败条件（均抛 AppError）：
            - actor_id 为空：ACTOR_OVERRIDE_FORBIDDEN。
            - idempotency_key 为空：COMMAND_CONTEXT_MISMATCH。
            - 身份互斥：generation_run_id 与 manual_command_id 同时存在或二者
              皆缺（且非 resource_create）时，COMMAND_CONTEXT_MISMATCH。
            - write_fence 存在但校验失败：RUN_LEASE_LOST / RUN_STATE_CONFLICT。
            - lease_context 存在但未携带 generation_run_id：COMMAND_CONTEXT_MISMATCH；
              租约校验失败：RUN_LEASE_LOST / RUN_STATE_CONFLICT。

        副作用：可能读取 GenerationRun 以校验租约与 fencing，不写库。
        """
        if not actor_id:
            raise AppError("ACTOR_OVERRIDE_FORBIDDEN", "actor identity must be non-empty")
        if not idempotency_key:
            raise AppError("COMMAND_CONTEXT_MISMATCH", "idempotency key is required")
        # Identity exclusivity: run id and manual command id must not both be set.
        if generation_run_id is not None and manual_command_id is not None:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "generation_run_id and manual_command_id cannot both be present",
            )
        if generation_run_id is None and manual_command_id is None:
            # A pure resource-rooted write may pass neither only when no run
            # identity is involved; otherwise reject.
            if operation not in ("resource_create",):
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "a run identity or manual command identity is required",
                )
        # Fencing: if a write fence is supplied, validate it against the run.
        if write_fence is not None:
            validate_write_fence(self._session, write_fence)
        if lease_context is not None:
            if generation_run_id is None:
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "a worker lease requires a generation_run_id",
                )
            validate_lease(self._session, lease_context, generation_run_id)

    def validate_change_set_context(
        self,
        ctx: ChangeSetCommandContext,
        operation_format: str,
        base_content_hash: str,
    ) -> None:
        """校验 ChangeSet 上下文，遵循身份互斥规则。

        参数：
            ctx: ChangeSet 命令上下文，source 必须为 author/agent/review 之一。
            operation_format: 操作格式。
            base_content_hash: 基线内容哈希。

        失败条件（均抛 AppError，错误码 COMMAND_CONTEXT_MISMATCH /
        RUN_LEASE_LOST / RUN_STATE_CONFLICT）：
            - author：不得携带 generation_run_id/agent_run_id/lease_context，必须
              提供 manual_command_id，且格式必须为 prosemirror_step。
            - agent/review：必须携带 generation_run_id 与 agent_run_id，不得携带
              manual_command_id，且必须提供 worker lease 并通过租约校验。
            - 其他 source：COMMAND_CONTEXT_MISMATCH。

        副作用：可能读取 GenerationRun 以校验租约，不写库。
        """
        if ctx.get("source") == "author":
            if ctx.get("generation_run_id") is not None or ctx.get("agent_run_id") is not None:
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "author commands must not carry a run identity",
                )
            if not ctx.get("manual_command_id"):
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "author change sets require a manual_command_id",
                )
            if ctx.get("lease_context") is not None:
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "author commands must not carry a worker lease",
                )
            if operation_format != "prosemirror_step":
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "author change sets require prosemirror_step format",
                )
        elif ctx.get("source") in ("agent", "review"):
            if not ctx.get("generation_run_id") or not ctx.get("agent_run_id"):
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "agent/review change sets require run identity",
                )
            if ctx.get("manual_command_id") is not None:
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "agent/review change sets must not carry a manual_command_id",
                )
            if ctx.get("lease_context") is None:
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "agent/review change sets require a worker lease",
                )
            lease = cast(LeaseContext, ctx.get("lease_context"))
            if lease is None:
                raise AppError("RUN_LEASE_LOST", "lease context is required")
            validate_lease(
                self._session,
                lease,
                ctx.get("generation_run_id") or "",
            )
        else:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "change set source must be author, agent or review",
            )

    def validate_manual_change_set_context(
        self,
        ctx: ManualChangeSetContext,
        operation_format: str,
        base_content_hash: str,
    ) -> None:
        """校验手动（作者）ChangeSet 上下文。

        将 ManualChangeSetContext 规约为 author 来源的 ChangeSet 上下文后，
        委托给 validate_change_set_context 执行完整校验。作者命令必须携带
        manual_command_id，不得携带任何运行身份或 worker lease。

        失败条件：与 validate_change_set_context 一致，抛 COMMAND_CONTEXT_MISMATCH
        / RUN_LEASE_LOST / RUN_STATE_CONFLICT。
        """
        manual: ManualChangeSetContext = {
            "generation_run_id": None,
            "write_fence": None,
            "manual_command_id": ctx["manual_command_id"],
            "source": "author",
            "actor_id": ctx["actor_id"],
            "idempotency_key": ctx["idempotency_key"],
            "expected_run_version": None,
        }
        self.validate_change_set_context(manual, operation_format, base_content_hash)
