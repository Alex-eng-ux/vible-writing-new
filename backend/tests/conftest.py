from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.models import Base

# Use a real PostgreSQL database for domain/service tests. The Task 2 tables
# do not depend on the vector extension, so create_all works without pgvector.
# Only the Alembic migration's CREATE EXTENSION vector step requires pgvector.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/novel_test",
)


@pytest.fixture(scope="session", autouse=True)
def _bind_test_database() -> None:
    db_session.reset_engine()
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ.setdefault("ACTOR_ID", "test-actor")
    os.environ.setdefault("DEPLOYMENT_MODE", "single_user_private")
    os.environ.setdefault("API_BIND_SCOPE", "loopback")
    os.environ.setdefault("INTERNAL_API_BASE_URL", "http://127.0.0.1:8000")
    os.environ.setdefault("APP_ENV", "development")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()


@pytest.fixture()
def db():
    """Yield a fresh session against the test database, rolling back per test."""
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        engine.dispose()


@pytest.fixture()
def volume(db):
    """Create a real project + volume so chapter/scene FKs resolve."""
    from app.domain.resources import create_project, create_volume

    project = create_project(
        db, "Test Project", "genre", "reader", "style",
        {"actor_id": "author-1", "idempotency_key": "proj-key"},
    )
    vol = create_volume(
        db, project.id, "Vol 1", "goal", "mainline", "range",
        {"actor_id": "author-1", "idempotency_key": "vol-key"},
    )
    return vol.id
