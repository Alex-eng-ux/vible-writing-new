"""ContinuityAgent 模块。

负责把草稿与已确认的事实/状态做一致性检查，并返回结构化 `ContinuityOutput`。
边界：Agent 只进行检查，绝不修改正文（prose）。

Task 9 真实 Agent 接线（additive）：
- 构造时可注入统一 `ModelProvider`（真实实现见 ``model_provider.py``）；注入后
  经 provider 调用真实模型并严格以 `ContinuityOutput` schema 校验响应；
- 未注入 provider 时保持原占位/fake 行为（确定性输出，默认测试不访问网络）；
- provider 失败（超时/401/限流/非 JSON/结构化输出失败）统一映射为
  ``AppError(LLM_*)`` 上抛，由运行层把运行置为 failed；绝不产生版本/候选/Canon。
"""

from __future__ import annotations

from pydantic import ValidationError

from app.agents.model_provider import ModelProvider
from app.agents.prompts import CONTINUITY_SYSTEM_PROMPT
from app.agents.schemas import AgentInputEnvelope, ContinuityOutput
from app.errors import AppError

# 送入真实模型的基线正文上限（防止 Prompt 过大；超出仅截断，不拼接）。
_ACCEPTED_TEXT_LIMIT = 4000


class ContinuityAgent:
    """检查草稿与已确认事实/状态的一致性；绝不修改正文。"""

    def __init__(self, provider: ModelProvider | None = None) -> None:
        """构造 ContinuityAgent。

        参数：
            provider: 统一模型 Provider。为 None 时使用确定性占位实现
                （Fake model 语义，默认测试不访问网络）；注入真实 Provider 时
                经其调用真实模型并校验 `ContinuityOutput`。
        """
        self._provider = provider

    def run(self, envelope: AgentInputEnvelope) -> ContinuityOutput:
        """对草稿做连续性检查。

        参数：
            envelope: 输入信封，需包含基线文本（`accepted_text` 或 `draft_text`）。

        返回：`ContinuityOutput`。当基线文本缺失时返回 `needs_clarification`
        状态并附带澄清问题（不调用模型）；否则调用 provider 校验并返回结构化
        结果；未注入 provider 时返回 `pass` 状态与空场景快照增量。

        关键约束：仅检查，不修改任何已确认文本。
        失败条件：真实 Provider 调用失败抛 ``AppError(LLM_*)``；模型响应无法
        通过 `ContinuityOutput` 校验时抛 ``AppError(LLM_RESPONSE_INVALID)``。
        """
        if not envelope.accepted_text and not envelope.draft_text:
            return ContinuityOutput(
                status="needs_clarification",
                clarification_questions=["missing baseline text for continuity check"],
            )
        if self._provider is not None:
            return self._run_with_provider(envelope)
        return self._run_fake()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _run_with_provider(self, envelope: AgentInputEnvelope) -> ContinuityOutput:
        """经真实 Provider 做连续性检查并严格校验 `ContinuityOutput`。

        失败条件：provider 错误原样上抛；响应无法通过 schema 校验时包装为
        ``LLM_RESPONSE_INVALID``，绝不把非法输出当作可提交结果。
        """
        provider = self._provider
        if provider is None:
            raise AppError("RUN_STATE_CONFLICT", "continuity agent has no model provider")
        rc = envelope.runtime_context
        raw = provider.invoke_structured(
            prompt=self._build_prompt(envelope),
            generation_run_id=rc.generation_run_id,
            agent_run_id=rc.agent_run_id,
            node_name="continuity",
            system_prompt=CONTINUITY_SYSTEM_PROMPT,
        )
        try:
            return ContinuityOutput(**raw)
        except ValidationError as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model response failed ContinuityOutput schema validation",
                details={"validation_error": str(exc)},
            ) from exc

    def _build_prompt(self, envelope: AgentInputEnvelope) -> str:
        """构造送入真实模型的连续性检查指令（只含本发明项目上下文，不含密钥）。"""
        baseline = envelope.accepted_text or envelope.draft_text or ""
        parts = [
            f"项目：{envelope.project.get('name', envelope.project.get('id', ''))}",
        ]
        source_ids = [e.source_id for e in envelope.context_manifest]
        if source_ids:
            parts.append("来源引用：" + ",".join(source_ids))
        parts.append("已接受基线正文（截断）：" + baseline[:_ACCEPTED_TEXT_LIMIT])
        return "\n".join(parts)

    def _run_fake(self) -> ContinuityOutput:
        """确定性占位实现（Fake model 语义；未注入 Provider 时使用）。"""
        return ContinuityOutput(status="pass", scene_snapshot_delta={})
