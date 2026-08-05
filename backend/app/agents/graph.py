"""LangGraph 单场景编排图模块。

本模块定义了 `SceneGraph`：一个已编译的 LangGraph `StateGraph`，负责按
Writing -> Continuity -> Review -> Revision 的顺序编排单场景的写作流程。
图的职责仅限于编排与状态流转，真正的持久化写入都委托给领域服务；图节点
只调用各 Agent 并依据 `AgentResultRouter` 的决策进行路由。图通过
`thread_id` 与 checkpointer 支持断点续跑（resume）。

LangGraph 图边界：
- 图节点名称取自 scene / revision / review / continuity 四类 Agent，外加一个
  作者暂停节点 `pause_for_author`。
- review 分支不会调用 WritingAgent；Revision 分支只生成 ChangeSet，不直接改文。
- 作者交互（feedback / clarification / cancel）通过 `interrupt` 暂停并由
  `_pause_node` 处理。
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import StateSnapshot
from langgraph.types import Command, interrupt

from app.agents.continuity_agent import ContinuityAgent
from app.agents.hook_registry import HookRegistry
from app.agents.nodes import AgentCallable
from app.agents.result_router import AgentResultRouter
from app.agents.review_agent import ReviewAgent
from app.agents.revision_agent import RevisionAgent
from app.agents.schemas import AgentInputEnvelope
from app.agents.state import ChapterRunState
from app.agents.writing_agent import WritingAgent
from app.errors import AppError
from app.runtime.checkpointer import PostgresCheckpointer

# Language of the graph node names kept in scene / revision / review / continuity.
_WRITING = "writing"
_CONTINUITY = "continuity"
_REVIEW = "review"
_REVISION = "revision"
_PAUSE = "pause_for_author"


class SceneGraph:
    """已编译的 LangGraph 单场景图。

    图结构：Writing -> Continuity -> Review -> Revision。

    该图是带 checkpointer 编译的真实 `StateGraph`，可通过 `thread_id` 从
    checkpoint 恢复运行。默认 checkpointer 是内存版 `MemorySaver`（用于快速
    单测）；生产环境传入基于 Postgres 的 checkpointer，使运行在 worker 重启后
    仍存活，且新 worker 可基于同一 `generation_run_id` 继续运行。review 分支
    永远不会调用 WritingAgent。图只负责编排，正式写入全部委托给领域服务。
    """

    NODE_ORDER = [_WRITING, _CONTINUITY, _REVIEW, _REVISION]

    def __init__(
        self,
        registry: HookRegistry,
        router: AgentResultRouter,
        writing: WritingAgent | None = None,
        continuity: ContinuityAgent | None = None,
        review: ReviewAgent | None = None,
        revision: RevisionAgent | None = None,
        checkpointer: BaseCheckpointSaver | PostgresCheckpointer | None = None,
    ) -> None:
        """构造图并编译。

        参数：
            registry: hook 注册表，用于生命周期钩子与 schema 校验。
            router: 结果路由器，决定每个 Agent 输出后的去向。
            writing / continuity / review / revision: 各阶段 Agent，缺省时
                使用默认实现；实际调用时会被包装为 `AgentCallable`。
            checkpointer: 断点存储；为 None 时使用内存 `MemorySaver`，传入
                `PostgresCheckpointer` 时取其底层 saver 使用。

        副作用：构建并编译最终的 StateGraph，保存到 `self._compiled`。
        """
        self._registry = registry
        self._router = router
        self._writing = AgentCallable("writing", writing or WritingAgent(), registry, router)
        self._continuity = AgentCallable(
            "continuity", continuity or ContinuityAgent(), registry, router
        )
        self._review = AgentCallable("review", review or ReviewAgent(), registry, router)
        self._revision = AgentCallable("revision", revision or RevisionAgent(), registry, router)
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
        """解析并归一化 checkpointer 供 LangGraph 编译使用。

        返回：
            - checkpointer 为 None 时返回内存 `MemorySaver`（单测/快速验证用）。
            - checkpointer 为 `PostgresCheckpointer` 时返回其底层 saver，使
              运行可持久化、重启后恢复。
            - 否则原样返回（已是 `BaseCheckpointSaver` 实例）。
        """
        if checkpointer is None:
            return MemorySaver()
        if isinstance(checkpointer, PostgresCheckpointer):
            return checkpointer.saver
        return checkpointer

    def _build(self) -> StateGraph:
        """构建未编译的 StateGraph：注册节点并定义边路由。

        节点包括 writing / continuity / review / revision 四个 Agent 节点
        （统一由 `_node` 包装），以及作者暂停节点 `_pause_node`。每个 Agent
        节点后都通过 `_route` 做条件路由，依据 `pending_node` 决定是继续、
        暂停交给作者，还是进入 END。
        """
        graph = StateGraph(ChapterRunState)

        graph.add_node(_WRITING, self._node(_WRITING))
        graph.add_node(_CONTINUITY, self._node(_CONTINUITY))
        graph.add_node(_REVIEW, self._node(_REVIEW))
        graph.add_node(_REVISION, self._node(_REVISION))
        graph.add_node(_PAUSE, self._pause_node)

        graph.add_edge(START, _WRITING)
        graph.add_conditional_edges(
            _WRITING,
            self._route,
            {_CONTINUITY: _CONTINUITY, _PAUSE: _PAUSE, END: END},
        )
        graph.add_conditional_edges(
            _CONTINUITY,
            self._route,
            {_REVIEW: _REVIEW, _PAUSE: _PAUSE, END: END},
        )
        graph.add_conditional_edges(
            _REVIEW,
            self._route,
            {_REVISION: _REVISION, _PAUSE: _PAUSE, END: END},
        )
        graph.add_conditional_edges(
            _REVISION,
            self._route,
            {_PAUSE: _PAUSE, END: END},
        )
        graph.add_edge(_PAUSE, END)
        return graph

    def _node(self, agent_type: str) -> Callable:
        """为指定 Agent 类型生成 LangGraph 节点执行函数。

        返回的闭包在执行时：
        1. 取当前绑定的输入信封（`_current_envelope`）。
        2. 调用对应的 `AgentCallable` 获得节点结果。
        3. 持久化 `last_durable_node` 供恢复使用；若结果处于需要澄清/反馈/
           取消/失败状态，则把 `pending_node` 与澄清问题写入 state，并把
           `run_status` 置为 `paused`，交由后续 `_route` 进入作者暂停节点。
        4. 否则维持 `run_status` 为 `running` 并继续向后流转。

        关键约束：`last_durable_node` 是恢复时的身份锚点，必须先于结果返回
        写入 state，保证 crash 后可从同一节点续跑。
        """
        callable_ = {
            _WRITING: self._writing,
            _CONTINUITY: self._continuity,
            _REVIEW: self._review,
            _REVISION: self._revision,
        }[agent_type]

        def run(state: ChapterRunState, config: RunnableConfig) -> dict:
            envelope = self._current_envelope()
            result = callable_(state, envelope)
            # Persist the durable node and the pending decision for recovery.
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

    def _route(self, state: ChapterRunState) -> str:
        """边路由：当路由器决定暂停时，等待作者输入。

        参数：
            state: 当前运行状态。

        返回：
            下一个节点名。若存在 `pending_node`（即需要作者澄清/反馈/取消），
            返回 `_PAUSE`；否则依据 `last_durable_node` 按下游顺序返回
            continuity / review / revision；无匹配时返回 END。
        """
        if state.get("pending_node") is not None:
            return _PAUSE
        last = state.get("last_durable_node")
        if last == _WRITING:
            return _CONTINUITY
        if last == _CONTINUITY:
            return _REVIEW
        if last == _REVIEW:
            return _REVISION
        return END

    def _pause_node(self, state: ChapterRunState, config: RunnableConfig) -> dict:
        """等待作者的决策（feedback / clarification / cancel）。

        通过 LangGraph 的 `interrupt` 挂起并收集作者决策。作者决策 `action`
        取值：
            - "accept"：确认接受，清空暂停状态并继续运行。
            - "feedback"：继续暂停，保留待处理节点与澄清问题。
            - "cancel"：取消运行，置 `run_status` 为 `cancelled`。
        未知决策会抛出 `COMMAND_CONTEXT_MISMATCH` 错误。

        返回：更新后的 state 片段，用于写入 checkpoint 以便恢复。
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
            return {
                "pending_node": None,
                "clarification_questions": [],
                "run_status": "running",
                "last_durable_node": pending_node or _REVISION,
            }
        if action == "feedback":
            return {
                "pending_node": pending_node,
                "clarification_questions": questions,
                "run_status": "paused",
            }
        if action == "cancel":
            return {
                "pending_node": None,
                "clarification_questions": [],
                "run_status": "cancelled",
                "last_durable_node": pending_node or _REVISION,
            }
        raise AppError("COMMAND_CONTEXT_MISMATCH", f"unknown author decision: {action}")

    def _current_envelope(self) -> AgentInputEnvelope:
        """返回当前绑定的输入信封。

        若未通过 `invoke` 绑定信封就调用，则抛出 `RUN_STATE_CONFLICT` 错误，
        避免在缺少上下文时执行节点。
        """
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
        """在编译图上执行一次运行，支持首次启动或断点续跑。

        参数：
            state: 初始运行状态（首次启动时传入）。
            envelope: 本次运行绑定的输入信封，供所有节点读取。
            thread_id: 运行线程标识，用于读写 checkpoint。
            resume: 非 None 时表示恢复运行，通过 `Command(resume=resume)` 继续
                之前被 `interrupt` 暂停的运行；否则走全新 `invoke`。

        副作用：在调用期间将信封绑定到 `self._bound_envelope`，调用结束后
        无论成功与否都会清除，避免泄漏到下一次调用。

        返回：编译图执行后的最终 state 字典。
        """
        self._bound_envelope = envelope
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        try:
            if resume is not None:
                return self._compiled.invoke(Command(resume=resume), config)
            return self._compiled.invoke(state, config)
        finally:
            self._bound_envelope = None

    def get_state(self, thread_id: str) -> StateSnapshot:
        """读取指定 `thread_id` 的当前 checkpoint 快照。

        返回：`StateSnapshot`，可用于查看运行状态与待恢复的暂停信息。
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        return self._compiled.get_state(config)

    def step(
        self,
        state: ChapterRunState,
        envelope: AgentInputEnvelope,
        node: str,
    ) -> dict:
        """单步驱动适配器，供逐节点驱动图的调用方使用。

        规范路径是在编译图上调用 `invoke`；`step` 仅用于对单个节点逐一
        测试的图测试。

        参数：
            state: 当前运行状态。
            envelope: 输入信封。
            node: 要执行的节点名（writing / continuity / review / revision）。

        返回：该节点 Agent 执行后的字典结果。

        失败条件：未知节点名抛出 `RUN_STATE_CONFLICT` 错误。
        """
        if node == _WRITING:
            return self._writing(state, envelope)
        if node == _CONTINUITY:
            return self._continuity(state, envelope)
        if node == _REVIEW:
            return self._review(state, envelope)
        if node == _REVISION:
            return self._revision(state, envelope)
        raise AppError("RUN_STATE_CONFLICT", f"unknown graph node: {node}")

    def next_node(self, leader: str, router_result: dict) -> str | None:
        """返回下一个节点，或返回 None 表示暂停/结束（仅供单步测试使用）。

        参数：
            leader: 当前节点名（本实现未使用，保留以对齐调用方契约）。
            router_result: 路由器结果，需包含 `outcome` 字段。

        返回：
            下一个节点名；当 `outcome` 缺失或状态为需要澄清/反馈/取消/失败时
            返回 None（表示暂停或结束）。
        """
        outcome = router_result.get("outcome")
        if outcome is None:
            return None
        if outcome.status in ("needs_clarification", "feedback", "cancel", "failed"):
            return None
        return outcome.next_node
