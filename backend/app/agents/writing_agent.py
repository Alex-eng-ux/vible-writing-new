"""WritingAgent 模块。

负责生成结构化场景草稿，支持三种模式：draft（新章节）、continue（续写）、
rewrite（改写）。边界：Agent 只返回结构化输出 `WritingOutput`，绝不直接创建
`SceneRevision`；所有正式写入由后续领域服务负责。

Task 9 真实 Agent 接线（additive）：
- 构造时可注入统一 `ModelProvider`（真实实现见 ``model_provider.py``）；
  注入后经 provider 调用真实模型并严格以 `WritingOutput` schema 校验响应；
- 未注入 provider 时保持原占位/fake 行为（确定性输出，默认测试不访问网络）；
- provider 失败（超时/401/限流/非 JSON/结构化输出失败）统一映射为
  ``AppError(LLM_*)`` 上抛，由运行层把运行置为 failed；绝不产生版本/候选/Canon。
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import ValidationError

from app.agents.model_provider import ModelProvider
from app.agents.prompts import WRITING_SYSTEM_PROMPT
from app.agents.schemas import AgentInputEnvelope, CandidateFact, WritingOutput
from app.errors import AppError

# 送入真实模型的已接受正文上限（防止 Prompt 过大；超出仅截断，不拼接）。
_ACCEPTED_TEXT_LIMIT = 4000


class WritingAgent:
    """生成结构化场景草稿（draft / continue / rewrite）。

    只返回结构化输出，绝不直接创建 `SceneRevision`。
    """

    def __init__(self, provider: ModelProvider | None = None) -> None:
        """构造 WritingAgent。

        参数：
            provider: 统一模型 Provider。为 None 时使用确定性占位实现
                （Fake model 语义，默认测试不访问网络）；注入真实 Provider 时
                经其调用真实模型并校验 `WritingOutput`。
        """
        self._provider = provider

    def run(self, envelope: AgentInputEnvelope) -> WritingOutput:
        """根据输入信封生成场景草稿。

        参数：
            envelope: 输入信封，包含请求类型（new_chapter / rewrite / continue）、
                场景简介与上下文清单。

        返回：`WritingOutput`。当缺少场景简介或上下文（`scene_brief` 与
        `context_pack` 均为空）时返回 `needs_clarification` 状态并附带澄清问题
        （不调用模型）；否则返回 `ready` 状态，包含生成的草稿内容、候选事实与
        来源引用。

        关键约束：草稿内容仅基于当前输入生成，不触碰任何已确认（accepted）文本。
        失败条件：真实 Provider 调用失败抛 ``AppError(LLM_*)``；模型响应无法
        通过 `WritingOutput` 校验时抛 ``AppError(LLM_RESPONSE_INVALID)``。
        """
        mode = self._resolve_mode(envelope.request_type)
        if not envelope.scene_brief and not envelope.context_pack:
            return WritingOutput(
                status="needs_clarification",
                mode=mode,
                clarification_questions=["missing scene brief or context"],
            )
        if self._provider is not None:
            return self._run_with_provider(envelope, mode)
        return self._run_fake(envelope, mode)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_mode(request_type: str) -> Literal["draft", "continue", "rewrite"]:
        """由请求类型解析写作模式（new_chapter->draft、rewrite->rewrite、其余->continue）。"""
        if request_type == "new_chapter":
            return "draft"
        if request_type == "rewrite":
            return "rewrite"
        return "continue"

    def _run_with_provider(
        self, envelope: AgentInputEnvelope, mode: Literal["draft", "continue", "rewrite"]
    ) -> WritingOutput:
        """经真实 Provider 生成草稿并严格校验 `WritingOutput`。

        失败条件：provider 错误原样上抛；响应无法通过 schema 校验时包装为
        ``LLM_RESPONSE_INVALID``，绝不把非法输出当作可提交结果。
        """
        provider = self._provider
        if provider is None:
            raise AppError("RUN_STATE_CONFLICT", "writing agent has no model provider")
        rc = envelope.runtime_context
        raw = provider.invoke_structured(
            prompt=self._build_prompt(envelope, mode),
            generation_run_id=rc.generation_run_id,
            agent_run_id=rc.agent_run_id,
            node_name="writing",
            system_prompt=WRITING_SYSTEM_PROMPT,
        )
        try:
            return WritingOutput(**raw)
        except ValidationError as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model response failed WritingOutput schema validation",
                details={"validation_error": str(exc)},
            ) from exc

    def _build_prompt(
        self, envelope: AgentInputEnvelope, mode: Literal["draft", "continue", "rewrite"]
    ) -> str:
        """构造送入真实模型的写作指令（只含本项目上下文，不含任何密钥）。"""
        parts = [
            f"写作模式：{mode}",
            f"项目：{envelope.project.get('name', envelope.project.get('id', ''))}",
        ]
        if envelope.scene_brief:
            parts.append(
                "场景简介：" + json.dumps(envelope.scene_brief, ensure_ascii=False)
            )
        if envelope.accepted_text:
            parts.append("已接受正文（截断）：" + envelope.accepted_text[:_ACCEPTED_TEXT_LIMIT])
        if envelope.author_feedback and envelope.author_feedback.text:
            parts.append("作者反馈：" + envelope.author_feedback.text)
        source_ids = [e.source_id for e in envelope.context_manifest]
        if source_ids:
            parts.append("来源引用：" + ",".join(source_ids))
        return "\n".join(parts)

    def _run_fake(
        self, envelope: AgentInputEnvelope, mode: Literal["draft", "continue", "rewrite"]
    ) -> WritingOutput:
        """确定性占位实现（Fake model 语义；未注入 Provider 时使用）。"""
        content = f"Generated scene content in {mode} mode."
        source_refs = [e.source_id for e in envelope.context_manifest]
        return WritingOutput(
            status="ready",
            mode=mode,
            content=content,
            candidate_facts=[
                CandidateFact(
                    candidate_type="fact",
                    local_key="fact-1",
                    claim="A generated persistent fact.",
                    status="candidate",
                    scope="scene",
                    evidence_refs=source_refs[:1],
                )
            ],
            context_source_refs=source_refs,
            evidence_refs=source_refs,
        )
