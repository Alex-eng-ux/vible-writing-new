from __future__ import annotations

import pytest

from app.agents.schemas import ChapterPlanOutput
from app.db.models import (
    ChapterPlanDiscussionMessage,
    ChapterPlanProposal,
    ChapterPlanQuestion,
    ChapterPlanRevisionLink,
    ChapterPlanSceneLink,
    GenerationRun,
    RunOutboxRecord,
    Volume,
)
from app.domain.chapters import (
    accept_chapter_plan_revision,
    chapter_workflow_read,
    create_chapter,
    persist_chapter_plan_candidate,
)
from app.errors import AppError
from app.services.generation_runs import persist_planner_output


def _ctx() -> dict:
    return {
        "actor_id": "author-1",
        "idempotency_key": "cmd-1",
        "source": "agent",
        "generation_run_id": "run-1",
    }


def test_planner_candidate_is_idempotent_and_not_accepted(db, volume):
    chapter = create_chapter(db, volume, "C", "pov", {"text": "intent"}, _ctx())
    first = persist_chapter_plan_candidate(
        db,
        chapter.id,
        source_run_id="run-1",
        planning_lineage_id="lineage-1",
        chapter_contract={"pov": "pov"},
        scene_briefs=[{"client_key": "s1", "title": "S1", "scene_brief": {}}],
        reason="candidate",
        ctx=_ctx(),
    )
    second = persist_chapter_plan_candidate(
        db,
        chapter.id,
        source_run_id="run-1",
        planning_lineage_id="lineage-1",
        chapter_contract={"pov": "changed"},
        scene_briefs=[],
        reason="retry",
        ctx=_ctx(),
    )
    assert first.id == second.id
    assert first.status == "pending"
    assert db.query(type(first)).filter_by(chapter_id=chapter.id).count() == 1


def test_accept_plan_materializes_fixed_links_and_workflow_read(db, volume):
    chapter = create_chapter(db, volume, "C", "pov", {"text": "intent"}, _ctx())
    plan = persist_chapter_plan_candidate(
        db,
        chapter.id,
        source_run_id="run-2",
        planning_lineage_id="lineage-2",
        chapter_contract={"pov": "pov"},
        scene_briefs=[{"client_key": "s1", "title": "S1", "scene_brief": {}}],
        reason="candidate",
        ctx={**_ctx(), "generation_run_id": "run-2", "idempotency_key": "cmd-2"},
    )
    accepted = accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _ctx())
    assert db.query(ChapterPlanSceneLink).filter_by(plan_revision_id=accepted.id).count() == 1
    outbox = db.query(RunOutboxRecord).filter_by(
        resource_type="chapter_plan", resource_id=accepted.id
    ).one()
    assert outbox.payload["event_type"] == "chapter_plan.accepted"
    view = chapter_workflow_read(db, chapter.id)
    assert view["plan"]["accepted_revision_id"] == accepted.id
    assert view["scenes"][0]["scene_id"] == outbox.payload["scene_mapping"]["s1"]


def test_accept_plan_first_round_allows_null_pointer_and_rejects_stale_replay(db, volume):
    chapter = create_chapter(db, volume, "C", "pov", {"text": "intent"}, _ctx())
    plan = persist_chapter_plan_candidate(
        db,
        chapter.id,
        source_run_id="run-first-accept",
        planning_lineage_id="lineage-first-accept",
        chapter_contract={"pov": "pov"},
        scene_briefs=[],
        reason="candidate",
        ctx=_ctx(),
    )

    accepted = accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _ctx())
    assert accepted.status == "accepted"

    with pytest.raises(AppError, match="pointer mismatch"):
        accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, _ctx())
    with pytest.raises(AppError, match="version mismatch"):
        accept_chapter_plan_revision(db, chapter.id, plan.id, plan.id, 2, _ctx())


def test_planner_output_replay_deduplicates_question_proposal_and_message(db, volume):
    chapter = create_chapter(db, volume, "C", "pov", {"text": "intent"}, _ctx())
    project_id = db.get(Volume, volume).project_id
    run = GenerationRun(
        id="planner-replay-run",
        project_id=project_id,
        chapter_id=chapter.id,
        request_type="new_chapter",
        decision_target="plan",
        status="running",
        run_version=1,
        write_fencing_token=0,
    )
    db.add(run)
    db.flush()
    output = ChapterPlanOutput(
        status="needs_clarification",
        reason="Need one answer",
        clarification_questions=["Which POV?"],
    )
    persist_planner_output(db, run.id, output)
    persist_planner_output(db, run.id, output)

    assert db.query(ChapterPlanDiscussionMessage).filter_by(source_run_id=run.id).count() == 1
    assert db.query(ChapterPlanQuestion).filter_by(source_run_id=run.id).count() == 1
    question = db.query(ChapterPlanQuestion).filter_by(source_run_id=run.id).one()
    assert question.question_id

    ready_run = GenerationRun(
        id="planner-ready-run",
        project_id=project_id,
        chapter_id=chapter.id,
        request_type="new_chapter",
        decision_target="plan",
        status="running",
        run_version=1,
        write_fencing_token=0,
    )
    db.add(ready_run)
    db.flush()
    ready = ChapterPlanOutput(
        status="ready",
        chapter_contract={"pov": "pov"},
        scene_contracts=[],
        reason="Candidate",
        proposals=[
            {
                "field_path": "pov",
                "value": "limited",
                "rationale": "Keep the chapter close to the protagonist.",
            }
        ],
    )
    persist_planner_output(db, ready_run.id, ready)
    persist_planner_output(db, ready_run.id, ready)
    assert db.query(ChapterPlanDiscussionMessage).filter_by(source_run_id=ready_run.id).count() == 1
    proposals = db.query(ChapterPlanProposal).filter_by(source_run_id=ready_run.id).all()
    assert len(proposals) == 1
    assert proposals[0].proposal_id


def test_workflow_blocks_invalid_accepted_pointer_and_missing_scene_mapping(db, volume):
    chapter = create_chapter(db, volume, "C", "pov", {"text": "intent"}, _ctx())
    plan = persist_chapter_plan_candidate(
        db,
        chapter.id,
        source_run_id="run-blocked-workflow",
        planning_lineage_id="lineage-blocked-workflow",
        chapter_contract={"pov": "pov"},
        scene_briefs=[{"client_key": "s1", "title": "S1", "scene_brief": {}}],
        reason="candidate",
        ctx=_ctx(),
    )
    db.add(
        ChapterPlanRevisionLink(
            chapter_id=chapter.id,
            plan_revision_id=plan.id,
            plan_version=1,
        )
    )
    db.flush()
    view = chapter_workflow_read(db, chapter.id)
    assert view["phase"] == "blocked"
    assert f"accepted_plan_not_accepted:{plan.id}" in view["blocking_reasons"]

    plan.status = "accepted"
    db.flush()
    view = chapter_workflow_read(db, chapter.id)
    assert view["phase"] == "blocked"
    assert "scene_mapping_missing:s1" in view["blocking_reasons"]
