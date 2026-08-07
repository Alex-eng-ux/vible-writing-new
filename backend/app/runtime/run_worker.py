"""Worker 运行循环：领取 queued 运行、执行图并持久化结果（Task 9 生产 wiring）。

职责（不修改任何图/领域/API 契约）：
- ``RunWorker.tick`` 扫描 ``status='queued'`` 的运行（``FOR UPDATE SKIP LOCKED``
  领取，避免并发重复执行），经 ``RunExecutor``（可选观测装配）执行对应图
  （场景/章节/Canon，Fake model 语义），再把结果映射为运行状态与事件并提交；
- 执行失败 fail-closed：整个事务回滚（含租约与图写入），随后在独立事务把运行
  置为 ``failed`` 并写入 ``run_failed`` 事件（``RUN_LEASE_LOST`` 不覆盖，交给
  持有租约的 worker）；
- 幂等与防重复：一次 tick 只处理一个运行；运行离开 ``queued`` 后不再被领取；
  outbox 投递状态不在本循环内消费（由 ``PostgresOutboxPublisher`` 独立处理）。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Literal, cast

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.canon_agent import CanonAgent
from app.agents.canon_graph import CanonGraph
from app.agents.chapter_aggregator import ChapterAggregator
from app.agents.chapter_graph import ChapterGraph
from app.agents.chapter_planner import ChapterPlannerAgent
from app.agents.chapter_review_agent import ChapterReviewAgent
from app.agents.continuity_agent import ContinuityAgent
from app.agents.graph import SceneGraph
from app.agents.hook_registry import HookRegistry
from app.agents.model_provider import ModelProvider
from app.agents.result_router import AgentResultRouter
from app.agents.review_agent import ReviewAgent
from app.agents.revision_agent import RevisionAgent
from app.agents.schemas import (
    AgentInputEnvelope,
    AuthorFeedback,
    ContextManifestEntry,
    RuntimeContext,
)
from app.agents.state import ChapterRunState
from app.agents.writing_agent import WritingAgent
from app.db.models import (
    Chapter,
    ChapterPlanDiscussionMessage,
    ChapterPlanProposal,
    ChapterPlanQuestion,
    ChapterPlanRevision,
    ChapterPlanRevisionLink,
    ChapterPlanSceneLink,
    GenerationRun,
    RunOutboxRecord,
    Scene,
    SceneRevision,
    Volume,
    utcnow,
)
from app.domain.chapters import persist_chapter_review_output
from app.domain.interfaces import CommandContext
from app.errors import AppError
from app.observability.wiring import ObservabilityWiring
from app.runtime.executor import RunExecutor
from app.runtime.leases import LeaseRepository
from app.runtime.outbox import PostgresRunOutbox
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_identity import RunIdentity
from app.services.canon_runs import handle_chapter_accepted_outbox
from app.services.generation_runs import persist_planner_output

logger = logging.getLogger(__name__)

# 图构造器：给定运行与会话返回可执行图（默认按运行类型选择三图之一）。
GraphBuilder = Callable[[GenerationRun, Session], Any]


class RunWorker:
    """Worker 运行循环：领取并执行 queued 运行（每运行独立事务）。"""

    _CONSUMER_RETRY_DELAY_SECONDS = 5

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        actor_id: str,
        worker_id: str | None = None,
        observability: ObservabilityWiring | None = None,
        graph_builder: GraphBuilder | None = None,
        provider: ModelProvider | None = None,
        auto_plan_execution: bool = True,
        process_queued_runs: bool = True,
    ) -> None:
        """构造运行循环。

        参数：session_factory 为会话工厂；actor_id 为操作者身份；worker_id 为
        worker 标识（缺省取 actor_id）；observability 为观测装配（自动埋点）；
        graph_builder 为图构造器（缺省按运行类型选择三图，Fake model 语义）；
        provider 为统一模型 Provider（可选）：注入后场景图 WritingAgent 经其
        调用真实模型，未注入时保持 Fake model 语义（默认测试不访问网络）。
        """
        self._factory = session_factory
        self._actor_id = actor_id
        self._worker_id = worker_id or actor_id
        self._observability = observability
        self._graph_builder = graph_builder or self._default_graph_builder
        self._provider = provider
        self._auto_plan_execution = auto_plan_execution
        self._process_queued_runs = process_queued_runs

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def tick(self) -> int:
        """处理一批 queued 运行（每运行一个独立事务），返回处理数量。

        返回：本次实际处理并提交的运行数量；无 queued 运行时返回 0。
        约束：并发 worker 通过行锁领取，同一运行只被一个 worker 处理一次。
        """
        # 先恢复 accepted plan 产生的场景队列；测试 Worker 可关闭该自动推进，
        # 让 Playwright 通过 fixture 明确控制手动场景运行的生命周期。
        if self._auto_plan_execution:
            self._consume_plan_outbox()
        # 章节接受只写入 outbox；在独立事务中消费并幂等创建章节 Canon 运行。
        self._consume_chapter_accepted_outbox()
        # outbox 只负责首次投递；后续场景由 accepted 状态恢复，避免 outbox 已消费后队列停滞。
        if self._auto_plan_execution:
            self._recover_accepted_plan_scene_queues()
        if not self._process_queued_runs:
            return 0
        processed = 0
        while True:
            run_id = self._peek_queued()
            if run_id is None:
                break
            with self._factory() as session:
                done = self._process_one(session, run_id)
            if not done:
                break
            processed += 1
        return processed

    def _recover_accepted_plan_scene_queues(self) -> int:
        """恢复 accepted plan 的下一个场景运行，严格保持计划顺序并可重复执行。"""
        created = 0
        with self._factory() as session:
            accepted_plans = session.execute(
                select(ChapterPlanRevisionLink)
                .order_by(ChapterPlanRevisionLink.chapter_id, ChapterPlanRevisionLink.plan_revision_id)
                .with_for_update(skip_locked=True)
            ).scalars().all()
            for accepted_link in accepted_plans:
                if self._ensure_next_scene_run(
                    session, accepted_link.chapter_id, accepted_link.plan_revision_id
                ):
                    created += 1
                elif self._plan_scenes_are_accepted(
                    session, accepted_link.chapter_id, accepted_link.plan_revision_id
                ):
                    # 场景队列完成后单独创建章节审校运行；该运行由 ChapterGraph
                    # 先聚合固定场景版本，再调用 ChapterReviewAgent。
                    self._ensure_chapter_review_run(
                        session, accepted_link.chapter_id, accepted_link.plan_revision_id
                    )
            session.commit()
        return created

    def _ensure_next_scene_run(self, session: Session, chapter_id: str, plan_revision_id: str) -> bool:
        """在单个 accepted plan 下最多创建一个当前可运行场景。"""
        links = session.execute(
            select(ChapterPlanSceneLink)
            .where(
                ChapterPlanSceneLink.chapter_id == chapter_id,
                ChapterPlanSceneLink.plan_revision_id == plan_revision_id,
            )
            .order_by(ChapterPlanSceneLink.sort_order)
            .with_for_update()
        ).scalars().all()
        for index, scene_link in enumerate(links):
            existing = session.execute(
                select(GenerationRun)
                .where(
                    GenerationRun.chapter_id == chapter_id,
                    GenerationRun.scene_id == scene_link.scene_id,
                    GenerationRun.plan_revision_id == plan_revision_id,
                )
                .order_by(GenerationRun.created_at.desc())
                .with_for_update()
            ).scalars().first()
            if existing is not None:
                if existing.status != "accepted":
                    return False
                continue
            if index > 0:
                previous = links[index - 1]
                previous_scene = session.get(Scene, previous.scene_id)
                previous_run = session.execute(
                    select(GenerationRun)
                    .where(
                        GenerationRun.chapter_id == chapter_id,
                        GenerationRun.scene_id == previous.scene_id,
                        GenerationRun.plan_revision_id == plan_revision_id,
                    )
                    .order_by(GenerationRun.created_at.desc())
                    .with_for_update()
                ).scalars().first()
                if (
                    previous_scene is None
                    or previous_scene.accepted_scene_revision_id is None
                    or previous_run is None
                    or previous_run.status != "accepted"
                ):
                    return False
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                return False
            volume = session.get(Volume, chapter.volume_id)
            scene = session.get(Scene, scene_link.scene_id)
            base_scene_revision_id = scene.accepted_scene_revision_id if scene is not None else None
            run = GenerationRun(
                project_id=volume.project_id if volume is not None else chapter.volume_id,
                chapter_id=chapter_id,
                scene_id=scene_link.scene_id,
                plan_revision_id=plan_revision_id,
                request_type="continue",
                decision_target="scene",
                status="queued",
                normalized_input={
                    "run_scope": "scene",
                    "request_type": "continue",
                    "decision_target": "scene",
                    "plan_revision_id": plan_revision_id,
                    "base_scene_revision_id": base_scene_revision_id,
                    "chapter_intent": chapter.chapter_intent or {},
                },
            )
            session.add(run)
            session.flush()
            PostgresRunEventStore(session).emit(
                run.id,
                "run_queued",
                {
                    "run_scope": "scene",
                    "request_type": "continue",
                    "plan_revision_id": plan_revision_id,
                    "base_scene_revision_id": base_scene_revision_id,
                },
                fencing_token=0,
            )
            PostgresRunOutbox(session).enqueue(
                {
                    "resource_type": "run",
                    "resource_id": run.id,
                    "payload_schema": "run-event.v1",
                    "payload": {"event_type": "run_queued", "run_id": run.id},
                    "producer_command_id": run.id,
                    "generation_run_id": run.id,
                },
                fencing_token=0,
            )
            return True
        return False

    def _plan_scenes_are_accepted(
        self, session: Session, chapter_id: str, plan_revision_id: str
    ) -> bool:
        """确认计划中的每个场景都已有 accepted revision 和 accepted scene run。"""
        links = session.execute(
            select(ChapterPlanSceneLink)
            .where(
                ChapterPlanSceneLink.chapter_id == chapter_id,
                ChapterPlanSceneLink.plan_revision_id == plan_revision_id,
            )
            .order_by(ChapterPlanSceneLink.sort_order)
        ).scalars().all()
        if not links:
            return False
        for link in links:
            scene = session.get(Scene, link.scene_id)
            run = session.execute(
                select(GenerationRun)
                .where(
                    GenerationRun.chapter_id == chapter_id,
                    GenerationRun.scene_id == link.scene_id,
                    GenerationRun.plan_revision_id == plan_revision_id,
                )
                .order_by(GenerationRun.created_at.desc())
            ).scalars().first()
            if scene is None or scene.accepted_scene_revision_id is None or run is None or run.status != "accepted":
                return False
        return True

    def _ensure_chapter_review_run(
        self, session: Session, chapter_id: str, plan_revision_id: str
    ) -> bool:
        """为已完成场景队列幂等创建章节级审校运行。"""
        existing = session.execute(
            select(GenerationRun)
            .where(
                GenerationRun.chapter_id == chapter_id,
                GenerationRun.scene_id.is_(None),
                GenerationRun.plan_revision_id == plan_revision_id,
                GenerationRun.request_type == "review",
                GenerationRun.decision_target == "chapter",
            )
            .order_by(GenerationRun.created_at.desc())
            .with_for_update()
        ).scalars().first()
        if existing is not None:
            return False
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            return False
        volume = session.get(Volume, chapter.volume_id)
        run = GenerationRun(
            project_id=volume.project_id if volume is not None else chapter.volume_id,
            chapter_id=chapter_id,
            plan_revision_id=plan_revision_id,
            request_type="review",
            decision_target="chapter",
            status="queued",
            normalized_input={
                "run_scope": "chapter",
                "request_type": "review",
                "decision_target": "chapter",
                "plan_revision_id": plan_revision_id,
                "base_chapter_revision_id": chapter.accepted_chapter_revision_id,
                "chapter_intent": chapter.chapter_intent or {},
            },
        )
        session.add(run)
        session.flush()
        PostgresRunEventStore(session).emit(
            run.id,
            "run_queued",
            {
                "run_scope": "chapter",
                "request_type": "review",
                "decision_target": "chapter",
                "plan_revision_id": plan_revision_id,
            },
            fencing_token=0,
        )
        PostgresRunOutbox(session).enqueue(
            {
                "resource_type": "run",
                "resource_id": run.id,
                "payload_schema": "run-event.v1",
                "payload": {"event_type": "run_queued", "run_id": run.id},
                "producer_command_id": run.id,
                "generation_run_id": run.id,
            },
            fencing_token=0,
        )
        return True

    def _consume_plan_outbox(self) -> int:
        """消费 accepted plan 事件并恢复第一个未完成场景。

        outbox 记录本身是可重放的；场景运行通过固定 `(plan_revision_id, scene_id)`
        查重，因此 Worker 重启或重复投递不会创建第二个场景运行。
        """
        consumed = 0
        with self._factory() as session:
            rows = session.execute(
                select(RunOutboxRecord)
                .where(
                    RunOutboxRecord.resource_type == "chapter_plan",
                    RunOutboxRecord.delivery_status.in_(("pending", "publishing", "published", "consumed")),
                )
                .order_by(RunOutboxRecord.created_at)
                .with_for_update(skip_locked=True)
            ).scalars().all()
            for record in rows:
                payload = record.payload or {}
                if payload.get("event_type") != "chapter_plan.accepted":
                    continue
                chapter_id = payload.get("chapter_id")
                plan_revision_id = payload.get("plan_revision_id")
                if not chapter_id or not plan_revision_id:
                    record.delivery_status = "failed"
                    record.last_error = "invalid chapter_plan.accepted payload"
                    continue
                accepted_link = session.execute(
                    select(ChapterPlanRevisionLink)
                    .where(ChapterPlanRevisionLink.chapter_id == chapter_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if accepted_link is None or accepted_link.plan_revision_id != plan_revision_id:
                    record.delivery_status = "failed"
                    record.last_error = "accepted plan pointer does not match outbox payload"
                    continue
                # accepted-plan 重放复用直接入队的顺序与基线校验，避免旁路创建后续场景。
                self._ensure_next_scene_run(session, chapter_id, plan_revision_id)
                record.delivery_status = "consumed"
                consumed += 1
            session.commit()
        return consumed

    def _consume_chapter_accepted_outbox(self) -> int:
        """消费章节接受事件，并将对应 outbox 记录置为 consumed。

        `handle_chapter_accepted_outbox` 在同一事务内使用 advisory lock 和
        `(chapter_id, accepted_revision_id)` 幂等键创建 Canon 运行；重复 tick 或
        Worker 重启会再次进入该消费者，但不会产生第二个 Canon 运行。
        """
        consumed = 0
        with self._factory() as session:
            rows = session.execute(
                select(RunOutboxRecord)
                .where(
                    RunOutboxRecord.resource_type == "chapter_revision",
                    RunOutboxRecord.delivery_status.in_(
                        ("pending", "publishing", "published", "failed")
                    ),
                    or_(
                        RunOutboxRecord.next_attempt_at.is_(None),
                        RunOutboxRecord.next_attempt_at <= utcnow(),
                    ),
                )
                .order_by(RunOutboxRecord.created_at)
                .with_for_update(skip_locked=True)
            ).scalars().all()
            for record in rows:
                payload = record.payload or {}
                if payload.get("event_type") != "chapter_revision.accepted":
                    continue
                if record.payload_schema != "canon-auto.v1":
                    record.delivery_status = "failed"
                    record.last_error = "invalid chapter_revision.accepted payload schema"
                    continue
                chapter_id = payload.get("chapter_id")
                accepted_revision_id = payload.get("accepted_chapter_revision_id")
                if not chapter_id or not accepted_revision_id:
                    record.delivery_status = "failed"
                    record.last_error = "invalid chapter_revision.accepted payload"
                    continue
                if accepted_revision_id != record.resource_id:
                    record.delivery_status = "failed"
                    record.last_error = "accepted revision does not match outbox resource"
                    continue
                try:
                    # 单条事件使用 savepoint 隔离；handler 失败时保留原状态，
                    # 让下一次 tick 可以重放，而不阻断同一批次的其他事件。
                    with session.begin_nested():
                        handle_chapter_accepted_outbox(session, payload)
                except Exception as exc:  # noqa: BLE001 - 单条事件失败不得终止 Worker
                    record.delivery_status = "failed"
                    record.attempt_count += 1
                    record.next_attempt_at = utcnow() + timedelta(
                        seconds=self._CONSUMER_RETRY_DELAY_SECONDS
                    )
                    record.last_error = str(exc)[:2000]
                    continue
                record.delivery_status = "consumed"
                record.last_error = None
                record.next_attempt_at = None
                consumed += 1
            session.commit()
        return consumed

    def run_forever(self, interval: float = 1.0) -> None:
        """持续轮询执行（Worker 进程主循环）。

        参数：interval 为无待处理运行时的轮询间隔（秒）。
        副作用：进程存活期间持续执行；未处理完时立即继续（不等待）。
        """
        import time

        while True:
            try:
                processed = self.tick()
            except Exception:  # noqa: BLE001 - 基础设施短暂异常不得终止常驻 Worker
                logger.exception("RunWorker tick failed; retrying after interval")
                processed = 0
            if processed == 0:
                time.sleep(interval)

    # ------------------------------------------------------------------
    # 领取与执行
    # ------------------------------------------------------------------

    def _peek_queued(self) -> str | None:
        """非锁式窥探是否有 queued 运行（避免空事务）。"""
        with self._factory() as session:
            run_id = session.execute(
                select(GenerationRun.id)
                .where(GenerationRun.status == "queued")
                .order_by(GenerationRun.created_at)
                .limit(1)
            ).scalar_one_or_none()
        return run_id

    def _process_one(self, session: Session, run_id: str) -> bool:
        """领取并执行一次运行；返回是否处理成功（False 表示已被并发 worker 领取）。"""
        run = session.execute(
            select(GenerationRun)
            .where(GenerationRun.id == run_id, GenerationRun.status == "queued")
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if run is None:
            return False
        if run.scene_id is not None and run.plan_revision_id is not None and run.chapter_id is not None:
            accepted_link = session.execute(
                select(ChapterPlanRevisionLink)
                .where(ChapterPlanRevisionLink.chapter_id == run.chapter_id)
                .with_for_update()
            ).scalar_one_or_none()
            if accepted_link is None or accepted_link.plan_revision_id != run.plan_revision_id:
                # 计划替换后，旧 queued 场景 run 只能进入终态，不能领取租约或构建 graph。
                run.status = "superseded"
                run.run_version += 1
                run.last_error_code = "PLAN_REVISION_CONFLICT"
                session.flush()
                PostgresRunEventStore(session).emit(
                    run.id,
                    "run_superseded",
                    {"error_code": "PLAN_REVISION_CONFLICT"},
                    fencing_token=run.write_fencing_token,
                    producer_command_id=self._worker_id,
                )
                session.commit()
                return True
        identity = self._identity_for(run)
        leases = LeaseRepository(session)
        lease = leases.claim(identity, self._worker_id)
        run.status = "running"
        session.flush()
        try:
            graph = self._graph_builder(run, session)
            executor = RunExecutor(leases, graph, identity, observability=self._observability)
            state = self._state_for(run)
            envelope = self._build_envelope(session, run, lease)
            result = executor.execute(
                run.id,
                self._worker_id,
                lease["fencing_token"],
                lease["lease_token"],
                state,
                envelope,
            )
            planner_output = result.get("planner_output")
            if planner_output is not None and run.chapter_id and run.decision_target == "plan":
                from app.agents.schemas import ChapterPlanOutput

                persist_planner_output(session, run.id, ChapterPlanOutput(**planner_output), actor_id=self._actor_id)
            staged_revision_id = result.get("staged_chapter_revision_id")
            review_output = result.get("chapter_review_output")
            if staged_revision_id and review_output:
                from app.agents.schemas import ChapterReviewOutput

                # Review 写入必须携带完整 worker 身份上下文；领域函数当前只读取
                # generation_run_id，但省略其余字段会破坏 CommandContext 契约。
                review_ctx: CommandContext = {
                        "lease_context": {
                            "worker_id": self._worker_id,
                            "fencing_token": lease["fencing_token"],
                        },
                        "write_fence": {
                            "generation_run_id": run.id,
                            "owner_kind": "worker",
                            "owner_id": self._worker_id,
                            "fencing_token": lease["fencing_token"],
                        },
                        "generation_run_id": run.id,
                        "agent_run_id": f"{run.id}:a1",
                        "manual_command_id": None,
                        "source": "review",
                        "parent_generation_run_id": run.parent_generation_run_id,
                        "supersedes_run_id": run.supersedes_run_id,
                        "parent_plan_revision_id": run.parent_plan_revision_id,
                        "actor_id": self._actor_id,
                        "preceding_chapter_id": None,
                        "preceding_accepted_chapter_revision_id": None,
                        "entry_handoff_id": None,
                        "entry_source_chapter_revision_id": None,
                        "entry_handoff_chain_hash": None,
                        "base_scene_revision_id": None,
                        "base_chapter_revision_id": None,
                        "accepted_scene_revision_id": None,
                        "accepted_chapter_revision_id": None,
                        "plan_revision_id": run.plan_revision_id,
                        "canon_scope": None,
                        "decision_target": "chapter",
                        "context_source_refs": [],
                        "author_decision": None,
                        "idempotency_key": run.id,
                        "expected_run_version": run.run_version,
                }
                persist_chapter_review_output(
                    session,
                    staged_revision_id,
                    ChapterReviewOutput(**review_output),
                    review_ctx,
                )
            self._persist_outcome(session, run, result)
            session.commit()
        except Exception as exc:
            # fail-closed：回滚本次事务（含租约与图写入），再在独立事务落定终态。
            session.rollback()
            if isinstance(exc, AppError) and exc.code == "RUN_LEASE_LOST":
                # 租约已交给其他 worker：置技术暂停（可显式恢复），避免无限重试。
                self._mark_technical_pause(run_id, "RUN_LEASE_LOST")
                return True
            self._mark_failed(run_id, exc)
        return True

    # ------------------------------------------------------------------
    # 状态持久化
    # ------------------------------------------------------------------

    def _persist_outcome(self, session: Session, run: GenerationRun, result: dict) -> None:
        """把图执行结果映射为运行状态与事件（结果只含状态机字段，无正文）。"""
        status = result.get("run_status")
        last = result.get("last_durable_node")
        questions = list(result.get("clarification_questions") or [])
        pending_node = result.get("pending_node")
        event_type: str
        payload: dict[str, Any]
        run.last_durable_node = last
        run.pending_node = pending_node
        if status == "paused":
            if questions:
                run.status = "pending_clarification"
                run.clarification_questions = questions
                event_type, payload = "run_pending_clarification", {"questions": questions}
            else:
                run.status = "waiting_feedback"
                event_type, payload = "run_waiting_feedback", {"issues": []}
        elif status == "cancelled":
            run.status = "cancelled"
            event_type, payload = "run_cancelled", {}
        elif status == "failed":
            run.status = "failed"
            run.last_error_code = result.get("error_code") or "INTERNAL_ERROR"
            event_type, payload = "run_failed", {"error_code": run.last_error_code}
        else:
            # 图正常结束但未暂停（当前 Fake 图不会出现）：按等待作者处理，
            # 不把中间 Agent 输出伪造成已接受的终态。
            run.status = "waiting_feedback"
            run.pending_node = pending_node or last
            event_type, payload = "run_waiting_feedback", {"issues": []}
        session.flush()
        PostgresRunEventStore(session).emit(
            run.id,
            event_type,
            payload,
            fencing_token=run.write_fencing_token,
            producer_command_id=self._worker_id,
        )

    def _mark_technical_pause(self, run_id: str, error_code: str) -> None:
        """租约丢失等临时故障：置技术暂停（可显式恢复），避免无限重试。"""
        with self._factory() as session:
            run = session.execute(
                select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
            ).scalar_one_or_none()
            if run is None or run.status != "queued":
                return
            run.status = "paused"
            run.pause_reason = "technical"
            run.last_error_code = error_code
            session.flush()
            PostgresRunEventStore(session).emit(
                run_id,
                "run_paused",
                {"reason": "technical"},
                fencing_token=run.write_fencing_token,
                producer_command_id=self._worker_id,
            )
            session.commit()

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        """在独立事务把运行置为 failed 并写入 run_failed 事件（不覆盖已领走运行）。"""
        code = getattr(exc, "code", None)
        error_code = code if isinstance(code, str) and code else "INTERNAL_ERROR"
        with self._factory() as session:
            run = session.execute(
                select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
            ).scalar_one_or_none()
            if run is None or run.status not in ("queued", "running"):
                return
            run.status = "failed"
            run.last_error_code = error_code
            session.flush()
            PostgresRunEventStore(session).emit(
                run_id,
                "run_failed",
                {"error_code": error_code},
                fencing_token=run.write_fencing_token,
                producer_command_id=self._worker_id,
            )
            session.commit()

    # ------------------------------------------------------------------
    # 输入构建
    # ------------------------------------------------------------------

    def _identity_for(self, run: GenerationRun) -> RunIdentity:
        """由运行行构造稳定运行身份（供租约领取与执行器校验）。"""
        return RunIdentity(
            generation_run_id=run.id,
            agent_run_id=f"{run.id}:a1",
            agent_attempt_key="1",
            parent_generation_run_id=run.parent_generation_run_id,
            supersedes_run_id=run.supersedes_run_id,
            parent_plan_revision_id=run.parent_plan_revision_id,
        )

    def _state_for(self, run: GenerationRun) -> ChapterRunState:
        """构造初始运行状态（只引用持久化身份，不复制正文）。"""
        return ChapterRunState(
            generation_run_id=run.id,
            run_version=run.run_version,
            project_id=run.project_id,
            chapter_id=run.chapter_id,
            scene_id=run.scene_id,
        )

    def _build_envelope(self, session: Session, run: GenerationRun, lease: dict) -> AgentInputEnvelope:
        """由持久化运行输入重建 Agent 输入信封（最小上下文，Fake model 语义）。

        只读取规范化输入与场景/章节已接受版本指针，绝不读取客户端请求；重试
        绝不重新读取客户端输入。
        """
        ni = run.normalized_input or {}
        chapter_intent = ni.get("chapter_intent") or {}
        if run.chapter_id:
            chapter = session.get(Chapter, run.chapter_id)
            if chapter is not None:
                persisted_intent = chapter.chapter_intent or {}
                # 旧初始化数据可能只有占位键；只有包含自然语言 text 才作为 Planner 意图。
                if not chapter_intent.get("text") and persisted_intent.get("text"):
                    chapter_intent = persisted_intent
        scene_brief: dict = {}
        accepted_text = ""
        accepted_scene_revision_id: str | None = None
        if run.scene_id:
            scene = session.get(Scene, run.scene_id)
            if scene is not None:
                persisted_base = scene.accepted_scene_revision_id
                expected_base = ni.get("base_scene_revision_id")
                if expected_base != persisted_base:
                    raise AppError("SCENE_STALE", "scene baseline is stale; refresh and retry")
                accepted_scene_revision_id = persisted_base
                if persisted_base:
                    rev = session.get(SceneRevision, persisted_base)
                    if rev is not None and isinstance(rev.content, str):
                        accepted_text = rev.content
                # accepted plan 的 scene brief 是运行创建时的固定输入，不能随旧 Scene 编辑变化。
                scene_brief = {} if run.plan_revision_id else (scene.scene_brief or {})
                if run.plan_revision_id:
                    plan = session.get(ChapterPlanRevision, run.plan_revision_id)
                    link = session.execute(
                        select(ChapterPlanSceneLink).where(
                            ChapterPlanSceneLink.plan_revision_id == run.plan_revision_id,
                            ChapterPlanSceneLink.scene_id == run.scene_id,
                        )
                    ).scalar_one_or_none()
                    specs = (plan.scene_briefs if plan is not None else None) or (
                        (plan.chapter_contract or {}).get("scenes", []) if plan is not None else []
                    )
                    if link is not None:
                        fixed_spec = next(
                            (item for item in specs if item.get("client_key") == link.client_key),
                            None,
                        )
                        if fixed_spec is not None:
                            scene_brief = fixed_spec.get("scene_brief", fixed_spec.get("brief", {})) or {}
        accepted_chapter_revision_id: str | None = None
        if run.decision_target == "canon" and run.canon_source_revision_id:
            if run.scene_id:
                accepted_scene_revision_id = run.canon_source_revision_id
            else:
                accepted_chapter_revision_id = run.canon_source_revision_id
        base_scene_revision_id = ni.get("base_scene_revision_id")
        chapter_contract = self._chapter_contract_for(session, run)
        is_planner_run = run.chapter_id is not None and run.decision_target == "plan"
        lineage = self._planning_lineage_for(session, run) if is_planner_run else None
        discussion: list[dict] = []
        questions: list[dict] = []
        proposals: list[dict] = []
        if is_planner_run and lineage:
            discussion = [
                {"role": row.role, "kind": row.kind, "text": row.text, "source_run_id": row.source_run_id}
                for row in session.execute(
                    select(ChapterPlanDiscussionMessage)
                    .where(ChapterPlanDiscussionMessage.planning_lineage_id == lineage)
                    .order_by(ChapterPlanDiscussionMessage.message_sequence)
                ).scalars()
            ]
            questions = [
                {"question_id": row.question_id, "text": row.text, "impact": row.impact, "status": row.status}
                for row in session.execute(
                    select(ChapterPlanQuestion)
                    .where(ChapterPlanQuestion.planning_lineage_id == lineage, ChapterPlanQuestion.status == "pending")
                    .order_by(ChapterPlanQuestion.created_at)
                ).scalars()
            ]
            proposals = [
                {"proposal_id": row.proposal_id, "field_path": row.field_path, "value": row.value, "source": row.source, "status": row.status}
                for row in session.execute(
                    select(ChapterPlanProposal)
                    .where(ChapterPlanProposal.planning_lineage_id == lineage, ChapterPlanProposal.status == "pending")
                    .order_by(ChapterPlanProposal.created_at)
                ).scalars()
            ]
        manifest = [
            ContextManifestEntry(source_id=rid, kind="revision", revision_id=rid)
            for rid in dict.fromkeys([base_scene_revision_id, accepted_scene_revision_id])
            if rid
        ]
        feedback = ni.get("author_feedback")
        author_feedback = (
            AuthorFeedback(**feedback) if isinstance(feedback, dict) else AuthorFeedback()
        )
        runtime = RuntimeContext(
            generation_run_id=run.id,
            agent_run_id=f"{run.id}:a1",
            agent_attempt_key="1",
            thread_id=run.id,
            chapter_id=run.chapter_id,
            scene_id=run.scene_id,
            parent_generation_run_id=run.parent_generation_run_id,
            supersedes_run_id=run.supersedes_run_id,
            parent_plan_revision_id=run.parent_plan_revision_id,
            run_scope="chapter" if (run.chapter_id and not run.scene_id) else "scene",
            decision_target=cast(Literal["plan", "scene", "chapter", "canon"] | None, run.decision_target),
        )
        return AgentInputEnvelope(
            project={"id": run.project_id},
            runtime_context=runtime,
            scene_brief=scene_brief,
            request_type=cast(Literal["new_chapter", "continue", "rewrite", "review"], run.request_type or "continue"),
            base_scene_revision_id=base_scene_revision_id,
            base_chapter_revision_id=ni.get("base_chapter_revision_id"),
            plan_revision_id=run.plan_revision_id or ni.get("plan_revision_id"),
            accepted_scene_revision_id=accepted_scene_revision_id,
            accepted_chapter_revision_id=accepted_chapter_revision_id,
            chapter_contract=chapter_contract,
            chapter_intent=chapter_intent if is_planner_run else {},
            plan_discussion=discussion if is_planner_run else [],
            pending_plan_questions=questions if is_planner_run else [],
            pending_plan_proposals=proposals if is_planner_run else [],
            canon_scope=(
                ("scene" if run.scene_id else "chapter")
                if run.decision_target == "canon"
                else None
            ),
            context_manifest=manifest,
            accepted_text=accepted_text,
            author_feedback=author_feedback,
            lease_worker_id=self._worker_id,
            lease_fencing_token=lease["fencing_token"],
            write_fence_owner_kind="worker",
            write_fence_owner_id=self._worker_id,
            write_fence_fencing_token=lease["fencing_token"],
        )

    def _planning_lineage_for(self, session: Session, run: GenerationRun) -> str | None:
        """从持久化计划血缘恢复 Planner 讨论上下文。"""
        value = (run.normalized_input or {}).get("planning_lineage_id")
        if value:
            return str(value)
        if run.parent_plan_revision_id:
            parent = session.get(ChapterPlanRevision, run.parent_plan_revision_id)
            if parent is not None and parent.planning_lineage_id:
                return parent.planning_lineage_id
            return run.parent_plan_revision_id
        candidate = session.execute(
            select(ChapterPlanRevision)
            .where(ChapterPlanRevision.source_run_id == run.id)
            .limit(1)
        ).scalar_one_or_none()
        return candidate.planning_lineage_id if candidate else run.id

    def _chapter_contract_for(self, session: Session, run: GenerationRun) -> dict:
        """由已接受章节计划修订源取章节契约（供 ChapterPlanner/Review 使用）。

        优先取 `run.plan_revision_id` 指向的计划修订；否则回退到该章节当前
        已接受的计划修订（`ChapterPlanRevisionLink`）。无章节或无可接受计划时
        返回空 dict 让 planner 进入 needs_clarification（缺契约语义，不触达模型）。
        """
        if run.chapter_id is None:
            return {}
        plan_revision_id = run.plan_revision_id or (run.normalized_input or {}).get("plan_revision_id")
        plan = None
        if plan_revision_id:
            plan = session.get(ChapterPlanRevision, plan_revision_id)
        if plan is None or plan.chapter_id != run.chapter_id:
            link = session.execute(
                select(ChapterPlanRevisionLink).where(
                    ChapterPlanRevisionLink.chapter_id == run.chapter_id
                )
            ).scalar_one_or_none()
            if link is None:
                return {}
            plan = session.get(ChapterPlanRevision, link.plan_revision_id)
        if plan is None:
            return {}
        return plan.chapter_contract or {}

    def _default_graph_builder(self, run: GenerationRun, session: Session) -> Any:
        """按运行类型选择三图之一（未注入 provider 时 Fake model 语义；Canon 需要会话）。

        真实模型接线：注入 provider 时场景图 WritingAgent / ContinuityAgent /
        ReviewAgent / RevisionAgent 与章节图 ChapterPlannerAgent /
        ChapterReviewAgent 经其调用真实模型；Canon 分支在注入 provider 时
        CanonAgent 经其调用真实模型。章节聚合节点 `ChapterAggregator` 是领域服务
        （从库聚合，不调用模型），不接入 provider。
        """
        registry = HookRegistry()
        if run.decision_target == "canon":
            canon_kwargs: dict[str, Any] = {}
            if self._provider is not None:
                canon_kwargs["agent"] = CanonAgent(provider=self._provider)
            return CanonGraph(
                session=session,
                registry=registry,
                router=AgentResultRouter(),
                **canon_kwargs,
            )
        is_chapter = bool(run.chapter_id and not run.scene_id) or (run.normalized_input or {}).get(
            "run_scope"
        ) == "chapter"
        if is_chapter:
            chapter_kwargs: dict[str, Any] = {}
            chapter_kwargs["aggregator"] = ChapterAggregator(session)
            if self._provider is not None:
                chapter_kwargs["planner"] = ChapterPlannerAgent(provider=self._provider)
                chapter_kwargs["review"] = ChapterReviewAgent(provider=self._provider)
            return ChapterGraph(registry, AgentResultRouter(), **chapter_kwargs)
        kwargs: dict[str, Any] = {}
        if self._provider is not None:
            kwargs["writing"] = WritingAgent(provider=self._provider)
            kwargs["continuity"] = ContinuityAgent(provider=self._provider)
            kwargs["review"] = ReviewAgent(provider=self._provider)
            kwargs["revision"] = RevisionAgent(provider=self._provider)
        return SceneGraph(registry, AgentResultRouter(), **kwargs)


__all__ = ["RunWorker"]
