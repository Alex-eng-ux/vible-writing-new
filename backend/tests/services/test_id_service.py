from __future__ import annotations

from app.services.id_service import IdService


def test_idempotent_id_allocation_reuses_same_id(db):
    svc = IdService(db)
    first = svc.allocate("scene", "key-1", "proj-1")
    second = svc.allocate("scene", "key-1", "proj-1")
    assert first == second


def test_id_allocation_differs_by_scope(db):
    svc = IdService(db)
    a = svc.allocate("scene", "key-1", "proj-1")
    b = svc.allocate("scene", "key-1", "proj-2")
    assert a != b


def test_id_allocation_requires_key(db):
    from app.errors import AppError

    svc = IdService(db)
    from pytest import raises

    with raises(AppError) as exc:
        svc.allocate("scene", "", "proj-1")
    assert exc.value.code == "COMMAND_CONTEXT_MISMATCH"
