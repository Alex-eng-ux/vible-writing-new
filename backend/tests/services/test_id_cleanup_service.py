from __future__ import annotations

from datetime import UTC

from app.db.models import AgentRun, GenerationRun
from app.services.id_cleanup_service import IdCleanupService


def _terminal_run(db, run_id, age_days=30):
    from datetime import timedelta

    from app.db.models import utcnow

    run = GenerationRun(
        id=run_id,
        project_id="proj-1",
        status="completed",
        created_at=utcnow() - timedelta(days=age_days),
    )
    db.add(run)
    db.flush()
    return run


def test_cleanup_only_handles_terminal_runs_older_than_retention(db):
    from datetime import datetime

    now = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
    _terminal_run(db, "run-old", age_days=30)
    _terminal_run(db, "run-recent", age_days=1)
    svc = IdCleanupService(db, retention_days=7)
    runs = svc.collect_terminal_runs(now)
    ids = {r.id for r in runs}
    assert "run-old" in ids
    assert "run-recent" not in ids


def test_cleanup_skips_non_terminal_runs(db):
    from datetime import datetime, timedelta

    from app.db.models import utcnow

    now = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
    db.add(GenerationRun(id="run-running", project_id="proj-1", status="running",
                         created_at=utcnow() - timedelta(days=30)))
    db.flush()
    svc = IdCleanupService(db, retention_days=7)
    assert svc.collect_terminal_runs(now) == []


def test_cleanup_deletes_unreferenced_agent_runs(db):
    _terminal_run(db, "run-old", age_days=30)
    db.add(AgentRun(generation_run_id="run-old", agent_type="generator", status="completed"))
    db.flush()
    svc = IdCleanupService(db, retention_days=7)
    deleted = svc.cleanup_agent_runs(["run-old"])
    assert deleted == 1
