"""运行时租约（lease）仓库：对 run_leases 表做 fenced 租约 CRUD。

租约是 Worker 独占某次运行写入权限的机制，核心约束：
- 每次 ``claim`` 用一个单调递增的 fencing_token 取代任何旧租约（旧 token 失效）；
- 所有写路径（claim/renew/heartbeat）都要校验 worker_id、lease_token、fencing_token
  与租约有效期，任何一项不匹配即视为租约丢失，抛出 ``RUN_LEASE_LOST`` 且不写数据
  （fail-closed）；
- ``reclaim_expired`` 对过期租约做 fail-closed 接管：只有 run 侧过期时间也过期时
  才标记租约过期并推进 fencing_token，失败的 worker 无法继续写入。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GenerationRun, RunLease, utcnow
from app.errors import AppError
from app.runtime.run_identity import RunIdentity


def _lease_token() -> str:
    """生成一个随机租约 token（16 字节十六进制），用于标识一次租约领取。"""
    return secrets.token_hex(16)


class LeaseRepository:
    """对 run_leases 表做 fenced 租约 CRUD。

    过期 worker / 过期 token / 过期租约 / 属主不匹配都会抛出 ``RUN_LEASE_LOST``
    且不得写入任何数据（fail-closed）。
    """

    def __init__(self, session: Session, lease_ttl_seconds: int = 300) -> None:
        """初始化租约仓库。

        参数:
            session: 数据库会话。
            lease_ttl_seconds: 租约有效期（秒），默认 300 秒。
        """
        self._session = session
        self._ttl = lease_ttl_seconds

    def claim(self, identity: RunIdentity, worker_id: str) -> dict:
        """为指定 worker 领取一次运行的租约。

        参数:
            identity: 运行标识（含 generation_run_id）。
            worker_id: 领取租约的 worker ID。

        返回:
            租约信息字典，含 fencing_token、lease_token 与 lease_expires_at。

        约束: 新的领取会用新的 fencing_token 取代任何已有租约（旧租约标记为
        superseded），并同步更新 run 上的租约属主与 fencing token。
        失败条件: 目标运行不存在时抛出 ``RUN_STATE_CONFLICT``。
        """
        generation_run_id = identity["generation_run_id"]
        run = self._session.get(GenerationRun, generation_run_id)
        if run is None:
            raise AppError("RUN_STATE_CONFLICT", "generation run does not exist")

        now = utcnow()
        # A fresh claim supersedes any existing lease with a new fencing token.
        new_token = (run.write_fencing_token or 0) + 1
        expires = now + timedelta(seconds=self._ttl)
        token = _lease_token()

        existing = self._session.execute(
            select(RunLease).where(RunLease.generation_run_id == generation_run_id)
        ).scalar_one_or_none()
        if existing is not None:
            existing.status = "superseded"
        row = RunLease(
            generation_run_id=generation_run_id,
            worker_id=worker_id,
            fencing_token=new_token,
            lease_token=token,
            lease_expires_at=expires,
            status="active",
        )
        self._session.add(row)
        run.lease_owner = worker_id
        run.lease_expires_at = expires
        run.write_owner_kind = "worker"
        run.write_owner_id = worker_id
        run.write_fencing_token = new_token
        self._session.flush()
        return {
            "generation_run_id": generation_run_id,
            "worker_id": worker_id,
            "fencing_token": new_token,
            "lease_token": token,
            "lease_expires_at": expires,
        }

    def renew(
        self,
        generation_run_id: str,
        worker_id: str,
        fencing_token: int,
        lease_token: str,
    ) -> dict:
        """续约租约以延长其有效期。

        参数:
            generation_run_id: 目标运行 ID。
            worker_id: worker ID。
            fencing_token: 当前 fencing token。
            lease_token: 租约 token。

        返回:
            续约后的租约信息字典。

        失败条件: 租约缺失/过期/被他人持有（任一校验不通过）时抛出
        ``RUN_LEASE_LOST``。
        """
        now = utcnow()
        lease = self._active_lease(generation_run_id, worker_id, fencing_token, lease_token)
        if lease is None:
            raise AppError("RUN_LEASE_LOST", "lease is missing, stale or owned by another worker")
        expires = now + timedelta(seconds=self._ttl)
        lease.lease_expires_at = expires
        run = self._session.get(GenerationRun, generation_run_id)
        if run is not None:
            run.lease_expires_at = expires
        self._session.flush()
        return {
            "generation_run_id": generation_run_id,
            "worker_id": worker_id,
            "fencing_token": fencing_token,
            "lease_token": lease_token,
            "lease_expires_at": expires,
        }

    def heartbeat(
        self,
        generation_run_id: str,
        worker_id: str,
        fencing_token: int,
        lease_token: str,
    ) -> None:
        """发送心跳以证明 worker 仍存活。

        参数:
            generation_run_id: 目标运行 ID。
            worker_id: worker ID。
            fencing_token: 当前 fencing token。
            lease_token: 租约 token。

        失败条件: 租约丢失时抛出 ``RUN_LEASE_LOST``。
        约束: 心跳仅证明存活，不改变业务状态（不延长租约，也不写业务数据）。
        """
        lease = self._active_lease(generation_run_id, worker_id, fencing_token, lease_token)
        if lease is None:
            raise AppError("RUN_LEASE_LOST", "heartbeat rejected: lease is lost")
        # Heartbeat only proves liveness; it does not change business state.
        self._session.flush()

    def reclaim_expired(self, now: datetime) -> int:
        """通过 fenced CAS 对过期租约做 fail-closed 接管；返回回收数量。

        参数:
            now: 判定过期所依据的当前时间。

        返回:
            被标记为过期的租约数量。

        约束: 仅当 run 侧 lease_expires_at 也早于 now（双重确认）时才接管，并将
        run 的 write_owner 清空、把 write_fencing_token 推进，使旧 worker 的后续
        写入因 token 不匹配而失败。
        """
        expired = self._session.execute(
            select(RunLease).where(
                RunLease.status == "active",
                RunLease.lease_expires_at.isnot(None),
                RunLease.lease_expires_at < now,
            )
        ).scalars().all()
        count = 0
        for lease in expired:
            run = self._session.get(GenerationRun, lease.generation_run_id)
            if run is not None and run.lease_expires_at is not None and run.lease_expires_at < now:
                lease.status = "expired"
                run.write_owner_kind = None
                run.write_owner_id = None
                run.write_fencing_token = (run.write_fencing_token or 0) + 1
                count += 1
        self._session.flush()
        return count

    def _active_lease(
        self,
        generation_run_id: str,
        worker_id: str,
        fencing_token: int,
        lease_token: str,
    ):
        """校验并返回当前有效租约；任一校验不通过则返回 None。

        参数:
            generation_run_id: 目标运行 ID。
            worker_id: worker ID。
            fencing_token: 当前 fencing token。
            lease_token: 租约 token。

        返回:
            通过全部校验的 ``RunLease``；租约缺失、worker/lease_token/fencing_token
            不匹配或已过期时返回 ``None``。
        """
        lease = self._session.execute(
            select(RunLease).where(
                RunLease.generation_run_id == generation_run_id,
                RunLease.status == "active",
            )
        ).scalar_one_or_none()
        if lease is None:
            return None
        if lease.worker_id != worker_id:
            return None
        if lease.lease_token != lease_token:
            return None
        if lease.fencing_token != fencing_token:
            return None
        if lease.lease_expires_at is not None and lease.lease_expires_at < utcnow():
            return None
        return lease
