"""Task 8 指标计算测试：样本计数、公式、p50/p95、阈值与基线比较。

验证评估机制本身正确（样本数/分母/公式/阈值/基线与结果），并包含合成失败样本
确认指标能正确判负（机制不只会返回恒真）。
"""
from __future__ import annotations

from app.observability.evaluation import (
    EvalSample,
    EvaluationReport,
    MetricResult,
    evaluate_fixture,
    percentile,
)

from .fixtures import regression_samples


def _metric(report: EvaluationReport, name: str) -> MetricResult:
    """按名取指标；不存在时断言失败（保证后续访问非空）。"""
    m = report.by_name(name)
    assert m is not None, f"metric {name!r} missing in report"
    return m


def test_percentile_basics() -> None:
    """百分位计算：空/边界/中位数/线性插值。"""
    assert percentile([], 95) == 0.0
    assert percentile([1, 2, 3, 4, 5], 50) == 3.0
    assert percentile([1, 2, 3, 4, 5], 0) == 1.0
    assert percentile([1, 2, 3, 4, 5], 100) == 5.0
    # 线性插值：rank=(4-1)*0.5=1.5 → 2*0.5+3*0.5=2.5
    assert percentile([1, 2, 3, 4], 50) == 2.5
    # p95：values=100..245（步长 5），rank=29*0.95=27.55 → 237.75。
    assert 237.0 <= percentile([100 + i * 5 for i in range(30)], 95) <= 238.5


def test_synthetic_failure_marks_metric_failed() -> None:
    """合成失败样本：指标值 <1.0 且 passed=False（机制不恒真）。"""
    samples = [
        EvalSample(scenario="ok-1", kind="structured_output", expected=True, outcome=True),
        EvalSample(scenario="bad-1", kind="structured_output", expected=True, outcome=False),
    ]
    report = evaluate_fixture(samples)
    metric = _metric(report, "structured_output_validity")
    assert metric.sample_count == 2
    assert metric.denominator == 2
    assert metric.value == 0.5
    assert metric.passed is False
    assert report.gate_passed() is False


def test_false_positive_rate_denominator_and_threshold() -> None:
    """误报率分母为带标签负例数；超过 5% 判负。"""
    good = regression_samples()  # 30 条负例全部正确 → 误报率 0
    report = evaluate_fixture(good)
    fp = _metric(report, "rule_false_positive_rate")
    assert fp.sample_count == 30
    assert fp.denominator == 30
    assert fp.value == 0.0
    assert fp.passed is True

    # 合成 5 条误报负例（30 条中 5 条误报 → 16.7% > 5% 判负）。
    bad = [
        EvalSample(scenario=f"fp-{i}", kind="rule_negative", expected=False, outcome=(i < 5))
        for i in range(30)
    ]
    report2 = evaluate_fixture(bad)
    fp2 = _metric(report2, "rule_false_positive_rate")
    assert fp2.value == 5 / 30
    assert fp2.passed is False


def test_revision_success_rate_threshold() -> None:
    """一次修订成功率按 20 样例计算；低于 80% 判负。"""
    ok = [
        EvalSample(scenario=f"r-{i}", kind="revision", expected=True, outcome=(i < 17))
        for i in range(20)
    ]
    report = evaluate_fixture(ok)
    rev = _metric(report, "one_shot_revision_success")
    assert rev.sample_count == 20
    assert rev.value == 0.85
    assert rev.passed is True

    low = [
        EvalSample(scenario=f"r-{i}", kind="revision", expected=True, outcome=(i < 10))
        for i in range(20)
    ]
    report2 = evaluate_fixture(low)
    assert _metric(report2, "one_shot_revision_success").value == 0.50
    assert _metric(report2, "one_shot_revision_success").passed is False


def test_latency_token_p95_with_baseline() -> None:
    """p95 与基线比较：<= 基线*120% 通过；首条基线只记录不判回归。"""
    samples = [
        EvalSample(scenario=f"l-{i}", kind="latency", expected=True, outcome=True, value=100.0 + i)
        for i in range(30)
    ]
    samples += [
        EvalSample(scenario=f"t-{i}", kind="token", expected=True, outcome=True, value=500.0 + i)
        for i in range(30)
    ]
    # 无基线：只记录，不判回归（passed=True + baseline_only 备注）。
    report = evaluate_fixture(samples)
    metric = _metric(report, "latency_p95")
    assert metric.passed is True
    assert any("baseline_only" in n for n in metric.notes)

    # 基线 p95 = 100：实际 p95（~128）> 120 → 判负。
    report_above = evaluate_fixture(samples, latency_baseline_p95=100.0)
    assert _metric(report_above, "latency_p95").passed is False

    # 基线 p95 = 200：实际 p95 <= 240 → 通过。
    report_within = evaluate_fixture(samples, latency_baseline_p95=200.0)
    assert _metric(report_within, "latency_p95").passed is True

    # token 指标同样带样本数与基线版本。
    token = _metric(report_within, "token_p95")
    assert token.sample_count == 30
    assert token.baseline_version == "v1-fixture"
    assert token.formula == "percentile(values, 95)"
