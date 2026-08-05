"""LangGraph 章节编排图模块。

`ChapterGraph` 是已编译的 LangGraph `StateGraph`，负责按
ChapterPlanner -> ChapterReview -> ChapterAggregator 的顺序编排章节级流程。
与 `SceneGraph` 一致，图只负责编排与状态流转，正式写入委托给领域服务；
节点通过 `AgentCallable` 调用各 Agent 并依据 `AgentResultRouter` 路由。图通过
`thread_id` 与 checkpointer 支持断点续跑，并通过 `interrupt` 在章节计划、
章节结果等节点等待作者决策。

Task 4B 边界：只追加章节规划/审校/聚合分支，不改写 Task 4A 的单场景状态、
Router 终态或运行身份字段。
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import StateSnapshot
from langgraph.types import Command, interrupt

from app.agents.chapter_aggregator import ChapterAggregator
from app.agents.chapter_planner import ChapterPlannerAgent
from app.agents.chapter_review_agent import ChapterReviewAgent
from app.agents.hook_registry import HookRegistry
from app.agents.nodes import AgentCallable
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope
from app.agents.state import ChapterRunState
from app.domain.interfaces import LeaseContext, RunWriteFence
from app.errors import AppError
from app.runtime.checkpointer import PostgresCheckpointer

_PLAN = "chapter_planner"
_REVIEW = "chapter_review"
_AGGREGATE = "chapter_aggregator"
_PAUSE = "pause_for_author"


class ChapterGraph:
    """已编译的 LangGraph 章节编排图。

    图结构：ChapterPlanner -> ChapterReview -> ChapterAggregator。

    该图是带 checkpointer 编译的真实 `StateGraph`，可通过 `thread_id` 从
    checkpoint 恢复运行。默认 checkpointer 是内存版 `MemorySaver`；生产环境
    传入基于 Postgres 的 checkpointer。章节审校分支不会调用 WritingAgent。
    """

    NODE_ORDER = [_PLAN, _REVIEW, _AGGREGATE]

    def __init__(
        self,
        registry: HookRegistry,
        router: AgentResultRouter,
        planner: ChapterPlannerAgent | None = None,
        review: ChapterReviewAgent | None = None,
        aggregator: ChapterAggregator | None = None,
        checkpointer: BaseCheckpointSaver | PostgresCheckpointer | None = None,
    ) -> None:
        """构造章节图并编译。

        参数：
            registry: hook 注册表，用于生命周期钩子与 schema 校验。
            router: 结果路由器，决定每个 Agent 输出后的去向。
            planner / review / aggregator: 各阶段 Agent，缺省时使用默认实现。
            checkpointer: 断点存储；为 None 时使用内存 `MemorySaver`。

        副作用：构建并编译最终的 StateGraph，保存到 `self._compiled`。
        """
        self._registry = registry
        self._router = router
        self._planner = AgentCallable("chapter_planner", planner or ChapterPlannerAgent(), registry, router)
        self._review = AgentCallable("chapter_review", review or ChapterReviewAgent(), registry, router)
        self._aggregator = aggregator
        self._checkpointer = self._resolve_checkpointer(checkpointer)
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
        graph.add_node(_PLAN, self._node(_PLAN))
        graph.add_node(_REVIEW, self._node(_REVIEW))
        graph.add_node(_AGGREGATE, self._aggregate_node)
        graph.add_node(_PAUSE, self._pause_node)

        graph.add_edge(START, _PLAN)
        graph.add_conditional_edges(
            _PLAN,
            self._route,
            {_REVIEW: _REVIEW, _PAUSE: _PAUSE, END: END},
        )
        graph.add_conditional_edges(
            _REVIEW,
            self._route,
            {_AGGREGATE: _AGGREGATE, _PAUSE: _PAUSE, END: END},
        )
        graph.add_conditional_edges(
            _AGGREGATE,
            self._route,
            {_PAUSE: _PAUSE, END: END},
        )
        graph.add_conditional_edges(
            _PAUSE,
            self._route_after_pause,
            {_PLAN: _PLAN, _REVIEW: _REVIEW, _AGGREGATE: _AGGREGATE, _PAUSE: _PAUSE, END: END},
        )
        return graph

    def _node(self, agent_type: str) -> Callable:
        """为指定 Agent 类型生成 LangGraph 节点执行函数（与 SceneGraph 同款）。"""
        callable_ = {
            _PLAN: self._planner,
            _REVIEW: self._review,
        }[agent_type]

        def run(state: ChapterRunState, config: RunnableConfig) -> dict:
            envelope = self._current_envelope()
            feedback = state.get("author_feedback")
            if feedback:
                envelope = envelope.model_copy(update={"author_feedback": feedback})
            result = callable_(state, envelope)
            state["last_durable_node"] = result.get("last_durable_node") or agent_type
            outcome = result.get("outcome")
            if outcome is not None and outcome.status in (
                "needs_clarification",
                "feedback",
                "cancel",
                "failed",
            ):
                state["pending_node"] = outcome.pending_node
                state["clarification_questions"] = outcome.clarification_questions
                state["run_status"] = "paused"
                return {
                    "pending_node": outcome.pending_node,
                    "clarification_questions": outcome.clarification_questions,
                    "run_status": "paused",
                    "last_durable_node": agent_type,
                }
            return {
                "last_durable_node": agent_type,
                "run_status": "running",
            }

        return run

    def _aggregate_node(self, state: ChapterRunState, config: RunnableConfig) -> dict:
        """章节聚合节点：调用 `ChapterAggregator` 完成聚合。

        聚合失败（如场景未接受、入口 stale）时置为 paused 等待作者反馈，
        不抛出未捕获异常。
        """
        envelope = self._current_envelope()
        chapter_id = envelope.runtime_context.chapter_id
        if self._aggregator is None or chapter_id is None:
            state["run_status"] = "paused"
            return {
                "last_durable_node": _AGGREGATE,
                "run_status": "paused",
                "pending_node": _AGGREGATE,
                "clarification_questions": ["aggregator not wired for this chapter"],
            }
        try:
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
            self._aggregator.aggregate(
                chapter_id,
                reason="chapter aggregation",
                ctx={
                    "lease_context": lease_context,
                    "write_fence": write_fence,
                    "generation_run_id": envelope.runtime_context.generation_run_id,
                    "agent_run_id": envelope.runtime_context.agent_run_id,
                    "manual_command_id": None,
                    "source": "agent",
                    "parent_generation_run_id": envelope.runtime_context.parent_generation_run_id,
                    "supersedes_run_id": envelope.runtime_context.supersedes_run_id,
                    "parent_plan_revision_id": envelope.runtime_context.parent_plan_revision_id,
                    "actor_id": "chapter-graph",
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
                },
            )
        except AppError as exc:
            state["run_status"] = "paused"
            return {
                "last_durable_node": _AGGREGATE,
                "run_status": "paused",
                "pending_node": _AGGREGATE,
                "clarification_questions": [exc.message],
                "error_code": exc.code,
            }
        return {
            "last_durable_node": _AGGREGATE,
            "run_status": "running",
        }

    def _route_after_pause(self, state: ChapterRunState) -> str:
        """暂停恢复后的路由：accept/feedback 回到 pending_node 继续执行。

        accept 后回到保存的 pending_node（last_durable_node）继续执行；feedback
        携带 AuthorFeedback 后重新执行原节点（pending_node）；cancel 进入 END。
        """
        action = state.get("_pause_action")
        if action == "cancel":
            return END
        if action == "accept":
            # accept 已保存 pending_node 到 last_durable_node，回到它继续执行。
            return state.get("last_durable_node") or END
        if action == "feedback":
            return state.get("pending_node") or END
        return END

    def _route(self, state: ChapterRunState) -> str:
        """边路由：当路由器决定暂停时，等待作者输入。"""
        if state.get("pending_node") is not None:
            return _PAUSE
        last = state.get("last_durable_node")
        if last == _PLAN:
            return _REVIEW
        if last == _REVIEW:
            return _AGGREGATE
        return END

    def _pause_node(self, state: ChapterRunState, config: RunnableConfig) -> dict:
        """等待作者的决策（feedback / clarification / cancel）。

        通过 `interrupt` 挂起并收集作者决策。accept 回到 pending_node 继续；
        feedback 保留 pending_node 继续暂停；cancel 结束运行。
        """
        questions = state.get("clarification_questions") or []
        pending_node = state.get("pending_node")
        decision = interrupt(
            {
                "pending_node": pending_node,
                "clarification_questions": questions,
            }
        )
        decision = decision or {}
        action = decision.get("action", "cancel")
        if action == "accept":
            # 保存 pending_node 后再清空，供 _route_after_pause 回到原节点。
            saved = pending_node or _AGGREGATE
            return {
                "pending_node": None,
                "clarification_questions": [],
                "run_status": "running",
                "last_durable_node": saved,
                "_pause_action": "accept",
            }
        if action == "feedback":
            # 携带 AuthorFeedback 写入 checkpoint，并重新执行原节点。
            feedback = decision.get("author_feedback") or {}
            return {
                "pending_node": pending_node,
                "clarification_questions": questions,
                "run_status": "running",
                "author_feedback": feedback,
                "_pause_action": "feedback",
            }
        if action == "cancel":
            return {
                "pending_node": None,
                "clarification_questions": [],
                "run_status": "cancelled",
                "last_durable_node": pending_node or _AGGREGATE,
                "_pause_action": "cancel",
            }
        raise AppError("COMMAND_CONTEXT_MISMATCH", f"unknown author decision: {action}")

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
        if node == _PLAN:
            return self._planner(state, envelope)
        if node == _REVIEW:
            return self._review(state, envelope)
        raise AppError("RUN_STATE_CONFLICT", f"unknown graph node: {node}")
