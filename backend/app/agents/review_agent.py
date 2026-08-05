"""ReviewAgent 模块。

负责执行场景级评审，返回结构化 `ReviewOutput`（含 `ReviewIssue` 列表）。
边界：评审 Agent 绝不调用 WritingAgent、也不直接修改正文。

Task 9 真实 Agent 接线（additive）：
- 构造时可注入统一 `ModelProvider`（真实实现见 ``model_provider.py``）；注入后
  经 provider 调用真实模型并严格以 `ReviewOutput` schema 校验响应；
- 未注入 provider 时保持原占位/fake 行为（确定性输出，默认测试不访问网络）；
- provider 失败（超时/401/限流/非 JSON/结构化输出失败）统一映射为
  ``AppError(LLM_*)`` 上抛，由运行层把运行置为 failed；绝不产生版本/候选/Canon。
"""

from __future__ import annotations

from pydantic import ValidationError

from app.agents.model_provider import ModelProvider
from app.agents.prompts import REVIEW_SYSTEM_PROMPT
from app.agents.schemas import AgentInputEnvelope, ReviewOutput
from app.errors import AppError

# 送入真实模型的场景正文上限（防止 Prompt 过大；超出仅截断，不拼接）。
_ACCEPTED_TEXT_LIMIT = 4000


class ReviewAgent:
    """执行场景级评审并返回结构化 `ReviewIssue`。

    绝不调用 WritingAgent，也不直接修改正文。
    """

    def __init__(self, provider: ModelProvider | None = None) -> None:
        """构造 ReviewAgent。

        参数：
            provider: 统一模型 Provider。为 None 时使用确定性占位实现
                （Fake model 语义，默认测试不访问网络）；注入真实 Provider 时
                经其调用真实模型并校验 `ReviewOutput`。
        """
        self._provider = provider

    def run(self, envelope: AgentInputEnvelope) -> ReviewOutput:
        """对场景草稿执行评审。

        参数：
            envelope: 输入信封，需包含场景文本（`draft_text` 或 `accepted_text`）。

        返回：`ReviewOutput`。当场景文本缺失时返回 `needs_clarification` 状态
        并附带澄清问题（不调用模型）；否则调用 provider 校验并返回结构化结果；
        未注入 provider 时返回 `ready` 状态、空评审问题列表与 "pass" 评级。

        关键约束：评审只读，不修改任何正文。
        失败条件：真实 Provider 调用失败抛 ``AppError(LLM_*)``；模型响应无法
        通过 `ReviewOutput` 校验时抛 ``AppError(LLM_RESPONSE_INVALID)``。
        """
        if not envelope.draft_text and not envelope.accepted_text:
            return ReviewOutput(
                status="needs_clarification",
                clarification_questions=["missing scene text for review"],
            )
        if self._provider is not None:
            return self._run_with_provider(envelope)
        return self._run_fake()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _run_with_provider(self, envelope: AgentInputEnvelope) -> ReviewOutput:
        """经真实 Provider 执行评审并严格校验 `ReviewOutput`。

        失败条件：provider 错误原样上抛；响应无法通过 schema 校验时包装为
        ``LLM_RESPONSE_INVALID``，绝不把非法输出当作可提交结果。
        """
        provider = self._provider
        if provider is None:
            raise AppError("RUN_STATE_CONFLICT", "review agent has no model provider")
        rc = envelope.runtime_context
        raw = provider.invoke_structured(
            prompt=self._build_prompt(envelope),
            generation_run_id=rc.generation_run_id,
            agent_run_id=rc.agent_run_id,
            node_name="review",
            system_prompt=REVIEW_SYSTEM_PROMPT,
        )
        try:
            return ReviewOutput(**raw)
        except ValidationError as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model response failed ReviewOutput schema validation",
                details={"validation_error": str(exc)},
            ) from exc

    def _build_prompt(self, envelope: AgentInputEnvelope) -> str:
        """构造送入真实模型的评审指令（只含发明项目上下文，不含密钥）。"""
        text = envelope.draft_text or envelope.accepted_text or ""
        parts = [
            f"项目：{envelope.project.get('name', envelope.project.get('id', ''))}",
        ]
        source_ids = [e.source_id for e in envelope.context_manifest]
        if source_ids:
            parts.append("来源引用：" + ",".join(source_ids))
        parts.append("待评审场景正文（截断）：" + text[:_ACCEPTED_TEXT_LIMIT])
        return "\n".join(parts)

    def _run_fake(self) -> ReviewOutput:
        """确定性占位实现（Fake model 语义；未注入 Provider 时使用）。"""
        return ReviewOutput(status="ready", review_issues=[], overall_rating="pass")
