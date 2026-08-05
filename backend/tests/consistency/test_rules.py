"""Task 6 确定性规则测试：六类规则的触发与确定性输出。

覆盖：人物已死亡后再次行动、地点不可能到达、时间线先后冲突、术语变体、
世界硬规则、未知人物存在性、规则确定性输出与规则不写库。
"""

from __future__ import annotations

import json

import pytest

from app.consistency.rules import build_rule_engine, run_deterministic_rules
from app.consistency.schemas import RuleEngineInput

from .conftest import make_manifest, make_snapshot


def test_rule_engine_passes_valid_draft():
    """Task 4A 契约保持：合法草稿通过、短草稿标记。"""
    engine = build_rule_engine()
    assert engine.evaluate(
        RuleEngineInput(
            scene_id="s1", project_id="p1", draft_text="a full draft",
            accepted_scene_revision_id=None, rule_report={},
        )
    )["passed"] is True
    out = engine.evaluate(
        RuleEngineInput(
            scene_id="s1", project_id="p1", draft_text="ab",
            accepted_scene_revision_id=None, rule_report={},
        )
    )
    assert out["passed"] is False
    assert any(i["rule_id"] == "min_length" for i in out["issues"])


def test_dead_character_acting_flagged():
    """人物已死亡后再次行动 -> high, dimension=state。"""
    snap = make_snapshot(
        draft_text="阿明说了一句话",
        characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    state = [i for i in issues if i["dimension"] == "state"]
    assert len(state) == 1
    assert state[0]["severity"] == "high"
    assert "dead" in state[0]["local_key"]
    assert state[0]["evidence_refs"]


def test_departed_character_acting_flagged():
    """离场（departed）人物再次行动同样被标记。"""
    snap = make_snapshot(
        draft_text="阿明走进大殿",
        characters=[{"name": "阿明", "state": "departed", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    state = [i for i in issues if i["dimension"] == "state"]
    assert len(state) == 1
    assert state[0]["severity"] == "high"


def test_unreachable_location_flagged():
    """地点不可能到达 -> high, dimension=location。"""
    snap = make_snapshot(
        draft_text="阿明抵达南城",
        characters=[{"name": "阿明", "state": "alive", "last_seen_location": "北城"}],
        locations=[{"name": "南城", "reachable_from": ["东城"]}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    loc = [i for i in issues if i["dimension"] == "location"]
    assert len(loc) == 1
    assert loc[0]["severity"] == "high"
    assert "南城" in loc[0]["local_key"]


def test_reachable_location_not_flagged():
    """地点可达时不触发问题。"""
    snap = make_snapshot(
        draft_text="阿明抵达南城",
        characters=[{"name": "阿明", "state": "alive", "last_seen_location": "北城"}],
        locations=[{"name": "南城", "reachable_from": ["北城"]}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    assert not [i for i in issues if i["dimension"] == "location"]


def test_timeline_order_conflict_flagged():
    """时间线先后冲突 -> high, dimension=timeline。"""
    snap = make_snapshot(
        draft_text="第3章，阿明回到故乡",
        timeline=[{"event_key": "e1", "story_time": "第5章", "subject": "阿明", "detail": "决战"}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    tl = [i for i in issues if i["dimension"] == "timeline"]
    assert len(tl) == 1
    assert tl[0]["severity"] == "high"


def test_timeline_no_regression_when_after_latest():
    """草稿时间不早于已发生事件时不触发时间线问题。"""
    snap = make_snapshot(
        draft_text="第6章，阿明回到故乡",
        timeline=[{"event_key": "e1", "story_time": "第5章", "subject": "阿明", "detail": "决战"}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    assert not [i for i in issues if i["dimension"] == "timeline"]


def test_terminology_variant_flagged():
    """术语变体（形近但未登记）-> low, dimension=term。"""
    snap = make_snapshot(
        draft_text="他握紧符祝",
        terms=[{"canonical": "符咒", "variants": ["护符"]}],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    term = [i for i in issues if i["dimension"] == "term"]
    assert len(term) == 1
    assert term[0]["severity"] == "low"
    assert "符祝" in term[0]["local_key"]


def test_terminology_registered_variant_not_flagged():
    """已登记变体不触发术语问题。"""
    snap = make_snapshot(
        draft_text="他握紧护符",
        terms=[{"canonical": "符咒", "variants": ["护符"]}],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    assert not [i for i in issues if i["dimension"] == "term"]


def test_world_rule_violation_flagged():
    """世界硬规则违反 -> critical, dimension=rule。"""
    snap = make_snapshot(
        draft_text="一名普通人施展魔法摧毁了城墙",
        world_rules=[{
            "rule_key": "magic-ban",
            "rule_text": "普通人不得施展魔法",
            "forbidden_patterns": ["普通人施展魔法"],
        }],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    rule = [i for i in issues if i["dimension"] == "rule"]
    assert len(rule) == 1
    assert rule[0]["severity"] == "critical"


def test_unknown_character_flagged():
    """人物存在性：未登记人物出现并行动 -> medium, dimension=character。"""
    snap = make_snapshot(
        draft_text="神秘客推门而入",
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    ch = [i for i in issues if i["dimension"] == "character"]
    assert len(ch) == 1
    assert ch[0]["severity"] == "medium"
    assert "神秘客" in ch[0]["local_key"]


def test_rules_are_deterministic():
    """相同输入产生完全相同的输出（确定性）。"""
    snap = make_snapshot(
        draft_text="阿明说了一句话",
        characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    manifest = make_manifest()
    first = run_deterministic_rules(snap, manifest)
    second = run_deterministic_rules(snap, manifest)
    assert first == second
    json.dumps(first)  # 输出必须可序列化


def test_rules_config_can_disable_checks():
    """规则配置可关闭指定维度检查。"""
    snap = make_snapshot(
        draft_text="阿明说了一句话",
        characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest(), {"state_checks": False})
    assert not [i for i in issues if i["dimension"] == "state"]


def test_rules_do_not_write_database(db):
    """规则结果不写入 Canon（规则函数无 session 依赖，调用后库中数据不变）。"""
    from app.db.models import CanonFact

    before = db.query(CanonFact).count()
    snap = make_snapshot(
        draft_text="阿明说了一句话",
        characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    issues = run_deterministic_rules(snap, make_manifest())
    assert issues
    assert db.query(CanonFact).count() == before


@pytest.mark.parametrize(
    "draft,expected_dimension",
    [
        ("阿明说了一句话", "state"),
        ("阿明抵达南城", "location"),
    ],
)
def test_issues_carry_full_contract_fields(draft, expected_dimension):
    """每条 ReviewIssue 都带 local_key/severity/dimension/text_locator/evidence/修复建议。"""
    if expected_dimension == "state":
        snap = make_snapshot(
            draft_text=draft,
            characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
            known_names=["阿明"],
        )
    else:
        snap = make_snapshot(
            draft_text=draft,
            characters=[{"name": "阿明", "state": "alive", "last_seen_location": "北城"}],
            locations=[{"name": "南城", "reachable_from": ["东城"]}],
            known_names=["阿明"],
        )
    issues = run_deterministic_rules(snap, make_manifest())
    issue = next(i for i in issues if i["dimension"] == expected_dimension)
    assert issue["local_key"]
    assert issue["severity"] in {"low", "medium", "high", "critical"}
    assert issue["text_locator"]
    assert issue["text_locator"].get("quote")
    assert issue["evidence_refs"]
    assert issue["message"]
    assert issue["suggested_fix"]
    assert issue["status"] == "pending"
