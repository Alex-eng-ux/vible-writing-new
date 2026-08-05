from __future__ import annotations

import pytest

from app.errors import AppError
from app.runtime.identity_resolution import IdentityResolutionStep
from app.runtime.run_identity import RunIdentityStep


def test_run_identity_normalizes_required_fields():
    step = RunIdentityStep()
    ident = step.normalize(
        {},
        {
            "generation_run_id": "g1",
            "agent_run_id": "a1",
            "agent_attempt_key": "ak1",
            "parent_generation_run_id": None,
            "supersedes_run_id": None,
        },
    )
    assert ident["generation_run_id"] == "g1"
    assert ident["agent_attempt_key"] == "ak1"


def test_run_identity_rejects_missing_fields():
    step = RunIdentityStep()
    with pytest.raises(AppError):
        step.normalize({}, {"generation_run_id": "g1", "agent_run_id": "a1"})


def test_run_identity_rejects_parent_equals_current():
    step = RunIdentityStep()
    with pytest.raises(AppError):
        step.normalize(
            {},
            {
                "generation_run_id": "g1",
                "agent_run_id": "a1",
                "agent_attempt_key": "ak1",
                "parent_generation_run_id": "g1",
            },
        )


def test_run_identity_rejects_supersedes_equals_current():
    step = RunIdentityStep()
    with pytest.raises(AppError):
        step.normalize(
            {},
            {
                "generation_run_id": "g1",
                "agent_run_id": "a1",
                "agent_attempt_key": "ak1",
                "supersedes_run_id": "g1",
            },
        )


def test_run_identity_rejects_cross_run_reference():
    step = RunIdentityStep()
    with pytest.raises(AppError):
        step.validate_scope("g2", "g1")


def test_identity_resolution_resolves_local_key():
    step = IdentityResolutionStep()
    assert step.resolve_local_key("lk1", {"lk1": "formal-1"}) == "formal-1"


def test_identity_resolution_rejects_unmapped_local_key():
    step = IdentityResolutionStep()
    with pytest.raises(AppError):
        step.resolve_local_key("lk1", {})


def test_identity_resolution_anchor_hash_stable():
    step = IdentityResolutionStep()
    assert step.anchor_hash("text") == step.anchor_hash("text")
    assert step.anchor_hash("text").startswith("sha256:")


def test_identity_resolution_rejects_unknown_source_ref():
    step = IdentityResolutionStep()
    with pytest.raises(AppError):
        step.resolve_source_ref("s9", ["s1", "s2"])


def test_identity_resolution_accepts_known_source_ref():
    step = IdentityResolutionStep()
    assert step.resolve_source_ref("s1", ["s1", "s2"]) == "s1"
