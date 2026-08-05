"""运行时执行器：负责领取并执行某次运行的图。

本模块仅由 Worker 使用，通过 ``LeaseRepository`` 对租约做 fencing 校验，再将
运行状态输入 ``SceneGraph`` 执行。关键约束：
- 每个执行步骤都会校验 worker_id、lease_token 与单调递增的 fencing_token；
- 过期 worker / 过期 token / 被他人持有的租约都会抛出 ``RUN_LEASE_LOST`` 且不写
  任何数据（fail-closed）；
- 编译后的 LangGraph checkpoint 绑定到运行的 thread_id（即 generation_run_id），
  从而保证运行能从其 checkpoint 恢复。
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.agents.schemas import AgentInputEnvelope
from app.agents.state import ChapterRunState
from app.observability.wiring import ObservabilityWiring
from app.runtime.leases import LeaseRepository
from app.runtime.run_identity import RunIdentity


class RunExecutorPort(Protocol):
    """执行器对外端口，供依赖方做类型约束与测试替身。"""

    def claim(self, generation_run_id: str, worker_id: str) -> dict: ...
    def renew(self, generation_run_id: str, worker_id: str, fencing_token: int, lease_token: str) -> dict: ...
    def heartbeat(self, generation_run_id: str, worker_id: str, fencing_token: int, lease_token: str) -> None: ...
    def execute(self, generation_run_id: str, worker_id: str, fencing_token: int, lease_token: str) -> None: ...
    def reclaim_expired(self, now: datetime) -> int: ...


class GraphLike(Protocol):
    """可执行图协议：执行器只依赖该 invoke 签名。

    满足者：`SceneGraph`、`ChapterGraph`、`CanonGraph` 及观测包装器
    `GraphObservability`（均为 invoke(state, envelope, thread_id, resume=None)）。
    """

    def invoke(
        self,
        state: ChapterRunState,
        envelope: AgentInputEnvelope,
        thread_id: str,
        resume: dict | None = None,
    ) -> dict: ...


class RunExecutor:
    """领取并执行一次运行的图；仅由 Worker 使用。

    在每个步骤都校验 worker_id、lease_token 与单调递增的 fencing_token。
    过期 worker / 过期 token / 被他人持有的租约都会抛出 ``RUN_LEASE_LOST`` 且
    不写任何数据（fail-closed）。
    """

    def __init__(
        self,
        leases: LeaseRepository,
        graph: GraphLike,
        identity: RunIdentity,
        observability: ObservabilityWiring | None = None,
    ) -> None:
        """初始化执行器。

        参数:
            leases: 租约仓库，负责领取/续约/心跳/回收过期租约。
            graph: 待执行的可执行图（SceneGraph 等；须满足 invoke 签名）。
            identity: 本次运行的标识（RunIdentity）。
            observability: 可选的观测装配；传入时把图包上
                `GraphObservability` 并注册 `TraceHook`（幂等，见
                `ObservabilityWiring.traced`），自动记录 run_start / node_end /
                run_end / error；观测失败 fail-open，不影响业务结果与执行次数。
        """
        self._leases = leases
        self._identity = identity
        self._graph = (
            observability.traced(graph) if observability is not None else graph
        )

    def claim(self, generation_run_id: str, worker_id: str) -> dict:
        """为指定 worker 领取一次运行的租约。

        参数:
            generation_run_id: 目标运行 ID。
            worker_id: 领取租约的 worker ID。

        返回:
            租约信息字典，含 fencing_token 与 lease_token，供后续续约/执行使用。
        """
        return self._leases.claim(self._identity, worker_id)

    def renew(self, generation_run_id: str, worker_id: str, fencing_token: int, lease_token: str) -> dict:
        """续约租约以延长其有效期。

        参数:
            generation_run_id: 目标运行 ID。
            worker_id: worker ID。
            fencing_token: 当前 fencing token，须与租约一致且单调递增。
            lease_token: 租约 token，须与租约一致。

        返回:
            续约后的租约信息字典。

        失败条件: 租约缺失/过期/被他人持有（token 不匹配）时抛出 ``RUN_LEASE_LOST``。
        """
        return self._leases.renew(generation_run_id, worker_id, fencing_token, lease_token)

    def heartbeat(self, generation_run_id: str, worker_id: str, fencing_token: int, lease_token: str) -> None:
        """发送心跳以证明 worker 仍存活。

        参数:
            generation_run_id: 目标运行 ID。
            worker_id: worker ID。
            fencing_token: 当前 fencing token。
            lease_token: 租约 token。

        失败条件: 租约已丢失时抛出 ``RUN_LEASE_LOST``；心跳仅证明存活，不改变业务状态。
        """
        self._leases.heartbeat(generation_run_id, worker_id, fencing_token, lease_token)

    def reclaim_expired(self, now: datetime) -> int:
        """回收所有已过期租约（fail-closed 接管）。

        参数:
            now: 判定过期所依据的当前时间。

        返回:
            被回收（标记为过期）的租约数量。
        """
        return self._leases.reclaim_expired(now)

    def execute(
        self,
        generation_run_id: str,
        worker_id: str,
        fencing_token: int,
        lease_token: str,
        state: ChapterRunState,
        envelope: AgentInputEnvelope,
        resume: dict | None = None,
    ) -> dict:
        """在租约仍然有效的前提下执行图。

        参数:
            generation_run_id: 目标运行 ID，同时作为 LangGraph checkpoint 的 thread_id。
            worker_id: worker ID。
            fencing_token: 当前 fencing token。
            lease_token: 租约 token。
            state: 章节运行状态。
            envelope: 输入 agent 的信封。
            resume: 可选的恢复锚点，用于从 checkpoint 恢复执行。

        返回:
            图执行结果字典。

        失败条件: 执行前租约校验失败（过期/被他人持有）时抛出 ``RUN_LEASE_LOST``。
        约束: 执行前先续约校验，再将编译后的 checkpoint 绑定到 thread_id
        （即 generation_run_id）以从 checkpoint 恢复运行。
        """
        # Verify the lease is still valid before running any node.
        self._leases.renew(generation_run_id, worker_id, fencing_token, lease_token)
        # The compiled LangGraph checkpoint is bound to the run's thread_id
        # (the generation_run_id) so a run resumes from its checkpoint.
        thread_id = generation_run_id
        return self._graph.invoke(state, envelope, thread_id=thread_id, resume=resume)
