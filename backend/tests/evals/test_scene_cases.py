"""Task 8 固定场景案例评测：验证发布门槛指标全部通过。

评测运行在本地 fixture 上（不依赖真实 LangSmith API Key），输出带样本数/分母/
公式/阈值/基线的报告；LangSmith dataset 路径在无 client 时降级本地。
"""
from __future__ import annotations

import json

from app.observability.evaluation import (
    EvaluationReport,
    MetricResult,
    evaluate_fixture,
    export_local_fixture,
    run_evaluation,
)

from .fixtures import regression_samples


def _metric(report: EvaluationReport, name: str) -> MetricResult:
    """按名取指标；不存在时断言失败（保证后续访问非空）。"""
    m = report.by_name(name)
    assert m is not None, f"metric {name!r} missing in report"
    return m


def test_local_fixture_gate_passes() -> None:
    """固定回归样例在本地评测上全部通过发布门槛。"""
    report = evaluate_fixture(regression_samples())
    assert report.gate_passed() is True

    # 结构化输出合法率与版本提交正确率必须为 100%。
    assert _metric(report, "structured_output_validity").value == 1.0
    assert _metric(report, "version_commit_correctness").value == 1.0

    # 未授权 Canon 写入 / 脱敏泄漏 / 重复丢失事件与版本均为 0。
    assert _metric(report, "unauthorized_canon_writes").value == 0
    assert _metric(report, "redaction_leakage").value == 0
    assert _metric(report, "duplicate_or_lost_events").value == 0
    assert _metric(report, "duplicate_or_lost_versions").value == 0

    # 规则误报率 <= 5%（>=30 条负例）；一次修订成功率 >= 80%（>=20 样例）。
    fp_metric = _metric(report, "rule_false_positive_rate")
    assert fp_metric.sample_count >= 30
    assert fp_metric.value <= 0.05
    rev_metric = _metric(report, "one_shot_revision_success")
    assert rev_metric.sample_count >= 20
    assert rev_metric.value >= 0.80


def test_report_records_samples_denominator_formula_threshold_baseline() -> None:
    """报告每项指标都记录样本数/分母/公式/阈值/基线版本与结果。"""
    report = evaluate_fixture(regression_samples())
    data = report.to_dict()
    assert data["baseline_version"] == "v1-fixture"
    assert data["redaction_version"] == "redaction.v1"
    assert data["gate_passed"] is True
    assert len(data["metrics"]) >= 8
    for metric in data["metrics"]:
        # 不得只记录一个百分比：样本数、分母、公式、阈值、基线、结果都齐备。
        assert metric["sample_count"] > 0
        assert metric["denominator"] > 0
        assert metric["formula"]
        assert metric["threshold"]
        assert metric["baseline_version"] == "v1-fixture"
        assert isinstance(metric["value"], int | float)
        assert isinstance(metric["passed"], bool)


def test_langsmith_unavailable_falls_back_to_local() -> None:
    """LangSmith 不可用（无真实 API Key）时降级本地 fixture，同一组样例仍可评测。"""
    report = run_evaluation(regression_samples(), source="langsmith-dataset", langsmith_client=None)
    assert isinstance(report, EvaluationReport)
    assert report.degraded is True
    assert report.source == "local-fixture"
    assert report.gate_passed() is True  # 降级不影响评测结果
    assert "fallback_reason" in report.extra


def test_local_export_is_json_serializable() -> None:
    """脱敏本地 fixture 导出可 JSON 序列化并携带规则版本。"""
    exported = export_local_fixture(regression_samples())
    blob = json.dumps(exported, ensure_ascii=False)
    assert exported["redaction_version"] == "redaction.v1"
    assert len(exported["samples"]) == len(regression_samples())
    # 导出内容不含任何敏感原文。
    assert "林默推开星门" not in blob
