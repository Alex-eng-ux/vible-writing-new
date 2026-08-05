"""Task 9 Worker 运行循环测试（Fake model 语义）。

覆盖：领取 queued 运行并执行（观测事件齐备）、paused->waiting_feedback /
pending_clarification 映射、图异常 fail-closed 转 failed、RUN_LEASE_LOST 不覆盖、
重复 tick 不重复处理（防重复执行）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.agents.hook_registry import HookRegistry
from app.db.models import GenerationRun, RunEvent
from app.errors import AppError
from app.observability.sink import LocalSink
from app.observability.wiring import ObservabilityWiring
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_worker import RunWorker
from tests.acceptance.test_hashes import _hierarchy


@pytest.fixture(autouse=True)
def _cleanup_committed_runs(db):
    """清除此前 worker 测试提交到共享测试库的运行数据（保证测试隔离）。

    worker 使用独立会话提交，fixture 回滚无法撤销；每次测试前清理运行相关
    表，避免遗留 queued 运行被下一次 tick 误处理。
    """
    db.execute(text("DELETE FROM run_events"))
    db.execute(text("DELETE FROM run_leases"))
    db.execute(text("DELETE FROM generation_runs"))
    db.commit()
    yield


class _StubGraph:
    """可被观测包装的桩图（带 registry 属性与可注入结果/异常）。"""

    def __init__(self, result: dict, error: Exception | None = None) -> None:
        self._registry = HookRegistry()
        self._result = result
        self._error = error
        self.calls = 0

    @property
    def registry(self) -> HookRegistry:
        return self._registry

    def invoke(self, state, envelope, thread_id, resume=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return dict(self._result)


def _make_worker(db, graph: _StubGraph):
    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    wiring = ObservabilityWiring(sink=LocalSink(), environment="evaluation")
    return (
        RunWorker(
            factory,
            actor_id="worker-1",
            observability=wiring,
            graph_builder=lambda run, session: graph,
        ),
        wiring,
    )


def _create_queued_run(db, run_id: str = "g-w-1", request_type: str = "new_chapter"):
    project, scene = _hierarchy(db)
    run = GenerationRun(
        id=run_id,
        project_id=project.id,
        chapter_id=scene.chapter_id,
        scene_id=scene.id,
        status="queued",
        run_version=1,
        request_type=request_type,
        decision_target="scene",
        normalized_input={
            "run_scope": "scene",
            "request_type": request_type,
            "decision_target": "scene",
        },
    )
    db.add(run)
    db.flush()
    # 镜像真实 API 创建路径：run_queued 事件（fencing_token=0 与新建运行一致）。
    PostgresRunEventStore(db).emit(
        run_id, "run_queued", {"run_scope": "scene", "request_type": request_type}, fencing_token=0
    )
    db.commit()
    return run


def _events(db, run_id: str) -> list[str]:
    rows = db.execute(
        RunEvent.__table__.select()
        .where(RunEvent.__table__.c.generation_run_id == run_id)
        .order_by(RunEvent.__table__.c.sequence)
    ).all()
    return [r.event_type for r in rows]


def test_worker_executes_scene_run_and_persists_waiting_feedback(db) -> None:
    """paused 结果映射为 waiting_feedback + run_waiting_feedback 事件；观测齐备。"""
    run = _create_queued_run(db)
    graph = _StubGraph(
        {"run_status": "paused", "pending_node": "writing", "clarification_questions": [], "last_durable_node": "writing"}
    )
    worker, wiring = _make_worker(db, graph)

    assert worker.tick() == 1
    assert graph.calls == 1

    db.expire_all()
    row = db.get(GenerationRun, run.id)
    assert row.status == "waiting_feedback"
    assert row.pending_node == "writing"
    assert _events(db, run.id) == ["run_queued", "run_waiting_feedback"]

    # 观测：run_start / run_end 自动记录（节点 node_end 由真实图触发，见
    # tests/observability/test_production_wiring.py）。
    records = wiring.local.records if wiring.local is not None else []
    kinds = [r["kind"] for r in records]
    assert "run_start" in kinds and "run_end" in kinds
    assert any(r.get("generation_run_id") == run.id for r in records)


def test_worker_pending_clarification_mapping(db) -> None:
    """带澄清问题的 paused 结果映射为 pending_clarification。"""
    run = _create_queued_run(db, run_id="g-w-2")
    graph = _StubGraph(
        {
            "run_status": "paused",
            "pending_node": "scene_draft_review",
            "clarification_questions": ["请确认本场景的目标角色是谁"],
            "last_durable_node": "scene_draft_review",
        }
    )
    worker, _ = _make_worker(db, graph)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run.id)
    assert row.status == "pending_clarification"
    assert row.clarification_questions == ["请确认本场景的目标角色是谁"]
    assert _events(db, run.id) == ["run_queued", "run_pending_clarification"]


def test_worker_graph_error_marks_failed(db) -> None:
    """图异常：事务回滚后转 failed，写入 run_failed 事件并保留稳定错误码。"""
    run = _create_queued_run(db, run_id="g-w-3")
    graph = _StubGraph({}, error=AppError("RUN_STATE_CONFLICT", "boom"))
    worker, _ = _make_worker(db, graph)

    assert worker.tick() == 1  # 失败被吞并标记，不向上抛
    db.expire_all()
    row = db.get(GenerationRun, run.id)
    assert row.status == "failed"
    assert row.last_error_code == "RUN_STATE_CONFLICT"
    assert _events(db, run.id) == ["run_queued", "run_failed"]


def test_worker_lease_lost_marks_technical_pause(db) -> None:
    """RUN_LEASE_LOST：置技术暂停（可恢复），不覆盖且不无限重试。"""
    run = _create_queued_run(db, run_id="g-w-4")
    graph = _StubGraph({}, error=AppError("RUN_LEASE_LOST", "lease taken"))
    worker, _ = _make_worker(db, graph)

    assert worker.tick() == 1
    db.expire_all()
    row = db.get(GenerationRun, run.id)
    assert row.status == "paused"
    assert row.pause_reason == "technical"
    assert row.last_error_code == "RUN_LEASE_LOST"
    assert _events(db, run.id) == ["run_queued", "run_paused"]
    # 不再被重新领取（避免无限重试）。
    assert worker.tick() == 0


def test_worker_does_not_reprocess_completed_run(db) -> None:
    """运行离开 queued 后再次 tick 不重复执行（防重复命令执行）。"""
    run = _create_queued_run(db, run_id="g-w-5")
    graph = _StubGraph(
        {"run_status": "paused", "pending_node": "writing", "clarification_questions": [], "last_durable_node": "writing"}
    )
    worker, _ = _make_worker(db, graph)

    assert worker.tick() == 1
    assert worker.tick() == 0
    assert graph.calls == 1
    db.expire_all()
    assert db.get(GenerationRun, run.id).status == "waiting_feedback"
