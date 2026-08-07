"""LangGraph 节点适配模块。

提供 `AgentCallable`：把单一 Agent 包装成图节点可调用的适配器，统一执行
生命周期钩子、schema 校验与结果路由。同时定义 `AgentRunner` 协议与
`node_output_check` 辅助函数，用于从 state 中安全读取节点输出。
"""

from __future__ import annotations

from typing import Protocol, cast

from pydantic import BaseModel

from app.agents.hook_registry import HookRegistry
from app.agents.result_router import AgentResultRouter
from app.agents.schemas import AgentInputEnvelope
from app.agents.state import ChapterRunState
from app.errors import AppError


class AgentRunner(Protocol):
    """Agent 运行协议：输入信封，返回结构化输出模型。"""

    def run(self, envelope: AgentInputEnvelope) -> BaseModel: ...


class AgentCallable:
    """把 Agent 包装为图节点可调用对象的适配器。

    职责：运行 Agent、执行 schema/生命周期钩子并路由结果。调用顺序为：
    before 钩子 -> Agent 运行 -> schema 校验 -> 路由 -> after 钩子。返回的
    字典包含输出、路由结果、待处理节点与澄清问题，供图节点写入 state。
    """

    def __init__(
        self,
        agent_type: str,
        agent: AgentRunner,
        registry: HookRegistry,
        router: AgentResultRouter,
    ) -> None:
        """构造 AgentCallable。

        参数：
            agent_type: Agent 类型标识（writing / continuity / review / revision）。
            agent: 实际执行的 Agent。
            registry: hook 注册表，提供生命周期钩子与 schema 校验。
            router: 结果路由器，决定输出后的去向。
        """
        self._agent_type = agent_type
        self._agent = agent
        self._registry = registry
        self._router = router

    def __call__(self, state: ChapterRunState, envelope: AgentInputEnvelope) -> dict:
        """执行 Agent 并返回标准化结果字典。

        参数：
            state: 当前运行状态（本实现未直接使用，保留以对齐图节点契约）。
            envelope: 输入信封。

        返回：包含 `output`、`outcome`、`pending_node`、
        `clarification_questions` 与 `last_durable_node` 的字典，供图节点写入
        并决定后续路由。

        失败条件：schema 校验失败或路由识别为需澄清但缺少问题时会抛出异常，
        由 `ErrorHook` 统一映射为稳定运行状态。
        """
        for hook in self._registry.lifecycle(self._agent_type):
            hook.before(self._agent_type, envelope)
        output = self._agent.run(envelope)
        self._registry.schema.validate(output)
        # Planner 专属结果校验必须发生在路由前，避免不完整候选被误判为可继续执行。
        for hook in self._registry.result_validators(self._agent_type):
            hook.validate(self._agent_type, output, envelope)
        outcome = self._router.route(output, self._agent_type, self._agent_type)
        for hook in self._registry.lifecycle(self._agent_type):
            hook.after(self._agent_type, output, outcome)
        return {
            "output": output,
            "outcome": outcome,
            "pending_node": outcome.pending_node,
            "clarification_questions": outcome.clarification_questions,
            "last_durable_node": self._agent_type,
        }


def node_output_check(state: ChapterRunState, field: str) -> BaseModel:
    """从 state 中安全读取指定字段的节点输出。

    参数：
        state: 当前运行状态。
        field: 要读取的字段名。

    返回：该字段对应的 `BaseModel` 输出。

    失败条件：字段缺失（为 None）时抛出 `RUN_STATE_CONFLICT` 错误，避免用
    空值继续后续节点。
    """
    value = state.get(field)
    if value is None:
        raise AppError("RUN_STATE_CONFLICT", f"missing node output: {field}")
    return cast(BaseModel, value)
