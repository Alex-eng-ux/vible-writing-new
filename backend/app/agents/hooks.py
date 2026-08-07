"""Agent hook 模块。

定义 Agent 生命周期与图边界的各类钩子：`CommitGuardHook`（提交守卫）、
`FactExtractionHook`（候选事实归一化）、`SchemaHook`（输出 schema 校验）、
`ErrorHook`（异常映射）。钩子由 `HookRegistry` 注册并按 Agent 类型选择。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.agents.schemas import AgentInputEnvelope, ChapterPlanOutput, RouterOutcome
from app.domain.commit_guard import CommitGuard
from app.domain.interfaces import LeaseContext, RunWriteFence
from app.errors import AppError


class LifecycleHook(Protocol):
    """Agent 生命周期钩子协议：在 Agent 运行前后被调用。"""

    def before(self, agent_type: str, envelope: AgentInputEnvelope) -> None: ...
    def after(self, agent_type: str, output: BaseModel, outcome: RouterOutcome) -> None: ...


class ResultValidationHook(Protocol):
    """路由前结果校验钩子协议。"""

    def validate(
        self, agent_type: str, output: BaseModel, envelope: AgentInputEnvelope
    ) -> None: ...


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


class PlannerDiscussionHook:
    """在 Planner 结果进入路由前校验讨论边界与候选计划结构。

    该钩子只做输入/输出契约校验，不创建计划修订、场景或 Canon；正式 accepted
    写入仍由作者决策事务完成。缺省字段保持兼容，但一旦模型显式提供来源、状态
    或顺序元数据，就必须符合章节规划契约。
    """

    _PROVENANCE_STATUSES = {
        "author_confirmed",
        "ai_suggested",
        "unresolved",
        "explicitly_omitted",
    }
    _PROVENANCE_SOURCES = {"author", "ai", "merged"}

    def validate(
        self, agent_type: str, output: BaseModel, envelope: AgentInputEnvelope
    ) -> None:
        if agent_type != "chapter_planner":
            return
        runtime = envelope.runtime_context
        if runtime.run_scope != "chapter" or runtime.decision_target != "plan":
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "chapter planner result requires a chapter plan run",
            )
        if not isinstance(output, ChapterPlanOutput):
            raise AppError("LLM_RESPONSE_INVALID", "chapter planner output has an invalid schema")
        self._validate_proposals(output.proposals)
        self._validate_provenance(
            output.contract_field_provenance,
            "contract_field_provenance",
        )
        for client_key, provenance in output.scene_field_provenance.items():
            if not isinstance(client_key, str) or not client_key.strip():
                raise AppError(
                    "COMMAND_CONTEXT_MISMATCH",
                    "scene field provenance requires a non-empty client_key",
                )
            self._validate_provenance(provenance, f"scene_field_provenance[{client_key}]")
        self._validate_assumptions(output.unresolved_assumptions)
        self._validate_scene_contracts(output)

    @staticmethod
    def _validate_assumptions(assumptions: object) -> None:
        if not isinstance(assumptions, list) or any(
            not isinstance(item, str) or not item.strip() for item in assumptions
        ):
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "unresolved_assumptions must contain non-empty strings",
            )

    def _validate_proposals(self, proposals: object) -> None:
        if not isinstance(proposals, list):
            raise AppError("COMMAND_CONTEXT_MISMATCH", "planner proposals must be a list")
        seen_ids: set[str] = set()
        for proposal in proposals:
            if not isinstance(proposal, dict):
                raise AppError("COMMAND_CONTEXT_MISMATCH", "planner proposal must be an object")
            field_path = proposal.get("field_path")
            if not isinstance(field_path, str) or not field_path.strip():
                raise AppError("COMMAND_CONTEXT_MISMATCH", "planner proposal requires field_path")
            proposal_id = proposal.get("proposal_id")
            if proposal_id is not None:
                if not isinstance(proposal_id, str) or not proposal_id.strip():
                    raise AppError("COMMAND_CONTEXT_MISMATCH", "planner proposal_id is invalid")
                if proposal_id in seen_ids:
                    raise AppError("PLAN_REVISION_CONFLICT", "planner proposal_id is duplicated")
                seen_ids.add(proposal_id)
            source = proposal.get("source", "ai")
            if source not in self._PROVENANCE_SOURCES:
                raise AppError("COMMAND_CONTEXT_MISMATCH", "planner proposal source is invalid")
            status = proposal.get("status", "pending")
            if status != "pending":
                raise AppError(
                    "PLAN_NOT_ACCEPTED",
                    "planner proposal must remain pending before author confirmation",
                )

    def _validate_provenance(self, provenance: object, field_name: str) -> None:
        if not isinstance(provenance, dict):
            raise AppError("COMMAND_CONTEXT_MISMATCH", f"{field_name} must be an object")
        for path, metadata in provenance.items():
            if not isinstance(path, str) or not path.strip() or not isinstance(metadata, dict):
                raise AppError("COMMAND_CONTEXT_MISMATCH", f"{field_name} contains an invalid field")
            status = metadata.get("status")
            source = metadata.get("source")
            if status is not None and status not in self._PROVENANCE_STATUSES:
                raise AppError("COMMAND_CONTEXT_MISMATCH", f"{field_name} has an invalid status")
            if source is not None and source not in self._PROVENANCE_SOURCES:
                raise AppError("COMMAND_CONTEXT_MISMATCH", f"{field_name} has an invalid source")

    def _validate_scene_contracts(self, output: ChapterPlanOutput) -> None:
        contracts = output.scene_contracts
        if output.status == "ready" and not contracts:
            raise AppError(
                "PLAN_NOT_ACCEPTED",
                "ready planner output requires at least one scene contract",
            )
        seen_keys: set[str] = set()
        for index, scene in enumerate(contracts):
            if not isinstance(scene, dict):
                raise AppError("COMMAND_CONTEXT_MISMATCH", "scene contract must be an object")
            client_key = scene.get("client_key")
            if not isinstance(client_key, str) or not client_key.strip():
                raise AppError("COMMAND_CONTEXT_MISMATCH", "scene contract requires client_key")
            if client_key in seen_keys:
                raise AppError("PLAN_REVISION_CONFLICT", "scene contract client_key is duplicated")
            seen_keys.add(client_key)
            if "order" in scene and scene["order"] != index:
                raise AppError("PLAN_REVISION_CONFLICT", "scene contract order is not contiguous")
            brief = scene.get("scene_brief", scene.get("brief", {}))
            if not isinstance(brief, dict):
                raise AppError("COMMAND_CONTEXT_MISMATCH", "scene contract brief must be an object")


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
