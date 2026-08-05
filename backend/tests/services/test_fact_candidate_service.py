from __future__ import annotations

from app.services.fact_candidate_service import FactCandidateService


def _agent_ctx():
    return {
        "generation_run_id": "run-1",
        "agent_run_id": "agent-1",
        "manual_command_id": None,
        "source": "agent",
        "actor_id": "author-1",
        "idempotency_key": "key-a",
        "expected_run_version": 1,
        "lease_context": {"worker_id": "w1", "fencing_token": 1},
        "write_fence": None,
    }


def _candidate():
    return {
        "project_id": "proj-1",
        "chapter_id": "chap-1",
        "scene_id": "scene-1",
        "scope": "scene",
        "candidate_type": "fact",
        "fingerprint": "fp-1",
        "source_revision_id": "rev-1",
        "content": {"fact": "text"},
    }


def test_fact_candidate_create_and_idempotent_repeat(db):
    svc = FactCandidateService(db)
    first = svc.upsert("run-1", [_candidate()], _agent_ctx())
    second = svc.upsert("run-1", [_candidate()], _agent_ctx())
    assert first[0]["id"] == second[0]["id"]
    assert first[0]["status"] == "pending"


def test_fact_candidate_run_id_mismatch_rejected(db):
    from app.errors import AppError

    svc = FactCandidateService(db)
    from pytest import raises

    with raises(AppError) as exc:
        svc.upsert("run-999", [_candidate()], _agent_ctx())
    assert exc.value.code == "COMMAND_CONTEXT_MISMATCH"
