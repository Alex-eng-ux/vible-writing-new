"""Task 4B 章节 Agent 测试：规划、审校与聚合。"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.chapter_aggregator import ChapterAggregator
from app.agents.chapter_planner import ChapterPlannerAgent
from app.agents.chapter_review_agent import ChapterReviewAgent
from app.agents.schemas import (
    AgentInputEnvelope,
    ChapterPlanOutput,
    ChapterReviewOutput,
    RuntimeContext,
)


def _envelope(**overrides: object) -> AgentInputEnvelope:
    # 动态字段集合由调用方 overrides 控制，运行时由 pydantic 校验。
    base: dict[str, Any] = {
        "runtime_context": RuntimeContext(
            generation_run_id="g1",
            agent_run_id="a1",
            agent_attempt_key="k1",
            thread_id="t1",
            run_scope="chapter",
            decision_target="plan",
        ),
    }
    base.update(overrides)
    return AgentInputEnvelope(**base)


def test_chapter_planner_returns_plan_candidate():
    agent = ChapterPlannerAgent()
    output = agent.run(_envelope(chapter_contract={"pov": "p", "scene_keys": ["s1", "s2"]}))
    assert isinstance(output, ChapterPlanOutput)
    assert output.status == "ready"
    assert len(output.scene_contracts) == 2


def test_chapter_planner_needs_clarification_without_contract():
    agent = ChapterPlannerAgent()
    output = agent.run(_envelope())
    assert output.status == "needs_clarification"


def test_chapter_review_returns_pass():
    agent = ChapterReviewAgent()
    output = agent.run(_envelope(chapter_contract={"pov": "p"}))
    assert isinstance(output, ChapterReviewOutput)
    assert output.status == "ready"
    assert output.overall_rating == "pass"


def test_chapter_review_needs_clarification_without_contract():
    agent = ChapterReviewAgent()
    output = agent.run(_envelope())
    assert output.status == "needs_clarification"


def test_aggregator_blocks_when_scene_not_accepted(db, volume):
    from app.domain.chapters import (
        accept_chapter_plan_revision,
        create_chapter,
        create_chapter_plan_revision,
        create_scene,
    )

    chapter = create_chapter(db, volume, "C1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", {"actor_id": "a", "idempotency_key": "k", "source": "agent"})
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, {"actor_id": "a", "idempotency_key": "k", "source": "agent"})
    chapter.chapter_sync_status = "in_sync"
    create_scene(db, chapter.id, "S1", {"client_key": "s1"}, {"actor_id": "a", "idempotency_key": "k"})
    db.flush()

    from app.errors import AppError

    aggregator = ChapterAggregator(db)
    with pytest.raises(AppError) as exc:
        aggregator.aggregate(
            chapter.id,
            reason="r",
            ctx={
                "actor_id": "a",
                "idempotency_key": "k",
                "source": "agent",
                "generation_run_id": "g1",
            },
        )
    assert exc.value.code == "scene_not_accepted"


def test_aggregator_rejects_stale_fencing_token_and_does_not_write(db, volume):
    """聚合节点必须校验 Worker fencing token；旧 token 被拒绝且不写入。"""
    from app.db.models import ChapterRevision, GenerationRun
    from app.domain.chapter_orchestration import current_accepted_chapter_revision_id
    from app.domain.chapters import (
        accept_chapter_plan_revision,
        aggregate_chapter_revision,
        commit_chapter_version,
        create_chapter,
        create_chapter_plan_revision,
        create_scene,
    )
    from app.errors import AppError

    chapter = create_chapter(db, volume, "C1", "pov", {"intent": 1}, {"actor_id": "a", "idempotency_key": "k"})
    plan = create_chapter_plan_revision(db, chapter.id, None, {"c": 1}, "r", {"actor_id": "a", "idempotency_key": "k", "source": "agent"})
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, {"actor_id": "a", "idempotency_key": "k", "source": "agent"})
    chapter.chapter_sync_status = "in_sync"
    scene = create_scene(db, chapter.id, "S1", {"client_key": "s1"}, {"actor_id": "a", "idempotency_key": "k"})
    db.flush()
    # 场景 accepted。
    from app.db.models import SceneRevision

    srev = SceneRevision(scene_id=scene.id, parent_revision_id=None, content="x", content_hash="h", reason="r", source_ref="s", status="accepted")
    db.add(srev)
    db.flush()
    scene.accepted_scene_revision_id = srev.id
    # 章节 accepted，建立显式指针。
    rev = aggregate_chapter_revision(db, chapter.id, [], "r", {"actor_id": "a", "idempotency_key": "k", "source": "agent", "generation_run_id": "g1"})
    commit_chapter_version(db, rev.id, {"actor_id": "a", "idempotency_key": "k", "source": "agent", "generation_run_id": "g1"})
    db.flush()
    # 创建 GenerationRun 持有 worker 租约，fencing token=5。
    run = GenerationRun(id="g1", project_id="p1", status="running", write_owner_kind="worker", write_owner_id="w1", write_fencing_token=5)
    db.add(run)
    # 为章节创建有效入口 handoff（来源=当前 accepted 指针）。
    from app.db.models import ChapterHandoff

    current = current_accepted_chapter_revision_id(db, chapter.id)
    handoff = ChapterHandoff(chapter_id=chapter.id, source_chapter_revision_id=current, entry_handoff_status="in_sync", chain_hash="chain-1", status="active")
    db.add(handoff)
    db.flush()

    aggregator = ChapterAggregator(db)
    before = current_accepted_chapter_revision_id(db, chapter.id)
    # 旧 token 4 不匹配当前 5，必须被拒绝。
    with pytest.raises(AppError) as exc:
        aggregator.aggregate(
            chapter.id,
            reason="r",
            ctx={
                "actor_id": "a",
                "idempotency_key": "k",
                "source": "agent",
                "generation_run_id": "g1",
                "lease_context": {"worker_id": "w1", "fencing_token": 4},
            },
            entry_handoff_id=handoff.id,
            entry_source_chapter_revision_id=current,
            entry_handoff_chain_hash="chain-1",
        )
    assert exc.value.code == "RUN_LEASE_LOST"
    # 不写入：没有新增 staged 章节修订。
    staged = db.query(ChapterRevision).filter_by(chapter_id=chapter.id, status="staged").count()
    assert staged == 0
    assert current_accepted_chapter_revision_id(db, chapter.id) == before
