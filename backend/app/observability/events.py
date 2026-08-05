"""可观测性事件：Trace 端口与测试替身。

本模块定义运行时可观测性（trace）的事件结构与端口：
- ``TracePort`` 是 trace 输出端，失败为 fail-open，绝不改变业务结果；Task 8 提供
  LangSmith/本地 sink 实现；
- ``RunContext``/``NodeEvent``/``ErrorEvent``/``EndEvent`` 描述一次运行的可观测
  事件载荷；
- Task 8 向后兼容追加：``RunContext``/``NodeEvent``/``ErrorEvent`` 通过
  ``NotRequired`` 追加字段（Task 4A 消费者无需提供，读取不受影响）；新增
  ``RunEndEvent``/``RunFeedback`` 运行终态与作者反馈事件；
- ``FakeTracePort`` 是 Task 4A 测试用的内存端口，同样 fail-open。
"""
from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict


class RunContext(TypedDict):
    """一次运行的上下文定位信息。

    - generation_run_id: 生成运行 ID。
    - agent_run_id: agent 运行 ID。
    - agent_attempt_key: agent 尝试键。
    - project_id: 项目 ID。
    - scene_id: 场景 ID（可为空）。
    - chapter_id: 章节 ID（可为空）。
    - request_type/environment/input_manifest_id: Task 8 追加（NotRequired，
      尚未进入对应节点时可为空；input_manifest_id 只引用当前运行清单）。
    """

    generation_run_id: str
    agent_run_id: str
    agent_attempt_key: str
    project_id: str
    scene_id: str | None
    chapter_id: str | None
    request_type: NotRequired[str]
    environment: NotRequired[str]
    input_manifest_id: NotRequired[str | None]


class NodeEvent(TypedDict):
    """节点级可观测事件。

    - run_context: 运行上下文。
    - node: 节点名。
    - status: 节点状态。
    - duration_ms: 节点耗时（毫秒）。
    - Task 8 追加：input_revision_ids（节点实际读取的版本 ID 列表，可追溯到
      manifest）、output_summary（输出摘要，不保存完整 Prompt 或正文）、
      token_usage（token 摘要，缺失记 null）、started_at/ended_at、
      node_name/generation_run_id/agent_run_id 顶层便捷字段（NotRequired）。
    """

    run_context: NotRequired[RunContext]
    node: NotRequired[str]
    status: NotRequired[str]
    duration_ms: NotRequired[int]
    generation_run_id: NotRequired[str]
    agent_run_id: NotRequired[str]
    node_name: NotRequired[str]
    started_at: NotRequired[str]
    ended_at: NotRequired[str]
    input_revision_ids: NotRequired[list[str]]
    output_summary: NotRequired[dict | None]
    token_usage: NotRequired[dict | None]


class ErrorEvent(TypedDict):
    """错误级可观测事件。

    - run_context: 运行上下文。
    - error_code: 错误码（必须来自统一注册表）。
    - message: 错误信息。
    - Task 8 追加：retryable/degraded/created_at/generation_run_id/node_name
      （NotRequired）；degraded 表示观测或外部依赖降级，与业务失败分开记录。
    """

    run_context: NotRequired[RunContext]
    error_code: str
    message: NotRequired[str]
    generation_run_id: NotRequired[str]
    node_name: NotRequired[str]
    retryable: NotRequired[bool]
    degraded: NotRequired[bool]
    created_at: NotRequired[str]


class EndEvent(TypedDict):
    """运行结束事件。

    - run_context: 运行上下文。
    - run_status: 运行结束状态。
    - summary: 运行摘要。
    """

    run_context: RunContext
    run_status: str
    summary: dict


class RunEndEvent(TypedDict):
    """Task 8 运行终态事件（只记录已持久化的最终业务状态）。

    - status: 统一 run_status 枚举（accepted/cancelled/failed/superseded 等；
      completed 仅用于幂等记录），不把中间 Agent 输出当作正式决策；
    - final_decision: 最终作者决策（可为空）；
    - duration_ms: 运行总耗时（毫秒）；
    - token_usage: 模型/工具 token 摘要（缺失记 null，不伪造精确数值）；
    - degraded_observability: 观测链路是否降级（LangSmith 失败等）。
    """

    generation_run_id: str
    status: str
    final_decision: str | None
    duration_ms: int
    token_usage: dict | None
    degraded_observability: bool


class RunFeedback(TypedDict):
    """Task 8 作者反馈事件。

    - target: 反馈目标（scene/chapter/run）；
    - decision: 决策类型（accept/feedback/cancel 等）；
    - feedback_hash: 反馈内容哈希（默认不保存反馈正文，完整采集受环境开关限制）。
    """

    generation_run_id: str
    target: str
    decision: str
    feedback_hash: str
    created_at: str


class TracePort(Protocol):
    """可观测性 trace 输出端。

    失败为 fail-open，绝不改变业务结果。Task 8 提供 LangSmith/本地 sink。
    """

    def start(self, generation_run_id: str, agent_run_id: str, metadata: dict) -> None: ...
    def end(self, generation_run_id: str, agent_run_id: str, summary: dict) -> None: ...


class FakeTracePort:
    """Task 4A 测试用的内存 Trace 端口；fail-open。

    记录 start/end 调用，供测试断言 trace 行为；失败不影响业务结果。
    """

    def __init__(self) -> None:
        """初始化，清空 start/end 记录。"""
        self.starts: list[dict] = []
        self.ends: list[dict] = []

    def start(self, generation_run_id: str, agent_run_id: str, metadata: dict) -> None:
        """记录一次 trace 开始。

        参数:
            generation_run_id: 目标运行 ID。
            agent_run_id: agent 运行 ID。
            metadata: 附加元数据。

        副作用: 向 ``self.starts`` 追加一条记录。
        """
        self.starts.append({"generation_run_id": generation_run_id, "agent_run_id": agent_run_id, "metadata": metadata})

    def end(self, generation_run_id: str, agent_run_id: str, summary: dict) -> None:
        """记录一次 trace 结束。

        参数:
            generation_run_id: 目标运行 ID。
            agent_run_id: agent 运行 ID。
            summary: 运行摘要。

        副作用: 向 ``self.ends`` 追加一条记录。
        """
        self.ends.append({"generation_run_id": generation_run_id, "agent_run_id": agent_run_id, "summary": summary})
