"""运行标识（RunIdentity）的规范化与作用域校验。

本模块负责把运行信封中的身份字段归一化为 ``RunIdentity``，并拒绝跨运行的身份
混用。关键约束：
- 区分 generation_run_id、agent_run_id、agent_attempt_key 以及 parent/supersedes
  关系；
- 跨运行的 source_id、local_key 或正式 ID 引用必须被拒绝；
- 父运行/被取代运行不能等于当前运行（自引用视为上下文不匹配）。
"""
from __future__ import annotations

from typing import TypedDict

from app.errors import AppError


class RunIdentity(TypedDict):
    """一次运行的完整身份标识。

    - generation_run_id: 生成运行 ID（顶层运行标识）。
    - agent_run_id: agent 运行 ID。
    - agent_attempt_key: agent 尝试键，用于区分同一运行的不同尝试。
    - parent_generation_run_id: 父生成运行 ID（若存在）。
    - supersedes_run_id: 被取代的运行 ID（若存在）。
    - parent_plan_revision_id: 父计划修订 ID（若存在）。
    """

    generation_run_id: str
    agent_run_id: str
    agent_attempt_key: str
    parent_generation_run_id: str | None
    supersedes_run_id: str | None
    parent_plan_revision_id: str | None


class RunIdentityStep:
    """规范化运行身份并拒绝跨运行身份混用。

    区分 generation_run_id、agent_run_id、agent_attempt_key 及 parent/supersedes
    关系。跨运行的 source_id、local_key 或正式 ID 引用必须被拒绝。
    """

    def normalize(self, state: dict, envelope: dict) -> RunIdentity:
        """从信封中提取并规范化运行身份。

        参数:
            state: 图状态（本方法当前只读取信封，状态保留供扩展）。
            envelope: 输入 agent 的信封，含身份字段。

        返回:
            规范化后的 ``RunIdentity``。

        失败条件:
            - 缺少 generation_run_id/agent_run_id/agent_attempt_key 任一字段时抛出
              ``COMMAND_CONTEXT_MISMATCH``；
            - parent_generation_run_id 或 supersedes_run_id 等于当前
              generation_run_id（自引用）时抛出 ``COMMAND_CONTEXT_MISMATCH``。
        """
        generation_run_id = envelope.get("generation_run_id")
        agent_run_id = envelope.get("agent_run_id")
        agent_attempt_key = envelope.get("agent_attempt_key")
        if not generation_run_id or not agent_run_id or not agent_attempt_key:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "run identity requires generation_run_id, agent_run_id and agent_attempt_key",
            )
        parent = envelope.get("parent_generation_run_id")
        supersedes = envelope.get("supersedes_run_id")
        if parent and parent == generation_run_id:
            raise AppError("COMMAND_CONTEXT_MISMATCH", "parent run cannot equal the current run")
        if supersedes and supersedes == generation_run_id:
            raise AppError("COMMAND_CONTEXT_MISMATCH", "supersedes run cannot equal the current run")
        return RunIdentity(
            generation_run_id=generation_run_id,
            agent_run_id=agent_run_id,
            agent_attempt_key=agent_attempt_key,
            parent_generation_run_id=parent,
            supersedes_run_id=supersedes,
            parent_plan_revision_id=envelope.get("parent_plan_revision_id"),
        )

    def validate_scope(self, run_id: str, owner_run_id: str) -> None:
        """拒绝指向受规管身份 ID 的跨运行引用。

        参数:
            run_id: 被引用的运行 ID。
            owner_run_id: 当前运行（属主）ID。

        失败条件: run_id 与 owner_run_id 不一致时抛出
        ``COMMAND_CONTEXT_MISMATCH``（跨运行身份引用被禁止）。
        """
        if run_id != owner_run_id:
            raise AppError(
                "COMMAND_CONTEXT_MISMATCH",
                "cross-run identity reference is forbidden",
            )
