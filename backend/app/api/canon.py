"""Canon 专用运行 API：章节/场景 canon-runs 创建与 Canon 决策提交。

Task 5C 边界：
- Canon 运行创建（章节/场景专用入口）生成独立 `generation_run_id`，通过 outbox
  入队，HTTP 请求绝不执行 LangGraph/CanonAgent。
- `submit_canon_decisions` 按持久 `candidate_id` 逐条校验 `confirm|reject|defer`，
  使用作者 `manual_command_id`、API command fence 与 `expected_run_version`。
- 普通运行入口仍拒绝 `target=canon`；Canon 不得调用 WritingAgent。
- 运行查询复用 `GET /api/runs/{run_id}`（runs.py）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    CanonFact,
    Chapter,
    FactCandidate,
    GenerationRun,
    PlotThread,
    PlotThreadUpdate,
    Scene,
    TimelineEvent,
    TimelineEventCandidate,
)
from ..domain.idempotency import fingerprint
from ..errors import AppError
from ..services.canon_runs import start_canon_run, submit_canon_decisions
from .commands import execute_command
from .deps import get_actor_id, get_db, get_idempotency_key
from .schemas import (
    CanonCandidateListRead,
    CanonCandidateRead,
    CanonDecisionRequest,
    CanonEntryRead,
    CanonRunCreateRequest,
    CanonSnapshotRead,
    DecisionResponse,
    RunSnapshot,
)

router = APIRouter(prefix="/api", tags=["canon"])

# 三类候选模型（结构同构，统一按 FactCandidate 的形状访问公共字段）。
_CANDIDATE_MODELS: dict[str, type[FactCandidate]] = {
    "fact": FactCandidate,  # type: ignore[dict-item]
    "timeline_event": TimelineEventCandidate,  # type: ignore[dict-item]
    "plot_thread": PlotThreadUpdate,  # type: ignore[dict-item]
}


def _candidate_projection(row) -> dict:
    """把候选模型行投影为 CanonCandidateRead 字典（与领域层 _to_dict 一致）。"""
    return {
        "id": row.id,
        "project_id": row.project_id,
        "chapter_id": row.chapter_id,
        "scene_id": row.scene_id,
        "scope": row.scope,
        "scope_identity": row.scope_identity,
        "candidate_type": row.candidate_type,
        "status": row.status,
        "source_identity": row.source_identity,
        "content": row.content,
        "local_key": row.local_key,
        "generation_run_id": row.generation_run_id,
    }


def _current_canon_source_and_run(
    session: Session,
    target_type: Literal["scene", "chapter"],
    target_id: str,
) -> tuple[str | None, GenerationRun | None]:
    """返回目标当前 accepted 来源及该来源最新 Canon run。

    候选表会保留历史来源和多次终态运行；只按目标 id 查询会把旧候选误展示到
    新 accepted revision。这里先读取服务端 accepted 指针，再按来源修订选择
    最新 Canon run，供候选列表和前端运行状态共同使用。没有 accepted 来源或
    Canon run 时返回空快照，避免把无血缘候选暴露为当前状态。
    """
    if target_type == "chapter":
        chapter_target = session.get(Chapter, target_id)
        source_revision_id = chapter_target.accepted_chapter_revision_id if chapter_target else None
        run_query = select(GenerationRun).where(
            GenerationRun.chapter_id == target_id,
            GenerationRun.scene_id.is_(None),
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == source_revision_id,
        )
    else:
        scene_target = session.get(Scene, target_id)
        source_revision_id = scene_target.accepted_scene_revision_id if scene_target else None
        run_query = select(GenerationRun).where(
            GenerationRun.scene_id == target_id,
            GenerationRun.decision_target == "canon",
            GenerationRun.canon_source_revision_id == source_revision_id,
        )
    if not source_revision_id:
        return None, None
    run = session.execute(
        run_query.order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    return source_revision_id, run


@router.get("/projects/{project_id}/canon", response_model=CanonSnapshotRead)
def get_project_canon(
    project_id: str,
    session: Session = Depends(get_db),
) -> CanonSnapshotRead:
    """只读返回项目正式 Story Bible（status=active 的 fact/timeline_event/plot_thread）。"""
    facts = session.execute(
        select(CanonFact).where(
            CanonFact.project_id == project_id, CanonFact.status == "active"
        )
    ).scalars().all()
    timeline = session.execute(
        select(TimelineEvent).where(
            TimelineEvent.project_id == project_id, TimelineEvent.status == "active"
        )
    ).scalars().all()
    threads = session.execute(
        select(PlotThread).where(
            PlotThread.project_id == project_id, PlotThread.status == "active"
        )
    ).scalars().all()

    def _entry(etype: Literal["fact", "timeline_event", "plot_thread"], row, text: str, **extra) -> CanonEntryRead:
        return CanonEntryRead(
            id=row.id,
            type=etype,
            text=text,
            status=row.status,
            created_at=row.created_at.isoformat(),
            chapter_id=getattr(row, "chapter_id", None),
            **extra,
        )

    return CanonSnapshotRead(
        project_id=project_id,
        facts=[
            _entry("fact", f, f.fact_text) for f in facts
        ],
        timeline_events=[
            _entry(
                "timeline_event",
                t,
                t.event_text,
                story_time=t.story_time,
                entities=t.entities,
            )
            for t in timeline
        ],
        plot_threads=[
            _entry(
                "plot_thread",
                p,
                p.thread_text,
                state=p.state,
                planned_resolution=p.planned_resolution,
            )
            for p in threads
        ],
    )


@router.get("/scenes/{scene_id}/canon-candidates", response_model=CanonCandidateListRead)
def get_scene_canon_candidates(
    scene_id: str,
    session: Session = Depends(get_db),
) -> CanonCandidateListRead:
    """只读返回当前 accepted 场景来源对应的 Canon 候选与运行。"""
    source_revision_id, run = _current_canon_source_and_run(session, "scene", scene_id)
    items: list[CanonCandidateRead] = []
    if source_revision_id and run is not None:
        for model_cls in _CANDIDATE_MODELS.values():
            rows = session.execute(
                select(model_cls).where(
                    model_cls.scene_id == scene_id,
                    model_cls.source_revision_id == source_revision_id,
                    model_cls.generation_run_id == run.id,
                )
            ).scalars().all()
            items.extend(CanonCandidateRead(**_candidate_projection(r)) for r in rows)
    # 按类型分组稳定排序（fact -> timeline_event -> plot_thread）。
    items.sort(key=lambda d: d.candidate_type)
    return CanonCandidateListRead(
        target_type="scene",
        target_id=scene_id,
        source_revision_id=source_revision_id,
        run_id=run.id if run else None,
        run_status=run.status if run else None,
        items=items,
    )


@router.get(
    "/chapters/{chapter_id}/canon-candidates", response_model=CanonCandidateListRead
)
def get_chapter_canon_candidates(
    chapter_id: str,
    session: Session = Depends(get_db),
) -> CanonCandidateListRead:
    """只读返回当前 accepted 章节来源对应的 Canon 候选与运行。"""
    source_revision_id, run = _current_canon_source_and_run(session, "chapter", chapter_id)
    items: list[CanonCandidateRead] = []
    if source_revision_id and run is not None:
        for model_cls in _CANDIDATE_MODELS.values():
            rows = session.execute(
                select(model_cls).where(
                    model_cls.chapter_id == chapter_id,
                    model_cls.scene_id.is_(None),
                    model_cls.source_revision_id == source_revision_id,
                    model_cls.generation_run_id == run.id,
                )
            ).scalars().all()
            items.extend(CanonCandidateRead(**_candidate_projection(r)) for r in rows)
    items.sort(key=lambda d: d.candidate_type)
    return CanonCandidateListRead(
        target_type="chapter",
        target_id=chapter_id,
        source_revision_id=source_revision_id,
        run_id=run.id if run else None,
        run_status=run.status if run else None,
        items=items,
    )


@router.post("/chapters/{chapter_id}/canon-runs", response_model=RunSnapshot)
def post_chapter_canon_run(
    chapter_id: str,
    body: CanonRunCreateRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> RunSnapshot:
    """创建章节 Canon 运行（run_scope=chapter，只能使用当前 accepted 且同步的章节版本）。"""
    if body.canon_scope != "chapter":
        raise AppError("COMMAND_CONTEXT_MISMATCH", "chapter canon requires canon_scope=chapter")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        return (
            start_canon_run(
                session, actor_id, "chapter", chapter_id, body,
                manual_command_id or "", idempotency_key,
            ),
            manual_command_id,
        )

    return RunSnapshot(
        **execute_command(
            session,
            f"chapter:{chapter_id}",
            "canon_run_start",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.post("/scenes/{scene_id}/canon-runs", response_model=RunSnapshot)
def post_scene_canon_run(
    scene_id: str,
    body: CanonRunCreateRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> RunSnapshot:
    """创建场景 Canon 运行（run_scope=scene，只能使用当前 accepted 场景版本）。"""
    if body.canon_scope != "scene":
        raise AppError("COMMAND_CONTEXT_MISMATCH", "scene canon requires canon_scope=scene")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        return (
            start_canon_run(
                session, actor_id, "scene", scene_id, body,
                manual_command_id or "", idempotency_key,
            ),
            manual_command_id,
        )

    return RunSnapshot(
        **execute_command(
            session,
            f"scene:{scene_id}",
            "canon_run_start",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.post("/runs/{run_id}/canon-decisions", response_model=DecisionResponse)
def post_canon_decisions(
    run_id: str,
    body: CanonDecisionRequest,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> DecisionResponse:
    """作者对 Canon 运行逐条提交候选决策（confirm|reject|defer）。"""
    if body.idempotency_key != idempotency_key:
        raise AppError(
            "COMMAND_CONTEXT_MISMATCH",
            "canon decision idempotency_key must match the Idempotency-Key header",
        )
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        result = submit_canon_decisions(
            session, actor_id, run_id, body, manual_command_id or ""
        )
        return result, manual_command_id

    return DecisionResponse(
        **execute_command(
            session,
            f"run:{run_id}",
            "canon_decision",
            idempotency_key,
            request_fp,
            run,
        )
    )
