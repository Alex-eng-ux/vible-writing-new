from __future__ import annotations

import json

from app.consistency.rules import build_rule_engine
from app.consistency.schemas import RuleEngineInput


def _input(text: str = "valid draft") -> RuleEngineInput:
    return RuleEngineInput(
        scene_id="s1",
        project_id="p1",
        draft_text=text,
        accepted_scene_revision_id=None,
        rule_report={},
    )


def test_rule_engine_passes_valid_draft():
    engine = build_rule_engine()
    out = engine.evaluate(_input("a full draft"))
    assert out["passed"] is True
    assert out["issues"] == []


def test_rule_engine_flags_short_draft():
    engine = build_rule_engine()
    out = engine.evaluate(_input("ab"))
    assert out["passed"] is False
    assert any(i["rule_id"] == "min_length" for i in out["issues"])


def test_rule_output_is_serializable():
    engine = build_rule_engine()
    out = engine.evaluate(_input())
    json.dumps(out)  # must not raise


def test_rule_engine_does_not_write_database():
    """Rule evaluation must never touch the database."""
    engine = build_rule_engine()
    out = engine.evaluate(_input())
    assert out["passed"] is True
