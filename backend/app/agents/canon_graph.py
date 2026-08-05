"""LangGraph Canon 编排图模块。

`CanonGraph` 是已编译的 LangGraph `StateGraph`，负责按
CanonAgent -> 作者逐条确认（interrupt）-> 正式提交 的顺序编排 Canon 流程。
与 `ChapterGraph` 一致，图只负责编排与状态流转；候选持久化与正式 Canon
写入委托给领域服务（`upsert_canon_candidates` / `CanonCandidateService`），
正式写入前必须经过 `CommitGuard` 校验。

Task 4C 边界：只追加 Canon 输出与决策分支，不改写 Task 4A/4B 已冻结的
单场景状态、Router 终态或运行身份字段。Agent 与普通正文节点不能直接写入
正式 Canon；正式 `CanonFact` 只在作者确认后由提交节点生成。
"""

from __future__ import annotations

import os

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import StateSnapshot
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session

from app.agents.canon_agent import CanonAgent
from app.agents.hook_registry import HookRegistry
from app.agents.nodes import AgentCallable
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope, CanonOutput
from app.agents.state import ChapterRunState
from app.domain.commit_guard import CommitGuard
from app.domain.interfaces import CommandContext, LeaseContext, RunWriteFence, RunWriteFencePort
from app.domain.lease import SqlRunWriteFencePort
from app.domain.story_bible import upsert_canon_candidates, validate_canon_candidate_sources
from app.errors import AppError
from app.runtime.checkpointer import PostgresCheckpointer
from app.services.canon_candidate_service import CanonCandidateService

_CANON_AGENT = "canon_agent"
_COMMIT = "canon_commit"
_PAUSE = "canon_confirmation_pause"

# 作者逐条决策 action -> 候选持久状态映射（confirm->accepted 等）。
_ACTION_TO_STATUS = {
    "confirm": "accepted",
    "reject": "rejected",
    "defer": "deferred",
}


class CanonGraph:
    """已编译的 LangGraph Canon 编排图。

    图结构：CanonAgent -> 作者逐条确认（interrupt，可恢复）-> 正式提交。
    候选在 CanonAgent 节点后立即幂等持久化（来源只能是当前 accepted 版本，
    使用 Worker/agent 上下文）；正式提交节点只接受作者人工命令上下文
    （服务端生成的 `manual_command_id` + API command write fence，
    `source=author`），绝不伪造 Worker 身份。
    """

    NODE_ORDER = [_CANON_AGENT, _COMMIT]

    def __init__(
        self,
        session: Session,
        registry: HookRegistry,
        router: AgentResultRouter,
        agent: CanonAgent | None = None,
        checkpointer: BaseCheckpointSaver | PostgresCheckpointer | None = None,
        write_fence_port: RunWriteFencePort | None = None,
        actor_id: str | None = None,
    ) -> None:
        """构造 Canon 图并编译。

        参数：
            session: 数据库会话，用于候选持久化与正式 Canon 更新。
            registry: hook 注册表，用于生命周期钩子与 schema 校验。
            router: 结果路由器，决定 CanonAgent 输出后的去向。
            agent: CanonAgent 实例，缺省时使用默认实现。
            checkpointer: 断点存储；为 None 时使用内存 `MemorySaver`。
            write_fence_port: API command fence 端口；缺省使用
                `SqlRunWriteFencePort`（作者确认提交必须取得 API command fence）。
            actor_id: 服务端解析的作者身份；缺省从 `ACTOR_ID` 配置读取，
                绝不由客户端/Agent 正文提供。

        副作用：构建并编译最终的 StateGraph，保存到 `self._compiled`。
        """
        self._session = session
        self._registry = registry
        self._router = router
        self._canon = AgentCallable("canon", agent or CanonAgent(), registry, router)
        self._checkpointer = self._resolve_checkpointer(checkpointer)
        self._write_fence_port = write_fence_port or SqlRunWriteFencePort(session)
        self._actor_id = actor_id or os.environ.get("ACTOR_ID") or "author-1"
        self._bound_envelope: AgentInputEnvelope | None = None
        self._graph = self._build()
        self._compiled = self._graph.compile(checkpointer=self._checkpointer)

    @property
    def registry(self) -> HookRegistry:
        """返回图使用的 hook 注册表（供观测 wiring 注册 TraceHook）。"""
        return self._registry

    @staticmethod
    def _resolve_checkpointer(
        checkpointer: BaseCheckpointSaver | PostgresCheckpointer | None,
    ) -> BaseCheckpointSaver:
        """解析并归一化 checkpointer 供 LangGraph 编译使用。"""
        if checkpointer is None:
            return MemorySaver()
        if isinstance(checkpointer, PostgresCheckpointer):
            return checkpointer.saver
        return checkpointer

    def _build(self) -> StateGraph:
        """构建未编译的 StateGraph：注册节点并定义边路由。"""
        graph = StateGraph(ChapterRunState)
        graph.add_node(_CANON_AGENT, self._canon_agent_node)
        graph.add_node(_COMMIT, self._commit_node)
        graph.add_node(_PAUSE, self._pause_node)

        graph.add_edge(START, _CANON_AGENT)
        graph.add_conditional_edges(
            _CANON_AGENT,
            self._route,
            {_PAUSE: _PAUSE, END: END},
        )
        graph.add_conditional_edges(
            _COMMIT,
            self._route,
            {END: END},
        )
        graph.add_conditional_edges(
            _PAUSE,
            self._route_after_pause,
            {_COMMIT: _COMMIT, END: END},
        )
        return graph

    def _canon_agent_node(self, state: ChapterRunState, config: RunnableConfig) -> dict:
        """CanonAgent 节点：提取候选 -> 幂等持久化 -> 等待作者逐条确认。

        候选来源只允许当前 accepted 版本（章节/场景指针）；来源不满足时
        暂停并给出稳定错误。正式写入不在此节点发生。
        """
        envelope = self._current_envelope()
        result = self._canon(state, envelope)
        outcome = result.get("outcome")
        if outcome is not None and outcome.status in (
            "needs_clarification",
            "failed",
            "cancel",
        ):
            state["pending_node"] = outcome.pending_node
            state["clarification_questions"] = outcome.clarification_questions
            state["run_status"] = "paused"
            return {
                "pending_node": outcome.pending_node,
                "clarification_questions": outcome.clarification_questions,
                "run_status": "paused",
                "last_durable_node": _CANON_AGENT,
                "error_code": outcome.error_code,
            }
        output = result.get("output")
        if not isinstance(output, CanonOutput):
            raise AppError("RUN_STATE_CONFLICT", "canon agent produced no structured output")
        candidates = self._collect_candidates(envelope, output)
        try:
            persisted = self._persist_candidates(envelope, candidates)
        except AppError as exc:
            state["run_status"] = "paused"
            return {
                "pending_node": _CANON_AGENT,
                "clarification_questions": [exc.message],
                "run_status": "paused",
                "last_durable_node": _CANON_AGENT,
                "error_code": exc.code,
            }
        return {
            "last_durable_node": _CANON_AGENT,
            "run_status": "paused",
            "pending_node": "canon_confirmation",
            "clarification_questions": [],
            "canon_scope": envelope.canon_scope,
            "canon_candidates": persisted,
        }

    def _commit_node(self, state: ChapterRunState, config: RunnableConfig) -> dict:
        """正式提交节点：以作者命令身份应用决策并生成正式 Canon。

        只有作者 confirm|reject|defer 后进入；cancel 不进入本节点。正式提交
        必须使用服务端生成的 `manual_command_id` 经 `RunWriteFencePort`
        领取 API command write fence，`source=author`，绝不伪造 Worker 身份
        或硬编码 actor。正式 `CanonFact`/`TimelineEvent`/`PlotThread` 只在
        章节级 confirm 时按候选类型生成，场景级确认不更新全局 Canon。
        """
        envelope = self._current_envelope()
        decisions = list(state.get("candidate_decisions") or [])
        action = state.get("_pause_action")
        # 兼容别名：未显式携带 decision 的条目用作者 action 映射。
        fallback = _ACTION_TO_STATUS.get(action or "")
        for decision in decisions:
            if not decision.get("decision") and fallback:
                decision["decision"] = fallback
        try:
            manual_command_id = state.get("manual_command_id")
            idempotency_key = state.get("decision_idempotency_key") or envelope.runtime_context.thread_id
            expected_run_version = state.get("expected_run_version")
            # 作者确认的正式提交：取得 API command fence（source=author），
            # 不使用 Worker 租约，也不把运行 ID 填入人工命令的 generation_run_id。
            write_fence = self._write_fence_port.claim_api_command(
                envelope.runtime_context.generation_run_id,
                manual_command_id or "",
                expected_run_version or 0,
            )
            ctx: CommandContext = {
                "lease_context": None,
                "write_fence": write_fence,
                "generation_run_id": None,
                "agent_run_id": None,
                "manual_command_id": manual_command_id,
                "source": "author",
                "parent_generation_run_id": None,
                "supersedes_run_id": None,
                "parent_plan_revision_id": None,
                "actor_id": self._actor_id,
                "preceding_chapter_id": None,
                "preceding_accepted_chapter_revision_id": None,
                "entry_handoff_id": None,
                "entry_source_chapter_revision_id": None,
                "entry_handoff_chain_hash": None,
                "base_scene_revision_id": None,
                "base_chapter_revision_id": None,
                "accepted_scene_revision_id": envelope.accepted_scene_revision_id,
                "accepted_chapter_revision_id": envelope.accepted_chapter_revision_id,
                "plan_revision_id": None,
                "canon_scope": envelope.canon_scope,
                "decision_target": envelope.runtime_context.decision_target,
                "context_source_refs": [],
                "author_decision": None,
                "idempotency_key": idempotency_key,
                "expected_run_version": expected_run_version,
            }
            # 正式写入前统一提交守卫（author 身份互斥 + API command fence）。
            CommitGuard(self._session).validate(
                "apply_canon_decisions",
                ctx["actor_id"],
                ctx.get("base_chapter_revision_id"),
                ctx["idempotency_key"],
                ctx["context_source_refs"],
                generation_run_id=None,
                manual_command_id=manual_command_id,
                expected_run_version=expected_run_version,
                lease_context=None,
                write_fence=write_fence,
            )
            canon_scope = state.get("canon_scope") or envelope.canon_scope or "chapter"
            service = CanonCandidateService(self._session)
            service.confirm_decisions(
                envelope.runtime_context.generation_run_id,
                decisions,
                ctx,
                canon_scope=canon_scope,
                chapter_id=envelope.runtime_context.chapter_id,
                scene_id=envelope.runtime_context.scene_id,
            )
        except AppError as exc:
            state["run_status"] = "failed"
            return {
                "last_durable_node": _COMMIT,
                "run_status": "failed",
                "error_code": exc.code,
            }
        return {
            "last_durable_node": _COMMIT,
            "run_status": "accepted",
            "pending_node": None,
            "clarification_questions": [],
        }

    def _route(self, state: ChapterRunState) -> str:
        """边路由：CanonAgent 输出后等待作者逐条确认（进入 pause）。"""
        if state.get("pending_node") is not None:
            return _PAUSE
        return END

    def _pause_node(self, state: ChapterRunState, config: RunnableConfig) -> dict:
        """等待作者的逐条 Canon 决策（confirm / reject / defer / cancel）。

        通过 `interrupt` 挂起并收集作者决策。confirm/reject/defer 进入正式
        提交节点；cancel 结束运行且不写入候选决策。
        """
        questions = state.get("clarification_questions") or []
        pending_node = state.get("pending_node") or "canon_confirmation"
        decision = interrupt(
            {
                "pending_node": pending_node,
                "clarification_questions": questions,
            }
        )
        decision = decision or {}
        action = decision.get("action", "cancel")
        if action in _ACTION_TO_STATUS:
            # 作者确认：保存服务端生成的 manual_command_id / 幂等键 / 期望运行
            # 版本到 checkpoint，供提交节点以 author 身份领取 API command fence。
            return {
                "pending_node": None,
                "clarification_questions": [],
                "run_status": "running",
                "_pause_action": action,
                "candidate_decisions": decision.get("candidate_decisions") or [],
                "manual_command_id": decision.get("manual_command_id"),
                "decision_idempotency_key": decision.get("idempotency_key"),
                "expected_run_version": decision.get("expected_run_version"),
                "last_durable_node": _COMMIT,
            }
        if action == "cancel":
            return {
                "pending_node": None,
                "clarification_questions": [],
                "run_status": "cancelled",
                "last_durable_node": _COMMIT,
                "_pause_action": "cancel",
            }
        raise AppError("COMMAND_CONTEXT_MISMATCH", f"unknown canon decision: {action}")

    def _route_after_pause(self, state: ChapterRunState) -> str:
        """暂停恢复后的路由：confirm/reject/defer 进入提交节点，cancel 结束。"""
        action = state.get("_pause_action")
        if action == "cancel":
            return END
        if action in _ACTION_TO_STATUS:
            return _COMMIT
        return END

    def _collect_candidates(self, envelope: AgentInputEnvelope, output: CanonOutput) -> list[dict]:
        """把 CanonAgent 输出统一收集为候选持久化载荷（scope 继承当前作用域）。"""
        project_id = (envelope.project or {}).get("id") or ""
        accepted_revision_id = (
            envelope.accepted_chapter_revision_id
            if envelope.canon_scope == "chapter"
            else envelope.accepted_scene_revision_id
        )
        scene_id = envelope.runtime_context.scene_id if envelope.canon_scope == "scene" else None
        payloads: list[dict] = []
        for cand in (
            *output.fact_candidates,
            *output.timeline_event_candidates,
            *output.plot_thread_updates,
        ):
            payloads.append(
                {
                    "project_id": project_id,
                    "chapter_id": envelope.runtime_context.chapter_id,
                    "scene_id": scene_id,
                    "scope": cand.scope,
                    "candidate_type": cand.candidate_type,
                    "fingerprint": None,
                    "source_revision_id": accepted_revision_id,
                    "content": {
                        "claim": cand.claim,
                        "entity_id": None,
                        "effective_story_time": cand.effective_story_time.model_dump(),
                        "narrative_knowledge": cand.narrative_knowledge,
                        "resolution_action": cand.resolution_action,
                        "entities": cand.entities,
                        "state": cand.thread_state or "open",
                        "planned_resolution": cand.planned_resolution,
                    },
                    "local_key": cand.local_key,
                }
            )
        return payloads

    def _persist_candidates(self, envelope: AgentInputEnvelope, candidates: list[dict]) -> list[dict]:
        """校验来源后按 (来源, 类型, 指纹) 幂等持久化候选。

        先校验候选来源只能是当前 accepted 版本，再通过 CommitGuard 校验
        写入 fencing，最后调用领域 `upsert_canon_candidates`。正式 Canon 不
        在此处生成。
        """
        if not candidates:
            return []
        canon_scope = envelope.canon_scope or "chapter"
        validate_canon_candidate_sources(
            self._session,
            candidates,
            canon_scope=canon_scope,
            chapter_id=envelope.runtime_context.chapter_id,
            scene_id=envelope.runtime_context.scene_id,
        )
        ctx = self._build_ctx(envelope)
        CommitGuard(self._session).validate(
            "persist_canon_candidates",
            ctx["actor_id"],
            None,
            ctx["idempotency_key"],
            ctx["context_source_refs"],
            generation_run_id=ctx.get("generation_run_id"),
            manual_command_id=ctx.get("manual_command_id"),
            expected_run_version=ctx.get("expected_run_version"),
            lease_context=ctx.get("lease_context"),
            write_fence=ctx.get("write_fence"),
        )
        return upsert_canon_candidates(self._session, envelope.runtime_context.generation_run_id, candidates, ctx)

    def _build_ctx(self, envelope: AgentInputEnvelope) -> CommandContext:
        """从输入信封构造完整的 `CommandContext`（身份互斥 + fencing 字段）。

        候选持久化与正式 Canon 提交共用该上下文；`source` 恒为 agent，携带
        Worker 租约/写栅栏（若信封提供）。Canon 的 confirm/reject/defer 决策
        由 `candidate_decisions` 表达，不写入普通 `author_decision`（该枚举
        只接受 plan/scene/chapter 的 accept|feedback|cancel，属冻结契约）。
        """
        lease_context: LeaseContext | None = None
        if envelope.lease_worker_id is not None and envelope.lease_fencing_token is not None:
            lease_context = {
                "worker_id": envelope.lease_worker_id,
                "fencing_token": envelope.lease_fencing_token,
            }
        write_fence: RunWriteFence | None = None
        if (
            envelope.write_fence_owner_kind is not None
            and envelope.write_fence_owner_id is not None
            and envelope.write_fence_fencing_token is not None
        ):
            write_fence = {
                "generation_run_id": envelope.runtime_context.generation_run_id,
                "owner_kind": envelope.write_fence_owner_kind,
                "owner_id": envelope.write_fence_owner_id,
                "fencing_token": envelope.write_fence_fencing_token,
            }
        ctx: CommandContext = {
            "lease_context": lease_context,
            "write_fence": write_fence,
            "generation_run_id": envelope.runtime_context.generation_run_id,
            "agent_run_id": envelope.runtime_context.agent_run_id,
            "manual_command_id": None,
            "source": "agent",
            "parent_generation_run_id": envelope.runtime_context.parent_generation_run_id,
            "supersedes_run_id": envelope.runtime_context.supersedes_run_id,
            "parent_plan_revision_id": envelope.runtime_context.parent_plan_revision_id,
            "actor_id": "canon-graph",
            "preceding_chapter_id": None,
            "preceding_accepted_chapter_revision_id": envelope.predecessor_accepted_chapter_revision_id,
            "entry_handoff_id": envelope.entry_handoff_id,
            "entry_source_chapter_revision_id": envelope.entry_source_chapter_revision_id,
            "entry_handoff_chain_hash": envelope.entry_handoff_chain_hash,
            "base_scene_revision_id": envelope.base_scene_revision_id,
            "base_chapter_revision_id": envelope.base_chapter_revision_id,
            "accepted_scene_revision_id": envelope.accepted_scene_revision_id,
            "accepted_chapter_revision_id": envelope.accepted_chapter_revision_id,
            "plan_revision_id": envelope.plan_revision_id,
            "canon_scope": envelope.canon_scope,
            "decision_target": envelope.runtime_context.decision_target,
            "context_source_refs": [],
            "author_decision": None,
            "idempotency_key": envelope.runtime_context.thread_id,
            "expected_run_version": None,
        }
        return ctx

    def _current_envelope(self) -> AgentInputEnvelope:
        """返回当前绑定的输入信封。"""
        if self._bound_envelope is None:
            raise AppError("RUN_STATE_CONFLICT", "no envelope bound to the graph invocation")
        return self._bound_envelope

    def invoke(
        self,
        state: ChapterRunState,
        envelope: AgentInputEnvelope,
        thread_id: str,
        resume: dict | None = None,
    ) -> dict:
        """在编译图上执行一次运行，支持首次启动或断点续跑。"""
        self._bound_envelope = envelope
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        try:
            if resume is not None:
                return self._compiled.invoke(Command(resume=resume), config)
            return self._compiled.invoke(state, config)
        finally:
            self._bound_envelope = None

    def get_state(self, thread_id: str) -> StateSnapshot:
        """读取指定 `thread_id` 的当前 checkpoint 快照。"""
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        return self._compiled.get_state(config)

    def step(
        self,
        state: ChapterRunState,
        envelope: AgentInputEnvelope,
        node: str,
    ) -> dict:
        """单步驱动适配器，供逐节点驱动图的调用方使用。"""
        if node == _CANON_AGENT:
            return self._canon(state, envelope)
        raise AppError("RUN_STATE_CONFLICT", f"unknown graph node: {node}")
