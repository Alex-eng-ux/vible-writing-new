"""Task 6 ReviewIssue 契约测试：定位、证据、候选引用与非法问题拒绝。

覆盖：合法问题通过；无依据指控（缺证据）拒绝；缺失定位/证据拒绝；
证据引用未登记来源拒绝；非法 severity/dimension/local_key/修复建议拒绝；
缺少证据/定位的问题不能进入自动修订。
"""

from __future__ import annotations

import pytest

from app.consistency.service import route_review_issues, validate_review_issue

from .conftest import make_issue, make_manifest


def test_valid_issue_passes_validation():
    """合法 ReviewIssue 通过校验（含 manifest 证据核对）。"""
    validate_review_issue(make_issue(), make_manifest())


def test_missing_evidence_rejected():
    """无依据指控：没有任何证据引用的问题被拒绝。"""
    with pytest.raises(ValueError, match="evidence_refs"):
        validate_review_issue(make_issue(evidence_refs=[]), make_manifest())


def test_missing_locator_rejected():
    """缺失正文定位的问题被拒绝。"""
    with pytest.raises(ValueError, match="text_locator"):
        validate_review_issue(make_issue(text_locator={}), make_manifest())


def test_unregistered_evidence_rejected():
    """证据引用未登记在 ContextManifest 的来源被拒绝。"""
    with pytest.raises(ValueError, match="unregistered"):
        validate_review_issue(make_issue(evidence_refs=["ghost-source"]), make_manifest())


def test_missing_local_key_rejected():
    with pytest.raises(ValueError, match="local_key"):
        validate_review_issue(make_issue(local_key=""), make_manifest())


def test_invalid_severity_rejected():
    """severity 不在 low|medium|high|critical 时拒绝（如 Task 4A 的 blocking）。"""
    with pytest.raises(ValueError, match="severity"):
        validate_review_issue(make_issue(severity="blocking"), make_manifest())  # type: ignore[typeddict-item]


def test_invalid_dimension_rejected():
    with pytest.raises(ValueError, match="dimension"):
        validate_review_issue(make_issue(dimension="pacing"), make_manifest())  # type: ignore[typeddict-item]


def test_missing_message_rejected():
    with pytest.raises(ValueError, match="message"):
        validate_review_issue(make_issue(message=""), make_manifest())


def test_missing_suggested_fix_rejected():
    with pytest.raises(ValueError, match="suggested_fix"):
        validate_review_issue(make_issue(suggested_fix=""), make_manifest())


def test_issue_without_evidence_cannot_enter_auto_revision():
    """缺少证据或定位的问题不得进入自动修订（路由前校验阻断）。"""
    with pytest.raises(ValueError):
        route_review_issues([make_issue(evidence_refs=[])], 0)
    with pytest.raises(ValueError):
        route_review_issues([make_issue(text_locator={"quote": ""})], 0)


def test_anchor_locator_accepted():
    """结构化定位（anchor_id/paragraph_ref）也视为有效正文定位。"""
    validate_review_issue(
        make_issue(text_locator={"paragraph_ref": "p3", "anchor_id": "a-1"}),
        make_manifest(),
    )
