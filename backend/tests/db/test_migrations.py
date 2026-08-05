from __future__ import annotations

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.models import Base

# The migration's CREATE EXTENSION vector step is validated directly against a
# real PostgreSQL (see test_migration_creates_vector_extension). The remaining
# tests verify the full model metadata (Task 2 tables, constraints, indexes)
# can be created against the real PostgreSQL and that key constraints exist.
# The vector extension is enabled in the test database (novel_test) already.


def test_all_task2_tables_created(db):
    inspector = inspect(db.bind)
    tables = set(inspector.get_table_names())
    expected = {
        "novel_projects",
        "volumes",
        "chapters",
        "scenes",
        "chapter_plan_revisions",
        "scene_revisions",
        "scene_draft_artifacts",
        "change_sets",
        "chapter_revisions",
        "chapter_handoffs",
        "entities",
        "canon_facts",
        "fact_candidates",
        "timeline_event_candidates",
        "plot_thread_updates",
        "foreshadowings",
        "generation_runs",
        "agent_runs",
        "run_decisions",
        "author_feedbacks",
        "canon_decision_records",
        "run_events",
        "run_event_consumer_cursors",
        "run_outbox_records",
        "run_leases",
        "command_idempotency_records",
        "context_manifests",
        "scene_snapshots",
        "chapter_snapshots",
    }
    assert expected <= tables


def test_candidate_source_exactly_one_check_constraint(db):
    inspector = inspect(db.bind)
    constraints = inspector.get_check_constraints("fact_candidates")
    names = {c["name"] for c in constraints}
    assert "ck_candidate_source_exactly_one" in names


def test_idempotency_unique_constraint(db):
    inspector = inspect(db.bind)
    constraints = inspector.get_unique_constraints("command_idempotency_records")
    names = {c["name"] for c in constraints}
    assert "uq_idempotency_key" in names


def test_run_event_sequence_unique_constraint(db):
    inspector = inspect(db.bind)
    constraints = inspector.get_unique_constraints("run_events")
    names = {c["name"] for c in constraints}
    assert "uq_run_event_sequence" in names


def test_candidate_source_unique_constraint(db):
    inspector = inspect(db.bind)
    constraints = inspector.get_unique_constraints("fact_candidates")
    names = {c["name"] for c in constraints}
    assert "uq_candidate_source_fingerprint" in names


def test_metadata_has_no_duplicate_table_definitions():

    names = [t.name for t in Base.metadata.sorted_tables]
    assert len(names) == len(set(names))


def test_v1_rc_observability_migration_preserves_events():
    """V1-RC 观测元数据迁移：升级/降级往返不丢失版本、候选与事件序列。

    在专用迁移库上：先升级到 Task 5C head（f8a9b0c1d2e3），写入一条带
    sequence 的 RunEvent 与一条 GenerationRun；再升级到 v1_rc_observability_metadata
    head，校验 payload_schema/redaction_version 以默认值回填且既有事件序列/负载
    不变；最后降级到 Task 5C head 再升级回来（往返），事件行与序列仍保留。
    """

    url = os.environ.get(
        "MIGRATION_TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/novel_migration_test",
    )
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "app/db/migrations")
    cfg.set_main_option("sqlalchemy.url", url)

    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()

    # 1) 升级到 Task 5C head，写入既有数据（事件带 sequence 与 payload）。
    command.upgrade(cfg, "f8a9b0c1d2e3")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO generation_runs (id, project_id, status, run_version, write_fencing_token, created_at, updated_at) "
                "VALUES ('g-mig-1', 'p-mig-1', 'accepted', 2, 0, now(), now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO run_events (event_id, generation_run_id, sequence, event_type, payload, created_at) "
                "VALUES ('ev-mig-1', 'g-mig-1', 1, 'run_queued', '{\"run_scope\":\"scene\"}', now()), "
                "('ev-mig-2', 'g-mig-1', 2, 'run_accepted', '{\"decision\":\"accept\"}', now())"
            )
        )
    engine.dispose()

    # 2) 升级到 head：既有事件行保留、序列不变，新列按默认值回填。
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT event_id, sequence, event_type, payload, payload_schema, redaction_version "
                "FROM run_events ORDER BY sequence"
            )
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == 1 and rows[0][2] == "run_queued"
        assert rows[0][3] == {"run_scope": "scene"}
        assert rows[0][4] == "run-event.v1"
        assert rows[0][5] == "redaction.v1"
        assert rows[1][1] == 2 and rows[1][2] == "run_accepted"
        # 运行行与版本/审计状态仍保留。
        run = conn.execute(
            text("SELECT project_id, status, run_version FROM generation_runs WHERE id='g-mig-1'")
        ).fetchone()
        assert run is not None and run[1] == "accepted" and run[2] == 2
    engine.dispose()

    # 3) 降级到 Task 5C head（两列移除）再升级回来：事件行与序列仍保留。
    command.downgrade(cfg, "f8a9b0c1d2e3")
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT sequence, event_type FROM run_events ORDER BY sequence")
        ).fetchall()
        assert [r[0] for r in rows] == [1, 2]
        assert rows[0][1] == "run_queued"
    engine.dispose()


def test_migration_creates_vector_extension_and_schema():
    """Run the first Alembic migration against a fresh database and verify the
    vector extension plus the full schema are actually created (not just the
    model metadata via create_all). Requires a real PostgreSQL with pgvector.
    Uses a dedicated migration-test database so it never disturbs the shared
    test database."""

    url = os.environ.get(
        "MIGRATION_TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/novel_migration_test",
    )
    # The runtime DB session is pointed at the test database by conftest; the
    # migration derives its URL from DATABASE_URL, so align it. Drop everything
    # first so this test genuinely exercises the migration from an empty schema.
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "app/db/migrations")
    cfg.set_main_option("sqlalchemy.url", url)

    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    # alembic_version is not part of Base.metadata; drop it so the migration
    # genuinely runs from an empty schema (otherwise a stale version row makes
    # Alembic skip the upgrade entirely).
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    insp = inspect(engine)
    assert "fact_candidates" in insp.get_table_names()
    assert "generation_runs" in insp.get_table_names()
    # vector extension is present
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
        # The extension must be installed; the exact version varies by environment
        # (official pgvector/pgvector:pg16 ships 0.8.x, injected local builds 0.8.1).
        assert row is not None and row[0].startswith("0.8")
        # a vector column round-trips
        val = conn.execute(text("SELECT '[1,2,3]'::vector <-> '[4,5,6]'::vector")).scalar()
        assert abs(val - 5.196152422706632) < 1e-9
    engine.dispose()
