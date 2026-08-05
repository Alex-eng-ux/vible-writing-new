from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import RunDecision
from ..domain.interfaces import CommandContext
from ..domain.story_bible import append_run_decision


class RunDecisionService:
    """将作者与 Canon 的决策保存为不可变记录。

    使用幂等键与运行版本防止重复或并发覆盖；相同的 (run, target, idempotency_key)
    只产生一个结果。
    """

    def __init__(self, session: Session) -> None:
        """初始化决策记录服务。

        参数：session 为数据库会话。
        副作用：持有会话引用，事务边界由调用方管理。
        """
        self._session = session

    def append(
        self,
        run_id: str,
        target: str,
        request_snapshot: dict,
        ctx: CommandContext,
    ) -> RunDecision:
        """追加一条不可变的运行决策记录并返回。

        参数：run_id 为生成运行 id；target 为决策目标；request_snapshot 为请求
        快照；ctx 为命令上下文。
        返回：新建的 RunDecision 记录。
        副作用：由领域函数 append_run_decision 追加并 flush 决策记录；须在已通过
        CommitGuard 的事务内调用。
        失败条件与幂等约束：同领域函数 append_run_decision——agent/review 来源
        的 ctx.generation_run_id 必须等于 run_id，author 来源必须携带指向该 run
        的 api_command write_fence 且不得携带 generation_run_id，否则抛
        COMMAND_CONTEXT_MISMATCH；相同的 (generation_run_id, target, idempotency_key)
        只产生一条记录。
        """
        return append_run_decision(self._session, run_id, target, request_snapshot, ctx)
