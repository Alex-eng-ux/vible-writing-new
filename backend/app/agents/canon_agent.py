"""CanonAgent 模块。

Task 4C Canon Agent：从作者已接受的章节版本提取章节级候选，或在作者明确
触发且场景版本已接受时提取场景级局部候选。边界：
- 章节级候选只允许以 `accepted_chapter_revision_id` 为来源；
- 场景级局部候选只允许在 `canon_scope=scene` 且 `accepted_scene_revision_id`
  非空时生成，候选 `scope=scene`，不得直接更新全局 Canon；
- 只返回结构化候选（`CanonOutput`），不写库；候选持久化与正式更新由
  Canon 分支（CanonGraph）的领域服务完成。

Task 9 真实 Agent 接线（additive）：
- 构造时可注入统一 `ModelProvider`（真实实现见 ``model_provider.py``）；注入后
  经 provider 调用真实模型并严格以 `CanonOutput` schema 校验响应；
- 未注入 provider 时保持原占位/fake 行为（确定性输出，默认测试不访问网络）；
- provider 失败（超时/401/限流/非 JSON/结构化输出失败）统一映射为
  ``AppError(LLM_*)`` 上抛，由运行层把运行置为 failed；绝不产生 Canon 写入。
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import ValidationError

from app.agents.model_provider import ModelProvider
from app.agents.prompts import CANON_SYSTEM_PROMPT
from app.agents.schemas import (
    AgentInputEnvelope,
    CanonCandidate,
    CanonOutput,
    CanonSource,
    EffectiveStoryTime,
)
from app.errors import AppError

# 送入真实模型的已接受正文上限（防止 Prompt 过大；超出仅截断，不拼接）。
_ACCEPTED_TEXT_LIMIT = 4000


class CanonAgent:
    """从已接受版本提取三类 Canon 候选。

    输入信封必须携带 `canon_scope` 与对应的已接受版本指针；来源不足时返回
    `needs_clarification`，绝不读取未接受草稿或自行确认事实。
    """

    def __init__(self, provider: ModelProvider | None = None, fake: bool = True) -> None:
        """构造 CanonAgent。

        参数：
            provider: 统一模型 Provider。为 None 时使用确定性占位实现
                （Fake model 语义，默认测试不访问网络）；注入真实 Provider 时
                经其调用真实模型并校验 `CanonOutput`。
            fake: 是否使用占位实现（未注入 provider 时始终为占位行为）。
        """
        self._provider = provider
        self._fake = fake

    def run(self, envelope: AgentInputEnvelope) -> CanonOutput:
        """根据输入信封提取待确认的三类候选。

        参数：
            envelope: 输入信封，需包含 `canon_scope` 与对应的已接受版本指针
            （`accepted_chapter_revision_id` 或 `accepted_scene_revision_id` +
            `runtime_context.scene_id`）。

        返回：`CanonOutput`。来源不足或作用域不明确时返回 `needs_clarification`
        状态并附带澄清问题；否则调用 provider 校验并返回结构化结果；未注入
        provider 时返回 `ready` 状态与三类候选列表。

        关键约束：只从已接受版本读取来源；候选 `scope` 必须等于 `canon_scope`；
        `candidate_id` 由运行时后置分配，模型不生成正式值。
        失败条件：真实 Provider 调用失败抛 ``AppError(LLM_*)``；模型响应无法
        通过 `CanonOutput` 校验时抛 ``AppError(LLM_RESPONSE_INVALID)``。
        """
        canon_scope = envelope.canon_scope
        if canon_scope == "chapter":
            if not envelope.accepted_chapter_revision_id:
                return CanonOutput(
                    status="needs_clarification",
                    clarification_questions=["chapter canon requires an accepted chapter revision"],
                )
        elif canon_scope == "scene":
            if not envelope.accepted_scene_revision_id or not envelope.runtime_context.scene_id:
                return CanonOutput(
                    status="needs_clarification",
                    clarification_questions=["scene canon requires an accepted scene revision and scene_id"],
                )
        else:
            return CanonOutput(
                status="needs_clarification",
                clarification_questions=["canon_scope must be chapter or scene"],
            )

        if self._provider is not None:
            return self._run_with_provider(envelope)
        return self._run_fake(envelope)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _run_with_provider(self, envelope: AgentInputEnvelope) -> CanonOutput:
        """经真实 Provider 提取 Canon 候选并严格校验 `CanonOutput`。

        失败条件：provider 错误原样上抛；响应无法通过 schema 校验时包装为
        ``LLM_RESPONSE_INVALID``，绝不把非法输出当作可提交结果。
        """
        provider = self._provider
        if provider is None:
            raise AppError("RUN_STATE_CONFLICT", "canon agent has no model provider")
        rc = envelope.runtime_context
        raw = provider.invoke_structured(
            prompt=self._build_prompt(envelope),
            generation_run_id=rc.generation_run_id,
            agent_run_id=rc.agent_run_id,
            node_name="canon",
            system_prompt=CANON_SYSTEM_PROMPT,
        )
        try:
            return CanonOutput(**raw)
        except ValidationError as exc:
            raise AppError(
                "LLM_RESPONSE_INVALID",
                "model response failed CanonOutput schema validation",
                details={"validation_error": str(exc)},
            ) from exc

    def _build_prompt(self, envelope: AgentInputEnvelope) -> str:
        """构造送入真实模型的 Canon 提取指令（只含本发明项目上下文，不含密钥）。"""
        parts = [
            f"Canon 作用域：{envelope.canon_scope}",
            f"项目：{envelope.project.get('name', envelope.project.get('id', ''))}",
        ]
        if envelope.accepted_text:
            parts.append("已接受正文（截断）：" + envelope.accepted_text[:_ACCEPTED_TEXT_LIMIT])
        source_ids = [e.source_id for e in envelope.context_manifest]
        if source_ids:
            parts.append("来源引用：" + ",".join(source_ids))
        return "\n".join(parts)

    def _run_fake(self, envelope: AgentInputEnvelope) -> CanonOutput:
        """确定性占位实现（Fake model 语义；未注入 Provider 时使用）。"""
        # canon_scope 已经由 run() 校验为非 None 的 chapter|scene。
        canon_scope: Literal["chapter", "scene"] = cast(
            Literal["chapter", "scene"], envelope.canon_scope
        )
        # fake 实现：从信封预置的候选提示生成；无预置时从已接受正文提取一条事实候选。
        hints = (envelope.snapshot_before or {}).get("candidates") or []
        candidates = [self._build_candidate(canon_scope, envelope, hint) for hint in hints]
        if not candidates and envelope.accepted_text:
            candidates = [
                self._build_candidate(
                    canon_scope,
                    envelope,
                    {"candidate_type": "fact", "local_key": "fact-auto", "claim": envelope.accepted_text[:120]},
                )
            ]
        fact_candidates = [c for c in candidates if c.candidate_type == "fact"]
        timeline = [c for c in candidates if c.candidate_type == "timeline_event"]
        plot_threads = [c for c in candidates if c.candidate_type == "plot_thread"]
        return CanonOutput(
            status="ready",
            fact_candidates=fact_candidates,
            timeline_event_candidates=timeline,
            plot_thread_updates=plot_threads,
            evidence_refs=[hint.get("source_id", "src") for hint in hints if hint.get("source_id")],
        )

    def _build_candidate(
        self, canon_scope: str, envelope: AgentInputEnvelope, hint: dict
    ) -> CanonCandidate:
        """把预置候选提示规范化为 `CanonCandidate`（scope 继承当前运行作用域）。"""
        story_time = hint.get("effective_story_time") or {}
        # canon_scope 已经由 run() 校验为非 None 的 chapter|scene。
        scope: Literal["chapter", "scene"] = cast(Literal["chapter", "scene"], canon_scope)
        return CanonCandidate(
            candidate_id=None,
            candidate_type=hint.get("candidate_type", "fact"),
            local_key=hint.get("local_key", "candidate"),
            claim=hint.get("claim", ""),
            status="pending_author_confirmation",
            scope=scope,
            source=CanonSource(
                chapter_id=envelope.runtime_context.chapter_id,
                scene_id=envelope.runtime_context.scene_id if canon_scope == "scene" else None,
                source_id=hint.get("source_id", ""),
                paragraph_ref=hint.get("paragraph_ref"),
                text_locator=hint.get("text_locator") or {},
            ),
            effective_story_time=EffectiveStoryTime(
                value=story_time.get("value", ""),
                precision=story_time.get("precision", "unknown"),
            ),
            narrative_knowledge=hint.get("narrative_knowledge", "objective"),
            resolution_action=hint.get("resolution_action", "confirm_existing"),
            evidence_refs=hint.get("evidence_refs") or [],
            entities=hint.get("entities") or [],
            thread_state=hint.get("thread_state"),
            planned_resolution=hint.get("planned_resolution"),
        )
