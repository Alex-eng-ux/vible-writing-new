"""RevisionAgent 模块。

负责根据作者反馈与评审问题生成 ChangeSet（`RevisionOutput`，含 `TextOperation`
列表）。边界：只有存在合法 `base_scene_revision_id` 时才运行；首稿反馈必须
回到 WritingAgent，绝不生成无基线的 ChangeSet。

Task 9 真实 Agent 接线（additive）：
- 构造时可注入统一 `ModelProvider`（真实实现见 ``model_provider.py``）；注入后
  经 provider 调用真实模型并严格以 `RevisionOutput` schema 校验响应；
- 未注入 provider 时保持原占位/fake 行为（确定性输出，默认测试不访问网络）；
- provider 失败（超时/401/限流/非 JSON/结构化输出失败）统一映射为
  ``AppError(LLM_*)`` 上抛，由运行层把运行置为 failed；绝不产生版本/候选/Canon。
"""

from __future__ import annotations

from pydantic import ValidationError

from app.agents.model_provider import ModelProvider
from app.agents.prompts import REVISION_SYSTEM_PROMPT
from app.agents.schemas import AgentInputEnvelope, RevisionOutput, TextOperation
from app.errors import AppError

# 送入真实模型的原稿正文上限（防止 Prompt 过大；超出仅截断，不拼接）。
_ACCEPTED_TEXT_LIMIT = 4000


class RevisionAgent:
    """根据作者反馈与评审问题生成 ChangeSet。

    仅在存在合法 `base_scene_revision_id` 时运行；首稿反馈必须回到
    WritingAgent，绝不生成无基线的 ChangeSet。
    """

    def __init__(self, provider: ModelProvider | None = None) -> None:
        """构造 RevisionAgent。

        参数：
            provider: 统一模型 Provider。为 None 时使用确定性占位实现
                （Fake model 语义，默认测试不访问网络）；注入真实 Provider 时
                经其调用真实模型并校验 `RevisionOutput`。
        """
        self._provider = provider

    def run(self, envelope: AgentInputEnvelope) -> RevisionOutput:
        """基于作者反馈与评审问题生成修订 ChangeSet。

        参数：
            envelope: 输入信封，需包含合法 `base_scene_revision_id` 以及作者
                反馈（`author_feedback`）。

        返回：`RevisionOutput`。当缺少合法基线或缺少作者反馈/评审问题时返回
        `needs_clarification` 状态并附带澄清问题（不调用模型）；否则调用 provider
        校验并返回结构化结果；未注入 provider 时返回 `ready` 状态与语义化文本
        操作列表。

        关键约束：绝不生成无基线的 ChangeSet，确保每次修订都挂在已确认基线之上。
        失败条件：真实 Provider 调用失败抛 ``AppError(LLM_*)``；模型响应无法
        通过 `RevisionOutput` 校验时抛 ``AppError(LLM_RESPONSE_INVALID)``。
        """
        if not envelope.base_scene_revision_id:
            return RevisionOutput(
                status="needs_clarification",
                clarification_questions=["no valid base_scene_revision_id for a ChangeSet"],
            )
        if not envelope.author_feedback.text and not envelope.author_feedback.operations:
            return RevisionOutput(
                status="needs_clarification",
                clarification_questions=["no author feedback or review issue to apply"],
            )
        if self._provider is not None:
            return self._run_with_provider(envelope)
        return self._run_fake(envelope)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _run_with_provider(self, envelope: AgentInputEnvelope) -> RevisionOutput:
        """经真实 Provider 生成修订 ChangeSet 并严格校验 `RevisionOutput`。

        失败条件：provider 错误原样上抛；响应无法通过 schema 校验时包装为
        ``LLM_RESPONSE_INVALID``，绝不把非法输出当作可提交结果。
        """
        provider = self._provider
        if provider is None:
            raise AppError("RUN_STATE_CONFLICT", "revision agent has no model provider")
        rc = envelope.runtime_context
        raw = provider.invoke_structured(
            prompt=self._build_prompt(envelope),
            generation_run_id=rc.generation_run_id,
            agent_run_id=rc.agent_run_id,
            node_name="revision",
            system_prompt=REVISION_SYSTEM_PROMPT,
        )
        try:
            return RevisionOutput(**raw)
        except ValidationError as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model response failed RevisionOutput schema validation",
                details={"validation_error": str(exc)},
            ) from exc

    def _build_prompt(self, envelope: AgentInputEnvelope) -> str:
        """构造送入真实模型的修订指令（只含本项目上下文，不含密钥）。"""
        baseline = envelope.accepted_text or envelope.draft_text or ""
        parts = [
            f"项目：{envelope.project.get('name', envelope.project.get('id', ''))}",
            f"基线版本：{envelope.base_scene_revision_id}",
            "作者反馈："
            + (envelope.author_feedback.text or "（无文本反馈，仅操作）"),
        ]
        source_ids = [e.source_id for e in envelope.context_manifest]
        if source_ids:
            parts.append("来源引用：" + ",".join(source_ids))
        parts.append("原稿正文（截断）：" + baseline[:_ACCEPTED_TEXT_LIMIT])
        return "\n".join(parts)

    def _run_fake(self, envelope: AgentInputEnvelope) -> RevisionOutput:
        """确定性占位实现（Fake model 语义；未注入 Provider 时使用）。"""
        return RevisionOutput(
            status="ready",
            base_scene_revision_id=envelope.base_scene_revision_id,
            operation_format="semantic_text",
            operations=[
                TextOperation(
                    op="replace",
                    old_text="",
                    new_text="revised",
                    reason="author feedback",
                    source="author_feedback",
                )
            ],
        )
