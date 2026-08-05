from __future__ import annotations

import pytest

from app import errors

EXPECTED_STABLE_CODES = {
    "RUN_STATE_CONFLICT",
    "RUN_LEASE_LOST",
    "IDEMPOTENCY_KEY_REUSE",
    "IDEMPOTENCY_IN_PROGRESS",
    "ACTOR_OVERRIDE_FORBIDDEN",
    "CHECKPOINT_EXPIRED",
    "COMMAND_CONTEXT_MISMATCH",
    "CONTEXT_BUDGET_EXCEEDED",
    "CONTEXT_MANIFEST_MISMATCH",
    "CONTEXT_SOURCE_UNAVAILABLE",
    "PLAN_REVISION_CONFLICT",
    "PLAN_NOT_ACCEPTED",
    "CANON_NOT_ENABLED",
    "CANON_USE_DEDICATED_ENDPOINT",
    "CHAPTER_HANDOFF_CONFLICT",
    "CHAPTER_OUT_OF_SYNC",
    "SCENE_NOT_ACCEPTED",
    "SCENE_ACTIVE_RUN",
    "SCENE_STALE",
    "SCENE_PLAN_MISMATCH",
    "SCENE_STATE_INCOMPATIBLE",
}


def test_all_stable_error_codes_are_registered() -> None:
    missing = EXPECTED_STABLE_CODES - set(errors.REGISTRY)
    assert not missing, f"missing stable codes: {sorted(missing)}"


def test_every_stable_code_has_fixed_http_and_retryable() -> None:
    for code in EXPECTED_STABLE_CODES:
        spec = errors.get_error_spec(code)
        assert spec.code == code
        assert isinstance(spec.http_status, int)
        assert isinstance(spec.retryable, bool)
        assert spec.default_message
        # Every code maps to itself (no synonymous fallback).
        assert spec.http_status == errors.REGISTRY[code].http_status


def test_unknown_code_falls_back_to_internal_error() -> None:
    spec = errors.get_error_spec("NOT_A_REAL_CODE")
    assert spec.code == "INTERNAL_ERROR"
    assert spec.http_status == 500


def test_build_envelope_shape() -> None:
    envelope = errors.build_envelope(
        "RUN_LEASE_LOST",
        "lease lost",
        run_id="run-abc",
        request_id="req-1",
        details={"attempt": 3},
    )
    assert set(envelope.keys()) == {"code", "message", "retryable", "run_id", "request_id", "details"}
    assert envelope["code"] == "RUN_LEASE_LOST"
    assert envelope["retryable"] is True
    assert envelope["run_id"] == "run-abc"
    assert envelope["request_id"] == "req-1"
    assert envelope["details"] == {"attempt": 3}


def test_resource_error_has_null_run_id() -> None:
    # Resource errors must not fabricate a run id.
    envelope = errors.build_envelope("SCENE_NOT_ACCEPTED", "scene not accepted", request_id="req-2")
    assert envelope["run_id"] is None


def test_run_error_carries_real_generation_run_id() -> None:
    envelope = errors.build_envelope(
        "RUN_STATE_CONFLICT",
        "conflict",
        run_id="generation-run-7",
        request_id="req-3",
    )
    assert envelope["run_id"] == "generation-run-7"


def test_registry_default_messages_never_leak_secrets() -> None:
    # The registry only ever surfaces fixed, safe default messages.
    for spec in errors.ERROR_SPECS:
        lowered = spec.default_message.lower()
        assert "password" not in lowered
        assert "api_key" not in lowered
        assert "postgres" not in lowered
        assert "supersecret" not in lowered
    # The generic fallback stays fixed and generic.
    envelope = errors.build_envelope("INTERNAL_ERROR", request_id="req-4")
    assert envelope["message"] == "internal server error"


def test_app_error_exposes_http_status_and_retryable() -> None:
    exc = errors.AppError("CANON_NOT_ENABLED", request_id="req-5")
    assert exc.http_status == 503
    assert exc.retryable is False
    with pytest.raises(errors.AppError):
        raise exc


def test_resource_errors_are_not_retryable_by_default() -> None:
    # Spot-check that resource/conflict errors are not silently retryable.
    for code in ("SCENE_NOT_ACCEPTED", "PLAN_NOT_ACCEPTED", "IDEMPOTENCY_KEY_REUSE"):
        assert errors.REGISTRY[code].retryable is False
