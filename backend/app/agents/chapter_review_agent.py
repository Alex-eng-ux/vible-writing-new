"""ChapterReviewAgent 模块。

Task 4B 章节审校 Agent：执行章节聚合后的章节级审校。边界：只在章节版本与
聚合资格满足条件后运行（调用方保证）；绝不调用 WritingAgent，也不直接修改
正文。

Task 9 真实 Agent 接线（additive）：
- 构造时可注入统一 `ModelProvider`（真实实现见 ``model_provider.py``）；注入后
  经 provider 调用真实模型并严格以 `ChapterReviewOutput` schema 校验响应；
- 未注入 provider 时保持原占位/fake 行为（确定性输出，默认测试不访问网络）；
- provider 失败（超时/401/限流/非 JSON/结构化输出失败）统一映射为
  ``AppError(LLM_*)`` 上抛，由运行层把运行置为 failed；绝不产生章节版本、
  Canon 或 handoff 写入。
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.agents.model_provider import ModelProvider
from app.agents.prompts import CHAPTER_REVIEW_SYSTEM_PROMPT
from app.agents.schemas import AgentInputEnvelope, ChapterReviewOutput
from app.errors import AppError

# 送入真实模型的章节合同/上下文上限（防止 Prompt 过大；超出仅截断，不拼接）。
_CONTRACT_LIMIT = 4000


class ChapterReviewAgent:
    """执行章节级审校并返回结构化 `ChapterReviewOutput`。

    绝不调用 WritingAgent，也不直接修改正文。
    """

    def __init__(self, provider: ModelProvider | None = None) -> None:
        """构造 ChapterReviewAgent。

        参数：
            provider: 统一模型 Provider。为 None 时使用确定性占位实现
                （Fake model 语义，默认测试不访问网络）；注入真实 Provider 时
                经其调用真实模型并校验 `ChapterReviewOutput`。
        """
        self._provider = provider

    def run(self, envelope: AgentInputEnvelope) -> ChapterReviewOutput:
        """对章节聚合结果执行章节审校。

        参数：
            envelope: 输入信封，需包含章节契约与聚合上下文。

        返回：`ChapterReviewOutput`。当缺少章节契约时返回 `needs_clarification`
        状态并附带澄清问题（不调用模型）；否则调用 provider 校验并返回结构化
        结果；未注入 provider 时返回 `ready`、空问题列表与 "pass" 评级。

        关键约束：审校只读，不修改任何正文或版本。
        失败条件：真实 Provider 调用失败抛 ``AppError(LLM_*)``；模型响应无法
        通过 `ChapterReviewOutput` 校验时抛 ``AppError(LLM_RESPONSE_INVALID)``。
        """
        if not envelope.chapter_contract:
            return ChapterReviewOutput(
                status="needs_clarification",
                clarification_questions=["missing chapter contract for review"],
            )
        if self._provider is not None:
            return self._run_with_provider(envelope)
        return self._run_fake()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _run_with_provider(self, envelope: AgentInputEnvelope) -> ChapterReviewOutput:
        """经真实 Provider 执行章节审校并严格校验 `ChapterReviewOutput`。

        失败条件：provider 错误原样上抛；响应无法通过 schema 校验时包装为
        ``LLM_RESPONSE_INVALID``，绝不把非法输出当作可提交结果。
        """
        provider = self._provider
        if provider is None:
            raise AppError("RUN_STATE_CONFLICT", "chapter review has no model provider")
        rc = envelope.runtime_context
        raw = provider.invoke_structured(
            prompt=self._build_prompt(envelope),
            generation_run_id=rc.generation_run_id,
            agent_run_id=rc.agent_run_id,
            node_name="chapter_review",
            system_prompt=CHAPTER_REVIEW_SYSTEM_PROMPT,
        )
        try:
            return ChapterReviewOutput(**raw)
        except ValidationError as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model response failed ChapterReviewOutput schema validation",
                details={"validation_error": str(exc)},
            ) from exc

    def _build_prompt(self, envelope: AgentInputEnvelope) -> str:
        """构造送入真实模型的章节审校指令（只含本发明项目上下文，不含密钥）。"""
        parts = [
            f"项目：{envelope.project.get('name', envelope.project.get('id', ''))}",
            "章节契约："
            + json.dumps(envelope.chapter_contract, ensure_ascii=False)[:_CONTRACT_LIMIT],
        ]
        source_ids = [e.source_id for e in envelope.context_manifest]
        if source_ids:
            parts.append("来源引用：" + ",".join(source_ids))
        return "\n".join(parts)

    def _run_fake(self) -> ChapterReviewOutput:
        """确定性占位实现（Fake model 语义；未注入 Provider 时使用）。"""
        return ChapterReviewOutput(
            status="ready", review_issues=[], overall_rating="pass", submitted=True
        )
