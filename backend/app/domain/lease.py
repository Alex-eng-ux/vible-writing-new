"""租约与写入 fencing 校验：防止过期写入者覆盖新的写入。

工作进程持有的租约与写入 fence 均绑定到生成运行（GenerationRun）的
当前 write_owner 与 write_fencing_token。任何写操作前必须校验令牌仍然
匹配，否则视为已被取代（RUN_LEASE_LOST）而拒绝写入，从而避免陈旧写入
覆盖新写入者（fencing）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import GenerationRun
from ..errors import AppError
from .interfaces import LeaseContext, RunWriteFence


def validate_lease(session: Session, lease: LeaseContext | None, generation_run_id: str) -> None:
    """校验工作进程租约是否匹配生成运行的写入所有者与令牌。

    参数：lease 为工作进程租约上下文；generation_run_id 为绑定的生成运行 id。

    失败条件（均抛 AppError）：
        - lease 缺失：RUN_LEASE_LOST。
        - 运行不存在：RUN_STATE_CONFLICT。
        - 所有者不匹配（非 worker 或 owner id 不符）：RUN_LEASE_LOST。
        - fencing token 过期（已被取代）：RUN_LEASE_LOST；调用方不得在过期
          令牌上写库。

    副作用：只读 GenerationRun，不写库。
    """
    if lease is None:
        raise AppError("RUN_LEASE_LOST", "a worker lease is required for this write")
    run = session.get(GenerationRun, generation_run_id)
    if run is None:
        raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
    if run.write_owner_kind != "worker" or run.write_owner_id != lease["worker_id"]:
        raise AppError("RUN_LEASE_LOST", "lease owner does not match the run write owner")
    if run.write_fencing_token != lease["fencing_token"]:
        raise AppError("RUN_LEASE_LOST", "stale fencing token; lease was superseded")


def validate_write_fence(session: Session, fence: RunWriteFence | None) -> None:
    """校验 RunWriteFence 是否匹配目标生成运行的所有者与令牌。

    参数：fence 为写入 fencing 令牌。

    失败条件（均抛 AppError）：
        - fence 缺失：RUN_LEASE_LOST。
        - 运行不存在：RUN_STATE_CONFLICT。
        - 所有者不匹配：RUN_LEASE_LOST。
        - fencing token 过期：RUN_LEASE_LOST，禁止写入。

    副作用：只读 GenerationRun，不写库。
    """
    if fence is None:
        raise AppError("RUN_LEASE_LOST", "a write fence is required for this write")
    run = session.get(GenerationRun, fence["generation_run_id"])
    if run is None:
        raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
    if run.write_owner_kind != fence["owner_kind"] or run.write_owner_id != fence["owner_id"]:
        raise AppError("RUN_LEASE_LOST", "write fence owner does not match the run write owner")
    if run.write_fencing_token != fence["fencing_token"]:
        raise AppError("RUN_LEASE_LOST", "stale write fence token; cannot write")


def claim_api_command_fence(
    session: Session,
    generation_run_id: str,
    manual_command_id: str,
    expected_run_version: int | None,
) -> RunWriteFence:
    """为一次作者（人工）命令领取目标运行的 API command fence。

    API command fence 不是 Worker 租约：它把运行写入所有者切换为
    `api_command` + `manual_command_id`，并推进单调递增的 fencing token。
    只有持有该 fence 的作者命令才能写入该运行（版本/决策/Canon）。

    参数：generation_run_id 为目标运行 id；manual_command_id 为服务端在幂等
    claim 时生成并持久化的作者命令 id；expected_run_version 为期望运行版本
    （当前实现仅透传，CAS 由调用方/上层完成）。
    返回：`RunWriteFence`（owner_kind=api_command）。

    失败条件：运行不存在抛 RUN_STATE_CONFLICT；manual_command_id 为空抛
    COMMAND_CONTEXT_MISMATCH。
    """
    if not manual_command_id:
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "manual_command_id is required for an api command fence",
        )
    run = session.get(GenerationRun, generation_run_id)
    if run is None:
        raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")
    new_token = (run.write_fencing_token or 0) + 1
    run.write_owner_kind = "api_command"
    run.write_owner_id = manual_command_id
    run.write_fencing_token = new_token
    session.flush()
    return {
        "generation_run_id": generation_run_id,
        "owner_kind": "api_command",
        "owner_id": manual_command_id,
        "fencing_token": new_token,
    }


class SqlRunWriteFencePort:
    """`RunWriteFencePort` 的 SQLAlchemy 实现：领取并校验 API command fence。

    供 Canon 分支作者确认提交等需要 API command fence 的领域/图路径使用；
    `validate` 委托 `validate_write_fence`（fail-closed）。
    """

    def __init__(self, session: Session) -> None:
        """用当前会话构造端口；后续领取/校验均基于该会话。"""
        self._session = session

    def claim_api_command(
        self, generation_run_id: str, manual_command_id: str, expected_run_version: int
    ) -> RunWriteFence:
        """领取 API command fence（见 `claim_api_command_fence`）。"""
        return claim_api_command_fence(
            self._session, generation_run_id, manual_command_id, expected_run_version
        )

    def validate(self, write_fence: RunWriteFence) -> None:
        """校验 fence 与运行所有者/令牌一致；失效返回 RUN_LEASE_LOST。"""
        validate_write_fence(self._session, write_fence)
