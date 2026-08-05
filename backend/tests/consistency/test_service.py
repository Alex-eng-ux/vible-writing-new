"""Task 6 一致性服务测试：问题合并、严重级别路由、一次自动修订与作者反馈转移。

覆盖：相同问题稳定合并（保留历史状态）、高风险问题阻止自动修订、低风险
问题最多自动修订一次、无问题直接接受、无证据问题阻断自动修订、规则结果
不写入 Canon。
"""

from __future__ import annotations

import pytest

from app.consistency.service import (
    merge_review_issues,
    route_review_issues,
    run_consistency_checks,
    validate_review_issue,
)

from .conftest import make_issue, make_manifest, make_snapshot


def test_merge_dedupes_same_issue_and_keeps_history():
    """相同问题（同指纹）合并只保留一条，且保留既有处理状态。"""
    existing = [make_issue(status="accepted")]
    new_issues = [make_issue(status="pending")]
    merged = merge_review_issues(existing, new_issues)
    assert len(merged) == 1
    assert merged[0]["status"] == "accepted"


def test_merge_keeps_distinct_issues():
    """不同问题（不同 local_key）不合并。"""
    merged = merge_review_issues(
        [make_issue(local_key="lk-a")],
        [make_issue(local_key="lk-b")],
    )
    assert len(merged) == 2


def test_merge_keeps_order_and_appends_new():
    """已有问题在前的顺序保持，新问题追加。"""
    first = make_issue(local_key="lk-a")
    second = make_issue(local_key="lk-b")
    merged = merge_review_issues([first], [second])
    assert [i["local_key"] for i in merged] == ["lk-a", "lk-b"]


def test_high_issue_blocks_auto_revision():
    """high 问题必须转作者反馈，不触发自动修订且不递增计数。"""
    issues = [make_issue(severity="high", dimension="state")]
    result = route_review_issues(issues, 0)
    assert result["outcome"] == "waiting_feedback"
    assert result["scene_auto_revision_count"] == 0


def test_critical_issue_blocks_auto_revision():
    """critical 问题同样必须转作者反馈。"""
    issues = [make_issue(severity="critical", dimension="rule")]
    result = route_review_issues(issues, 1)
    assert result["outcome"] == "waiting_feedback"
    assert result["scene_auto_revision_count"] == 1


def test_low_issue_auto_revision_only_once():
    """低风险问题在当前运行最多触发一次自动修订，之后转作者反馈。"""
    issues = [make_issue(severity="low")]
    first = route_review_issues(issues, 0)
    assert first["outcome"] == "auto_revision"
    assert first["scene_auto_revision_count"] == 1
    # 同运行第二次路由：即使仍是同一批问题也不再自动修订。
    second = route_review_issues(issues, first["scene_auto_revision_count"])
    assert second["outcome"] == "waiting_feedback"
    assert second["scene_auto_revision_count"] == 1


def test_medium_issue_auto_revision_only_once():
    """medium 问题同样最多一次自动修订。"""
    issues = [make_issue(severity="medium")]
    first = route_review_issues(issues, 0)
    assert first["outcome"] == "auto_revision"
    assert first["scene_auto_revision_count"] == 1
    second = route_review_issues(issues, 1)
    assert second["outcome"] == "waiting_feedback"


def test_no_issues_routes_to_accepted():
    """无问题直接接受。"""
    result = route_review_issues([], 2)
    assert result["outcome"] == "accepted"
    assert result["issues"] == []


def test_missing_evidence_blocks_auto_revision():
    """缺少证据的问题不能进入自动修订（路由前校验拒绝）。"""
    with pytest.raises(ValueError):
        route_review_issues([make_issue(evidence_refs=[])], 0)


def test_missing_locator_blocks_auto_revision():
    """缺少正文定位的问题不能进入自动修订（路由前校验拒绝）。"""
    with pytest.raises(ValueError):
        route_review_issues([make_issue(text_locator={})], 0)


def test_run_consistency_checks_returns_valid_issues():
    """run_consistency_checks 返回通过完整校验的问题。"""
    snap = make_snapshot(
        draft_text="阿明说了一句话",
        characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    manifest = make_manifest()
    issues = run_consistency_checks(snap, manifest)
    assert issues
    for issue in issues:
        validate_review_issue(issue, manifest)


def test_run_consistency_checks_respects_config():
    """关闭 state 检查后，死亡人物行动不再产生 state 问题。"""
    snap = make_snapshot(
        draft_text="阿明说了一句话",
        characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    issues = run_consistency_checks(snap, make_manifest(), {"state_checks": False})
    assert not [i for i in issues if i["dimension"] == "state"]


def test_rules_result_does_not_write_canon(db):
    """规则结果不写入 Canon：调用服务后正式 Canon 数据保持不变。"""
    from app.db.models import CanonFact

    before = db.query(CanonFact).count()
    snap = make_snapshot(
        draft_text="阿明说了一句话",
        characters=[{"name": "阿明", "state": "dead", "last_seen_location": "北城"}],
        known_names=["阿明"],
    )
    issues = run_consistency_checks(snap, make_manifest())
    assert issues
    db.commit()
    assert db.query(CanonFact).count() == before
