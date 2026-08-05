"""Agent hook 模块。

定义 Agent 生命周期与图边界的各类钩子：`CommitGuardHook`（提交守卫）、
`FactExtractionHook`（候选事实归一化）、`SchemaHook`（输出 schema 校验）、
`ErrorHook`（异常映射）。钩子由 `HookRegistry` 注册并按 Agent 类型选择。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.agents.schemas import AgentInputEnvelope, RouterOutcome
from app.domain.commit_guard import CommitGuard
from app.domain.interfaces import LeaseContext, RunWriteFence
from app.errors import AppError


class LifecycleHook(Protocol):
    """Agent 生命周期钩子协议：在 Agent 运行前后被调用。"""

    def before(self, agent_type: str, envelope: AgentInputEnvelope) -> None: ...
    def after(self, agent_type: str, output: BaseModel, outcome: RouterOutcome) -> None: ...


class CommitGuardHook:
    """校验、记录并路由写入；绝不创建正式 ID。

    所有正式提交节点都必须调用 Task 2 的 `CommitGuardPort` 与领域服务。本钩子
    是把 `CommitGuard` 适配到 Agent 图层的一层封装。
    """

    def __init__(self, guard: CommitGuard) -> None:
        """构造 CommitGuardHook。

        参数：
            guard: 底层 `CommitGuard`，负责写入校验与 fencing 检查。
        """
        self._guard = guard

    def validate(
        self,
        operation: str,
        actor_id: str,
        base_revision_id: str | None,
        idempotency_key: str,
        source_refs: list[str],
        write_fence: RunWriteFence | None = None,
        lease_context: LeaseContext | None = None,
    ) -> None:
        """校验并记录一次写入操作。

        参数：
            operation: 操作类型标识。
            actor_id: 执行写入的 actor 标识。
            base_revision_id: 基线修订 ID（可为 None，表示无基线）。
            idempotency_key: 幂等键，用于重复提交去重。
            source_refs: 来源引用列表。
            write_fence: 写入围栏（fencing token），携带 `generation_run_id`，
                用于防止过期 run 覆盖新数据；为 None 时不传 `generation_run_id`。
            lease_context: 租约上下文，用于身份互斥/租约校验。

        副作用：通过底层 `CommitGuard` 进行校验与记录；可能抛出异常表示写入
        被拒绝（如基线过期、fencing 失败）。

        失败条件：依赖 `write_fence` 与 `lease_context` 的防护语义，若 fencing
        或租约校验不通过，`CommitGuard` 内部会抛出对应错误。
        """
        self._guard.validate(
            operation=operation,
            actor_id=actor_id,
            base_revision_id=base_revision_id,
            idempotency_key=idempotency_key,
            source_refs=source_refs,
            generation_run_id=write_fence["generation_run_id"] if write_fence else None,
            manual_command_id=None,
            lease_context=lease_context,
            write_fence=write_fence,
        )


class FactExtractionHook:
    """从 Agent 输出中归一化提取候选事实的唯一入口。"""

    def extract(self, output: BaseModel) -> list[dict]:
        """从 Agent 输出中提取候选事实。

        参数：
            output: Agent 输出的结构化模型。

        返回：候选事实的字典列表；输出不含 `candidate_facts` 或为空时返回
        空列表。
        """
        candidate_facts = getattr(output, "candidate_facts", None)
        if not candidate_facts:
            return []
        return [c.model_dump() for c in candidate_facts]


class SchemaHook:
    """校验模型输出是否符合 schema；在模型返回后执行。"""

    def validate(self, output: BaseModel) -> None:
        """校验输出模型。

        参数：
            output: Agent 输出的结构化模型。

        失败条件：当状态为 `needs_clarification` 但 `clarification_questions`
        为空时抛出 `COMMAND_CONTEXT_MISMATCH` 错误，因为需澄清状态必须携带
        至少一个澄清问题。
        """
        status = getattr(output, "status", None)
        if status == "needs_clarification":
            questions = list(getattr(output, "clarification_questions", []))
            if not questions:
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "needs_clarification requires non-empty clarification_questions",
                )


class ErrorHook:
    """处理技术性失败并将其映射为稳定的运行状态。"""

    def handle(self, error: Exception) -> None:
        """处理节点执行异常。

        参数：
            error: 捕获到的异常。

        失败条件：`AppError` 原样抛出；其他异常统一包装为 `INTERNAL_ERROR`
        `AppError`，以保持下游可识别的稳定错误码。
        """
        if isinstance(error, AppError):
            raise error
        raise AppError("INTERNAL_ERROR", f"agent node failed: {error}")
