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

from collections.abc import Callable
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.canon_agent import CanonAgent
from app.agents.canon_graph import CanonGraph
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
    ChapterPlanRevision,
    ChapterPlanRevisionLink,
    GenerationRun,
    Scene,
    SceneRevision,
)
from app.errors import AppError
from app.observability.wiring import ObservabilityWiring
from app.runtime.executor import RunExecutor
from app.runtime.leases import LeaseRepository
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_identity import RunIdentity

# 图构造器：给定运行与会话返回可执行图（默认按运行类型选择三图之一）。
GraphBuilder = Callable[[GenerationRun, Session], Any]


class RunWorker:
    """Worker 运行循环：领取并执行 queued 运行（每运行独立事务）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        actor_id: str,
        worker_id: str | None = None,
        observability: ObservabilityWiring | None = None,
        graph_builder: GraphBuilder | None = None,
        provider: ModelProvider | None = None,
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

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def tick(self) -> int:
        """处理一批 queued 运行（每运行一个独立事务），返回处理数量。

        返回：本次实际处理并提交的运行数量；无 queued 运行时返回 0。
        约束：并发 worker 通过行锁领取，同一运行只被一个 worker 处理一次。
        """
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

    def run_forever(self, interval: float = 1.0) -> None:
        """持续轮询执行（Worker 进程主循环）。

        参数：interval 为无待处理运行时的轮询间隔（秒）。
        副作用：进程存活期间持续执行；未处理完时立即继续（不等待）。
        """
        import time

        while True:
            processed = self.tick()
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
        scene_brief: dict = {}
        accepted_text = ""
        accepted_scene_revision_id: str | None = None
        if run.scene_id:
            scene = session.get(Scene, run.scene_id)
            if scene is not None:
                scene_brief = scene.scene_brief or {}
                if scene.accepted_scene_revision_id:
                    accepted_scene_revision_id = scene.accepted_scene_revision_id
                    rev = session.get(SceneRevision, scene.accepted_scene_revision_id)
                    if rev is not None and isinstance(rev.content, str):
                        accepted_text = rev.content
        accepted_chapter_revision_id: str | None = None
        if run.decision_target == "canon" and run.canon_source_revision_id:
            if run.scene_id:
                accepted_scene_revision_id = run.canon_source_revision_id
            else:
                accepted_chapter_revision_id = run.canon_source_revision_id
        base_scene_revision_id = ni.get("base_scene_revision_id")
        chapter_contract = self._chapter_contract_for(session, run)
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
