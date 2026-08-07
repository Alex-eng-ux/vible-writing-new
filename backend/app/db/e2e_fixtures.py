"""Task 7B/7C E2E 确定性播种工具（Playwright 测试专用）。

后端 Worker 是占位实现（不执行 LangGraph 图），因此 Playwright 测试需要一个
确定性推进运行状态的入口。本模块通过领域服务与 ``PostgresRunEventStore``
写入运行状态与事件，所有数据均为固定 fixture（固定的审校问题、澄清问题、
暂停原因、Canon 候选），不依赖真实模型（Fake model 语义）。

边界：
- 只操作测试库（默认 ``E2E_DATABASE_URL``，回退 ``DATABASE_URL``）；
- 只播种/推进运行状态与事件，绝不修改后端领域契约或 API；
- 每次调用独立事务提交，配合 novel_e2e 库全局重置实现可重复测试。

CLI 用法（供 Playwright ``execSync`` 调用）::

    python -m app.db.e2e_fixtures seed-plan --db <url> --chapter-id <id>
    python -m app.db.e2e_fixtures seed-scene-accepted --db <url> --scene-id <id> --text "正文"
    python -m app.db.e2e_fixtures seed-chapter-accepted --db <url> --chapter-id <id>
    python -m app.db.e2e_fixtures seed-canon-candidates --db <url> --run-id <id>
    python -m app.db.e2e_fixtures seed-canon-entries --db <url> --project-id <id> [--chapter-id <id>]
    python -m app.db.e2e_fixtures advance --db <url> --run-id <id> --to waiting_feedback \
        --issues-json '[{"local_key":"k1","severity":"high",...}]'
    python -m app.db.e2e_fixtures advance --db <url> --run-id <id> --to pending_clarification \
        --questions-json '["请确认角色目标"]'
    python -m app.db.e2e_fixtures advance --db <url> --run-id <id> --to paused --reason manual
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, cast

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import (
    CanonFact,
    Chapter,
    ChapterPlanRevision,
    ChapterRevision,
    FactCandidate,
    GenerationRun,
    PlotThreadUpdate,
    PlotThread,
    RunEvent,
    RunOutboxRecord,
    Scene,
    SceneDraftArtifact,
    SceneRevision,
    TimelineEventCandidate,
    TimelineEvent,
)
from app.domain.chapters import (
    accept_chapter_plan_revision,
    aggregate_chapter_revision,
    commit_chapter_version,
    create_chapter_plan_revision,
    persist_chapter_plan_candidate,
)
from app.domain.interfaces import CommandContext
from app.domain.manuscript import content_hash
from app.domain.prosemirror import apply_prosemirror_steps, empty_doc_content
from app.domain.story_bible import upsert_canon_candidates
from app.runtime.run_events import PostgresRunEventStore

# 固定 fixture 的 CommandContext（author 来源，manual_command_id 标识身份）。
_FIXTURE_CTX = cast(
    CommandContext,
    {
        "lease_context": None,
        "write_fence": None,
        "generation_run_id": None,
        "agent_run_id": None,
        "manual_command_id": "e2e-fixture",
        "source": "author",
        "parent_generation_run_id": None,
        "supersedes_run_id": None,
        "parent_plan_revision_id": None,
        "actor_id": "e2e-fixture",
        "preceding_chapter_id": None,
        "preceding_accepted_chapter_revision_id": None,
        "entry_handoff_id": None,
        "entry_source_chapter_revision_id": None,
        "entry_handoff_chain_hash": None,
        "base_scene_revision_id": None,
        "base_chapter_revision_id": None,
        "accepted_scene_revision_id": None,
        "accepted_chapter_revision_id": None,
        "plan_revision_id": None,
        "canon_scope": None,
        "decision_target": None,
        "context_source_refs": [],
        "author_decision": None,
        "idempotency_key": "e2e-fixture",
        "expected_run_version": None,
    },
)


def _pm_doc(text: str) -> str:
    """把纯文本包装为规范化 ProseMirror 文档（空基线 + insert 操作）。"""
    return apply_prosemirror_steps(empty_doc_content(), [{"op": "insert", "value": text}])


def _db_url() -> str:
    """返回测试库 URL（优先 E2E_DATABASE_URL，回退 DATABASE_URL）。"""
    return os.environ.get(
        "E2E_DATABASE_URL",
        os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/novel_e2e"),
    )


def seed_plan(
    session: Session,
    chapter_id: str,
    scene_id: str | None = None,
) -> str:
    """创建并接受章节 plan；可选映射已有场景。

    返回：accepted plan revision id。E2E Worker 是否自动消费该 plan 由 Worker
    启动模式控制，fixture 本身不改变生产 outbox 语义。
    """
    scene_briefs: list[dict[str, Any]] = []
    if scene_id:
        scene = session.get(Scene, scene_id)
        if scene is None or scene.chapter_id != chapter_id:
            raise SystemExit(f"scene not found in chapter: {scene_id}")
        scene_briefs.append(
            {
                "client_key": "e2e-scene",
                "scene_id": scene.id,
                "title": scene.title,
                "scene_brief": scene.scene_brief or {},
            }
        )
    plan = create_chapter_plan_revision(
        session,
        chapter_id,
        None,
        {"scenes": scene_briefs, "outline": "e2e fixture outline"},
        "e2e fixture",
        _FIXTURE_CTX,
    )
    if scene_briefs:
        plan.scene_briefs = scene_briefs
    accept_chapter_plan_revision(
        session,
        chapter_id,
        plan.id,
        # 首次 accept：无当前 plan 指针，运行时按 None 处理（CAS 期望无指针）。
        cast(str, None),
        1,
        _FIXTURE_CTX,
    )
    session.commit()
    return plan.id


def seed_plan_candidate(session: Session, run_id: str) -> str:
    """为章节规划运行幂等播种待接受候选并推进到等待作者决策。"""
    run = session.execute(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None or run.chapter_id is None or run.decision_target != "plan":
        raise SystemExit(f"plan run not found: {run_id}")
    plan = session.execute(
        select(ChapterPlanRevision)
        .where(ChapterPlanRevision.source_run_id == run_id)
        .with_for_update()
    ).scalar_one_or_none()
    if plan is None:
        scene_briefs = [
            {
                "client_key": "journey-scene-1",
                "title": "第一场",
                "scene_brief": {"goal": "在观星台确认线索"},
            },
            {
                "client_key": "journey-scene-2",
                "title": "第二场",
                "scene_brief": {"goal": "作出不可逆选择"},
            },
        ]
        ctx = cast(CommandContext, {**_FIXTURE_CTX, "generation_run_id": run_id})
        plan = persist_chapter_plan_candidate(
            session,
            run.chapter_id,
            source_run_id=run_id,
            planning_lineage_id=run.id,
            chapter_contract={"outline": "确定性章节计划", "scenes": scene_briefs},
            scene_briefs=scene_briefs,
            reason="e2e fixture",
            ctx=ctx,
        )
    event_exists = session.execute(
        select(RunEvent.event_id)
        .where(RunEvent.generation_run_id == run_id, RunEvent.event_type == "run_waiting_feedback")
        .limit(1)
    ).scalar_one_or_none()
    if event_exists is None:
        run.status = "waiting_feedback"
        run.pending_node = "chapter_planner"
        run.write_fencing_token += 1
        session.flush()
        PostgresRunEventStore(session).emit(
            run_id,
            "run_waiting_feedback",
            {"issues": [], "candidate_revision_id": plan.id},
            fencing_token=run.write_fencing_token,
            producer_command_id="e2e-fixture",
        )
    session.commit()
    return plan.id


def seed_scene_accepted(session: Session, scene_id: str, text: str) -> str:
    """创建场景 accepted 版本（固定正文）并更新场景 accepted 指针。

    返回：新 SceneRevision id。若场景已有 accepted 版本，新版本以其为父。
    """
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise SystemExit(f"scene not found: {scene_id}")
    content = _pm_doc(text)
    rev = SceneRevision(
        scene_id=scene_id,
        parent_revision_id=scene.accepted_scene_revision_id,
        content=content,
        content_hash=content_hash(content),
        reason="e2e fixture",
        source_ref="e2e-fixture",
        status="accepted",
    )
    session.add(rev)
    session.flush()
    scene.accepted_scene_revision_id = rev.id
    session.commit()
    return rev.id


def seed_chapter_accepted(session: Session, chapter_id: str) -> str:
    """创建并接受章节版本（固定空场景列表），置章节 in_sync + 入口链 in_sync。

    返回：accepted chapter revision id。要求先执行 ``seed-plan``（章节 Canon
    运行要求章节处于 accepted + in_sync + 入口链非 stale 状态）。
    """
    rev = aggregate_chapter_revision(session, chapter_id, [], "e2e fixture", _FIXTURE_CTX)
    commit_chapter_version(session, rev.id, _FIXTURE_CTX)
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise SystemExit(f"chapter not found: {chapter_id}")
    # 入口链无上游 handoff 时置 in_sync：start_canon_run 仅校验非 stale，
    # confirm 的来源校验要求 entry_handoff_status == "in_sync"。
    chapter.entry_handoff_status = "in_sync"
    session.commit()
    return rev.id


# 固定 Canon 候选模板（Fake model：确定性数据，满足决策前内容校验约束：
# paragraph_ref 非空、effective_story_time.value 非空、narrative_knowledge
# 合法枚举；plot_thread 带 state/planned_resolution）。
def _candidate_templates() -> list[dict]:
    """返回三类固定候选的通用字段（不含作用域归属与来源，调用方补齐）。"""
    return [
        {
            "candidate_type": "fact",
            "fingerprint": None,
            "local_key": "canon-fact-1",
            "content": {
                "claim": "林默是星门守护者",
                "entity_id": "lin-mo",
                "paragraph_ref": "p1",
                "effective_story_time": {"value": "第1章", "precision": "exact"},
                "narrative_knowledge": "objective",
            },
        },
        {
            "candidate_type": "timeline_event",
            "fingerprint": None,
            "local_key": "canon-timeline-1",
            "content": {
                "claim": "林默在观星台发现星门异动",
                "paragraph_ref": "p2",
                "effective_story_time": {"value": "第1章·夜", "precision": "exact"},
                "narrative_knowledge": "objective",
                "entities": ["林默"],
            },
        },
        {
            "candidate_type": "plot_thread",
            "fingerprint": None,
            "local_key": "canon-thread-1",
            "content": {
                "claim": "星门背后的低语暗示旧神苏醒",
                "paragraph_ref": "p3",
                "effective_story_time": {"value": "第1章", "precision": "approx"},
                "narrative_knowledge": "rumor",
                "state": "open",
                "planned_resolution": "第9章揭示",
            },
        },
    ]


def seed_canon_candidates(session: Session, run_id: str) -> str:
    """为 Canon 运行播种固定三类候选并推进到 waiting_feedback。

    参数：run_id 为已通过 API 创建的 Canon 运行 id（``POST /api/{chapters|scenes}
    /{id}/canon-runs``）。
    返回：第一个候选 id（供调用方定位）；无候选时返回空串。

    候选来源版本取运行的 ``canon_source_revision_id``，保证后续决策时
    ``_validate_candidate_bindings`` 与 ``confirm_canon_decisions`` 的来源/
    作用域/归属校验全部通过。运行推进到 ``waiting_feedback`` 并写入
    ``run_waiting_feedback`` 事件（Canon 决策只在等待作者反馈时允许）。
    """
    run = session.execute(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise SystemExit(f"run not found: {run_id}")
    if run.decision_target != "canon":
        raise SystemExit(f"run is not a canon run: {run_id}")
    canon_scope = "scene" if run.scene_id else "chapter"
    source_rev = run.canon_source_revision_id
    if not source_rev:
        raise SystemExit(f"canon run has no source revision: {run_id}")

    candidates = []
    for tmpl in _candidate_templates():
        candidates.append(
            {
                **tmpl,
                "project_id": run.project_id,
                "chapter_id": run.chapter_id,
                "scene_id": run.scene_id,
                "scope": canon_scope,
                "source_revision_id": source_rev,
            }
        )
    existing_rows = []
    for model in (FactCandidate, TimelineEventCandidate, PlotThreadUpdate):
        existing_rows.extend(
            session.execute(
                select(model).where(model.generation_run_id == run_id)
            ).scalars().all()
        )
    if existing_rows:
        candidate_id = existing_rows[0].id
        candidate_count = len(existing_rows)
    else:
        # upsert_canon_candidates 要求 ctx.generation_run_id == run_id（身份互斥校验）。
        ctx = cast(CommandContext, {**_FIXTURE_CTX, "generation_run_id": run_id})
        rows = upsert_canon_candidates(session, run_id, candidates, ctx)
        candidate_id = rows[0]["id"] if rows else ""
        candidate_count = len(rows)
    event_exists = session.execute(
        select(RunEvent.event_id)
        .where(RunEvent.generation_run_id == run_id, RunEvent.event_type == "run_waiting_feedback")
        .limit(1)
    ).scalar_one_or_none()
    if event_exists is None:
        # 仅首次推进时递增 fencing 并追加事件；重复 fixture 调用保持事件序列稳定。
        run.status = "waiting_feedback"
        run.pending_node = "canon_extract"
        run.write_fencing_token += 1
        token = run.write_fencing_token
        session.flush()
        PostgresRunEventStore(session).emit(
            run_id,
            "run_waiting_feedback",
            {"issues": [], "candidate_count": candidate_count},
            fencing_token=token,
            producer_command_id="e2e-fixture",
        )
    session.commit()
    return candidate_id


def seed_canon_entries(session: Session, project_id: str, chapter_id: str | None = None) -> None:
    """播种项目正式 Story Bible 条目（固定 CanonFact/TimelineEvent/PlotThread）。

    参数：project_id 为项目 id；chapter_id 可选，作为 timeline/thread 的归属
    章节（章节级确认物化时会带 chapter_id；无则留空）。
    只写入 status=active 的正式条目，供 ``GET /api/projects/{id}/canon`` 展示。
    """
    session.add(
        CanonFact(
            project_id=project_id,
            entity_id="lin-mo",
            fact_text="林默是星门守护者",
            status="active",
        )
    )
    session.add(
        TimelineEvent(
            project_id=project_id,
            chapter_id=chapter_id,
            event_text="林默在观星台发现星门异动",
            story_time={"value": "第1章", "precision": "exact"},
            entities=["林默"],
            status="active",
        )
    )
    session.add(
        PlotThread(
            project_id=project_id,
            chapter_id=chapter_id,
            thread_text="星门背后的低语暗示旧神苏醒",
            state="open",
            planned_resolution="第9章揭示",
            status="active",
        )
    )
    session.commit()


def advance_run(
    session: Session,
    run_id: str,
    to: str,
    issues: list[dict] | None = None,
    questions: list[str] | None = None,
    reason: str | None = None,
    pending_node: str | None = None,
    draft_text: str | None = None,
) -> None:
    """把运行确定性推进到指定状态并写入对应 RunEvent（固定 payload）。

    事件类型与 payload：
        - waiting_feedback: ``run_waiting_feedback``，payload ``{"issues": [...]}``，
          提供 ``draft_text`` 时为运行播种首稿草稿并把 ``draft_artifact_id``
          一并放入 payload（前端接受决策时携带该 id 物化版本）；
        - pending_clarification: ``run_pending_clarification``，payload ``{"questions": [...]}``；
        - paused: ``run_paused``，payload ``{"reason": ...}``。

    payload 键均避开 ``sanitize_payload`` 的敏感键（text/content/prompt/prose/
    draft_text），审校问题正文不会被脱敏。
    """
    run = session.execute(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise SystemExit(f"run not found: {run_id}")
    run.status = to
    run.pending_node = pending_node
    if questions is not None:
        run.clarification_questions = questions
    if reason is not None:
        run.pause_reason = reason
    # 推进写入令牌（模拟 Worker 写 fence），事件用同一令牌写入。
    run.write_fencing_token += 1
    token = run.write_fencing_token
    session.flush()

    store = PostgresRunEventStore(session)
    if to == "waiting_feedback":
        payload: dict = {"issues": issues or []}
        # 首稿场景：播种 draft 供前端 accept 决策物化（场景无 accepted 版本时）。
        if draft_text is not None and run.scene_id is not None:
            artifact = SceneDraftArtifact(
                scene_id=run.scene_id,
                content=_pm_doc(draft_text),
                content_hash=content_hash(_pm_doc(draft_text)),
                status="pending",
                generation_run_id=run_id,
                agent_run_id=None,
                manual_command_id=None,
                idempotency_key="e2e-fixture-draft",
            )
            session.add(artifact)
            session.flush()
            payload["draft_artifact_id"] = artifact.id
        store.emit(
            run_id,
            "run_waiting_feedback",
            payload,
            fencing_token=token,
            producer_command_id="e2e-fixture",
        )
    elif to == "pending_clarification":
        store.emit(
            run_id,
            "run_pending_clarification",
            {"questions": questions or []},
            fencing_token=token,
            producer_command_id="e2e-fixture",
        )
    else:  # paused
        store.emit(
            run_id,
            "run_paused",
            {"reason": reason or "manual"},
            fencing_token=token,
            producer_command_id="e2e-fixture",
        )
    session.commit()


def seed_chapter_review(session: Session, run_id: str) -> str:
    """为章节审校运行幂等创建 staged revision 并推进等待反馈。"""
    run = session.execute(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None or run.chapter_id is None or run.decision_target != "chapter":
        raise SystemExit(f"chapter review run not found: {run_id}")
    revision = session.execute(
        select(ChapterRevision)
        .where(ChapterRevision.review_run_id == run_id)
        .with_for_update()
    ).scalar_one_or_none()
    if revision is None:
        ctx = cast(CommandContext, {**_FIXTURE_CTX, "generation_run_id": run_id})
        revision = aggregate_chapter_revision(
            session,
            run.chapter_id,
            [],
            "e2e fixture chapter review",
            ctx,
        )
        revision.review_issues = [
            {
                "local_key": "chapter-structure",
                "severity": "medium",
                "dimension": "structure",
                "message": "结构节奏需要确认",
            }
        ]
        revision.review_summary = {
            "status": "needs_review",
            "overall_rating": "B",
            "submitted": True,
        }
        revision.review_run_id = run_id
        chapter = session.get(Chapter, run.chapter_id)
        if chapter is not None:
            # 章节审校 fixture 模拟入口链已同步，允许随后按 accepted
            # ChapterRevision 启动章节级 Canon；不改变生产 handoff 语义。
            chapter.entry_handoff_status = "in_sync"
    event_exists = session.execute(
        select(RunEvent.event_id)
        .where(RunEvent.generation_run_id == run_id, RunEvent.event_type == "run_waiting_feedback")
        .limit(1)
    ).scalar_one_or_none()
    if event_exists is None:
        run.status = "waiting_feedback"
        run.pending_node = "chapter_review"
        run.write_fencing_token += 1
        session.flush()
        PostgresRunEventStore(session).emit(
            run_id,
            "run_waiting_feedback",
            {"issues": revision.review_issues, "chapter_revision_id": revision.id},
            fencing_token=run.write_fencing_token,
            producer_command_id="e2e-fixture",
        )
    session.commit()
    return revision.id


def diagnose_chapter(session: Session, chapter_id: str) -> dict[str, Any]:
    """输出失败诊断：阶段、运行、待决策、最后事件与未消费 outbox。"""
    from app.domain.chapters import chapter_workflow_read

    snapshot = chapter_workflow_read(session, chapter_id)
    active = snapshot.get("active_run") or {}
    run_id = active.get("run_id")
    last_event_sequence = 0
    if run_id:
        last_event_sequence = session.execute(
            select(RunEvent.sequence)
            .where(RunEvent.generation_run_id == run_id)
            .order_by(RunEvent.sequence.desc())
            .limit(1)
        ).scalar_one_or_none() or 0
    unconsumed_states = ("pending", "publishing", "published", "failed")
    outbox_query = select(RunOutboxRecord).where(
        RunOutboxRecord.delivery_status.in_(unconsumed_states)
    )
    # 没有活动 run 时只看章节资源 outbox，避免 generation_run_id IS NULL
    # 把其他命令的全局消息误报为本章节诊断结果。
    if run_id:
        outbox_query = outbox_query.where(
            (RunOutboxRecord.generation_run_id == run_id)
            | (RunOutboxRecord.resource_id == chapter_id)
        )
    else:
        outbox_query = outbox_query.where(RunOutboxRecord.resource_id == chapter_id)
    pending_outbox = session.execute(outbox_query.order_by(RunOutboxRecord.created_at)).scalars().all()
    return {
        "phase": snapshot.get("phase"),
        "run_id": run_id,
        "pending_decision": snapshot.get("pending_decision"),
        "last_event_sequence": last_event_sequence,
        "unconsumed_outbox": [
            {
                "outbox_id": row.outbox_id,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "delivery_status": row.delivery_status,
            }
            for row in pending_outbox
        ],
    }


def _parse_json(arg: str | None) -> Any:
    if not arg:
        return None
    return json.loads(arg)


def main(argv: list[str] | None = None) -> None:
    """CLI 入口：解析子命令并执行播种/推进。"""
    parser = argparse.ArgumentParser(description="Task 7B E2E fixture seeding")
    parser.add_argument("--db", default=None, help="database URL (default: E2E_DATABASE_URL)")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("seed-plan")
    p1.add_argument("--chapter-id", required=True)
    p1.add_argument("--scene-id", default=None)

    p2 = sub.add_parser("seed-scene-accepted")
    p2.add_argument("--scene-id", required=True)
    p2.add_argument("--text", required=True)

    p2b = sub.add_parser("seed-chapter-accepted")
    p2b.add_argument("--chapter-id", required=True)

    p2a = sub.add_parser("seed-plan-candidate")
    p2a.add_argument("--run-id", required=True)

    p2e = sub.add_parser("seed-chapter-review")
    p2e.add_argument("--run-id", required=True)

    pdiag = sub.add_parser("diagnose")
    pdiag.add_argument("--chapter-id", required=True)

    p2c = sub.add_parser("seed-canon-candidates")
    p2c.add_argument("--run-id", required=True)

    p2d = sub.add_parser("seed-canon-entries")
    p2d.add_argument("--project-id", required=True)
    p2d.add_argument("--chapter-id", default=None)

    p3 = sub.add_parser("advance")
    p3.add_argument("--run-id", required=True)
    p3.add_argument(
        "--to", required=True, choices=["waiting_feedback", "pending_clarification", "paused"]
    )
    p3.add_argument("--issues-json", default=None)
    p3.add_argument("--questions-json", default=None)
    p3.add_argument("--reason", default=None)
    p3.add_argument("--pending-node", default=None)
    p3.add_argument("--draft-text", default=None, help="waiting_feedback 时播种首稿草稿")

    args = parser.parse_args(argv)
    url = args.db or _db_url()
    engine = create_engine(url)
    with Session(engine) as session:
        if args.command == "seed-plan":
            plan_id = seed_plan(session, args.chapter_id, args.scene_id)
            print(plan_id)
        elif args.command == "seed-plan-candidate":
            print(seed_plan_candidate(session, args.run_id))
        elif args.command == "seed-scene-accepted":
            rev_id = seed_scene_accepted(session, args.scene_id, args.text)
            print(rev_id)
        elif args.command == "seed-chapter-accepted":
            rev_id = seed_chapter_accepted(session, args.chapter_id)
            print(rev_id)
        elif args.command == "seed-chapter-review":
            print(seed_chapter_review(session, args.run_id))
        elif args.command == "diagnose":
            print(json.dumps(diagnose_chapter(session, args.chapter_id), ensure_ascii=False))
        elif args.command == "seed-canon-candidates":
            candidate_id = seed_canon_candidates(session, args.run_id)
            print(candidate_id)
        elif args.command == "seed-canon-entries":
            seed_canon_entries(session, args.project_id, args.chapter_id)
            print("ok")
        else:  # advance
            advance_run(
                session,
                args.run_id,
                args.to,
                issues=_parse_json(args.issues_json),
                questions=_parse_json(args.questions_json),
                reason=args.reason,
                pending_node=args.pending_node,
                draft_text=args.draft_text,
            )
            print("ok")


if __name__ == "__main__":
    main(sys.argv[1:])
