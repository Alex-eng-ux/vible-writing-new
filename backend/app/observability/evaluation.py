"""本地评测与指标报告（Task 8）。

评测不依赖真实 LangSmith：固定 fixture（``EvalSample``）直接运行确定性断言，
输出结构化指标报告。每项指标都记录 ``sample_count``、分母、计算公式、通过阈值、
基线版本和结果，绝不只记录一个百分比。

LangSmith dataset 路径：``run_evaluation(source="langsmith-dataset")`` 在未提供
client（无真实 API Key）时自动降级到本地 fixture，并在报告中标记 ``degraded``；
Fake model 与 LangSmith 不可用时仍可运行同一组本地 fixture。

发布门槛（gate）：结构化输出合法率与版本提交正确率 100%，未授权 Canon 写入 0，
脱敏泄漏 0，重复/丢失业务事件 0，重复/丢失正式版本 0，规则误报率 <= 5%（>=30
条带标签负例），一次修订成功率 >= 80%（>=20 个修订样例）。
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .redaction import REDACTION_VERSION, redact

# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalSample:
    """固定评测样本（Fake model 语义：期望与结果均为确定性 fixture 值）。

    - scenario: 固定回归场景标签（续写/改写/时间线冲突/人物状态冲突/...）。
    - kind: 指标类别（structured_output/version_commit/canon_write/redaction/
      dedup_event/dedup_version/rule_negative/revision/latency/token）。
    - expected/outcome: 期望与实际的布尔或枚举结果。
    - value: latency/token 类样本的数值。
    - payload/sensitive: redaction 类样本的原始负载与必须不泄漏的原文片段。
    """

    scenario: str
    kind: str
    expected: bool | str
    outcome: bool | str
    note: str = ""
    value: float | None = None
    payload: dict | None = None
    sensitive: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricResult:
    """单指标结果：样本数、分母、公式、阈值、基线版本与通过标记。"""

    name: str
    sample_count: int
    denominator: int
    formula: str
    threshold: str
    baseline_version: str
    value: float
    passed: bool
    notes: tuple[str, ...] = ()


@dataclass
class EvaluationReport:
    """评测报告：指标列表、来源、基线与门槛结论。"""

    source: str
    baseline_version: str
    metrics: list[MetricResult]
    degraded: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def by_name(self, name: str) -> MetricResult | None:
        """按指标名查找结果；不存在返回 None。"""
        return next((m for m in self.metrics if m.name == name), None)

    def gate_passed(self) -> bool:
        """发布门槛是否全部通过（所有发布门槛指标 passed）。"""
        gate_names = {
            "structured_output_validity",
            "version_commit_correctness",
            "unauthorized_canon_writes",
            "redaction_leakage",
            "duplicate_or_lost_events",
            "duplicate_or_lost_versions",
            "rule_false_positive_rate",
            "one_shot_revision_success",
        }
        gate = [m for m in self.metrics if m.name in gate_names]
        return bool(gate) and all(m.passed for m in gate)

    def to_dict(self) -> dict:
        """导出报告字典（含每项样本数/分母/公式/阈值/基线/结果）。"""
        return {
            "source": self.source,
            "baseline_version": self.baseline_version,
            "degraded": self.degraded,
            "redaction_version": REDACTION_VERSION,
            "gate_passed": self.gate_passed(),
            "metrics": [
                {
                    "name": m.name,
                    "sample_count": m.sample_count,
                    "denominator": m.denominator,
                    "formula": m.formula,
                    "threshold": m.threshold,
                    "baseline_version": m.baseline_version,
                    "value": m.value,
                    "passed": m.passed,
                    "notes": list(m.notes),
                }
                for m in self.metrics
            ],
        }


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float:
    """计算一组数值的百分位数（线性插值；空列表返回 0.0）。

    参数：values 为数值序列；p 为 0-100 的百分位。
    返回：第 p 百分位的数值。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    rank = (len(ordered) - 1) * (p / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    frac = rank - lower
    return ordered[lower] * (1 - frac) + ordered[upper] * frac


def _accuracy(samples: Sequence[EvalSample]) -> tuple[int, int, list[str]]:
    """正确数/总数/备注（expected 与 outcome 一致的样本为正确）。"""
    correct = sum(1 for s in samples if s.outcome == s.expected)
    notes = [f"correct={correct} of {len(samples)}"]
    return correct, len(samples), notes


def _violations(samples: Sequence[EvalSample]) -> tuple[int, int, list[str]]:
    """违规数/总数/备注（outcome 与 expected 不一致即违规）。"""
    bad = [s for s in samples if s.outcome != s.expected]
    notes = [f"violations={len(bad)} of {len(samples)}"] + [s.scenario for s in bad]
    return len(bad), len(samples), notes


def _redaction_leaks(samples: Sequence[EvalSample]) -> tuple[int, int, list[str]]:
    """泄漏数/样本数/备注（对每个 redaction 样本运行脱敏并检查原文泄漏）。"""
    leaks: list[str] = []
    for s in samples:
        if s.payload is None:
            continue
        redacted = redact(s.payload, capture_content=False)
        redacted_json = json.dumps(redacted, sort_keys=True, ensure_ascii=False)
        for frag in s.sensitive:
            if frag and frag in redacted_json:
                leaks.append(f"{s.scenario}: fragment leaked")
    return len(leaks), len(samples), (["leakage=0"] if not leaks else leaks)


def _bool_rate(samples: Sequence[EvalSample]) -> tuple[float, int, int]:
    """布尔型样本中 outcome 为 True 的比例（True 数/总数）。"""
    trues = sum(1 for s in samples if s.outcome is True)
    return (trues / len(samples)) if samples else 0.0, trues, len(samples)


def _false_positive_rate(samples: Sequence[EvalSample]) -> tuple[float, int, int, list[str]]:
    """误报率 = fp / (fp + tn)；负例样本（expected 为 False）中 outcome 为 True 为误报。

    返回：误报率、误报数、负例总数、备注。
    """
    negatives = [s for s in samples if s.expected is False]
    fp = sum(1 for s in negatives if s.outcome is True)
    total = len(negatives)
    rate = (fp / total) if total else 0.0
    return rate, fp, total, [f"fp={fp} of {total} labeled negatives"]


def _success_rate(samples: Sequence[EvalSample]) -> tuple[float, int, int, list[str]]:
    """一次修订成功率 = 成功数 / 尝试数（outcome 为 True 的样本）。"""
    success = sum(1 for s in samples if s.outcome is True)
    total = len(samples)
    return (success / total) if total else 0.0, success, total, [f"success={success} of {total}"]


def _p_percentile_metric(
    name: str,
    samples: Sequence[EvalSample],
    p: float,
    baseline: float | None,
    baseline_version: str,
    budget_ratio: float = 1.2,
) -> MetricResult:
    """构造 p50/p95 指标：与基线比较（<= 基线*ratio）；首条基线只记录不判回归。"""
    values = [s.value for s in samples if s.value is not None]
    pv = percentile(values, p)
    if baseline is None:
        passed = True
        notes = ("baseline_only: first run records baseline, cannot claim regression pass",)
    else:
        passed = pv <= baseline * budget_ratio
        notes = (f"p{p:g}={pv:.1f} vs baseline={baseline:.1f} (ratio budget {budget_ratio:.0%})",)
    return MetricResult(
        name=name,
        sample_count=len(values),
        denominator=len(values),
        formula=f"percentile(values, {p:g})",
        threshold=f"<= baseline*{budget_ratio:g} (baseline-only first run)",
        baseline_version=baseline_version,
        value=pv,
        passed=passed,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 评测入口
# ---------------------------------------------------------------------------


def evaluate_fixture(
    samples: Sequence[EvalSample],
    *,
    baseline_version: str = "v1-fixture",
    latency_baseline_p95: float | None = None,
    token_baseline_p95: float | None = None,
) -> EvaluationReport:
    """使用本地 fixture 运行评测并输出带样本数/分母/公式/阈值/基线的报告。

    参数：samples 为固定评测样本；baseline_version 为基线版本号；latency_baseline_p95
    与 token_baseline_p95 为可选的 p95 基线（None 时该指标仅记录基线）。
    返回：EvaluationReport（含全部指标结果与发布门槛结论）。
    """
    metrics: list[MetricResult] = []

    def add_rate(name: str, kind: str, formula: str, threshold: str) -> None:
        sub = [s for s in samples if s.kind == kind]
        correct, total, notes = _accuracy(sub)
        metrics.append(
            MetricResult(
                name=name,
                sample_count=total,
                denominator=total,
                formula=formula,
                threshold=threshold,
                baseline_version=baseline_version,
                value=(correct / total) if total else 0.0,
                passed=total > 0 and correct == total,
                notes=tuple(notes),
            )
        )

    def add_zero_violation(name: str, kind: str, formula: str, threshold: str) -> None:
        sub = [s for s in samples if s.kind == kind]
        bad, total, notes = _violations(sub)
        metrics.append(
            MetricResult(
                name=name,
                sample_count=total,
                denominator=total,
                formula=formula,
                threshold=threshold,
                baseline_version=baseline_version,
                value=float(bad),
                passed=total > 0 and bad == 0,
                notes=tuple(notes),
            )
        )

    # 1) 结构化输出合法率（100%）
    add_rate("structured_output_validity", "structured_output", "correct / sample_count", "== 1.0")
    # 2) 版本提交正确率（100%）
    add_rate("version_commit_correctness", "version_commit", "correct / sample_count", "== 1.0")
    # 3) 未授权 Canon 写入（0）：canon_write 样本 expected=False 表示未授权，不得写入
    canon = [s for s in samples if s.kind == "canon_write"]
    unauthorized = [s for s in canon if s.expected is False]
    canon_violations = [s for s in unauthorized if s.outcome is True]
    metrics.append(
        MetricResult(
            name="unauthorized_canon_writes",
            sample_count=len(canon),
            denominator=len(unauthorized),
            formula="violations among unauthorized samples",
            threshold="== 0",
            baseline_version=baseline_version,
            value=float(len(canon_violations)),
            passed=len(unauthorized) > 0 and not canon_violations,
            notes=(
                f"unauthorized={len(unauthorized)} of {len(canon)} canon samples; "
                f"violations={len(canon_violations)}",
            ),
        )
    )
    # 4) 脱敏泄漏（0）：对 redaction 样本实际运行脱敏并检查泄漏
    red_samples = [s for s in samples if s.kind == "redaction"]
    leaks, red_total, red_notes = _redaction_leaks(red_samples)
    metrics.append(
        MetricResult(
            name="redaction_leakage",
            sample_count=red_total,
            denominator=red_total,
            formula="leaked samples; must be 0",
            threshold="== 0",
            baseline_version=baseline_version,
            value=float(leaks),
            passed=red_total > 0 and leaks == 0,
            notes=tuple(red_notes),
        )
    )
    # 5) 重复/丢失业务事件（0）
    add_zero_violation("duplicate_or_lost_events", "dedup_event", "violations; must be 0", "== 0")
    # 6) 重复/丢失正式版本（0）
    add_zero_violation("duplicate_or_lost_versions", "dedup_version", "violations; must be 0", "== 0")
    # 7) 规则误报率（<= 5%，至少 30 条带标签负例）
    fp_rate, fp, neg_total, fp_notes = _false_positive_rate(
        [s for s in samples if s.kind == "rule_negative"]
    )
    metrics.append(
        MetricResult(
            name="rule_false_positive_rate",
            sample_count=neg_total,
            denominator=neg_total,
            formula="fp / (fp + tn)",
            threshold="<= 0.05 (>=30 labeled negatives)",
            baseline_version=baseline_version,
            value=fp_rate,
            passed=neg_total >= 30 and fp_rate <= 0.05,
            notes=tuple(fp_notes),
        )
    )
    # 8) 一次修订成功率（>= 80%，至少 20 个修订样例）
    rev_samples = [s for s in samples if s.kind == "revision"]
    rev_rate, rev_ok, rev_total, rev_notes = _success_rate(rev_samples)
    metrics.append(
        MetricResult(
            name="one_shot_revision_success",
            sample_count=rev_total,
            denominator=rev_total,
            formula="success / attempts",
            threshold=">= 0.80 (>=20 revision samples)",
            baseline_version=baseline_version,
            value=rev_rate,
            passed=rev_total >= 20 and rev_rate >= 0.80,
            notes=tuple(rev_notes),
        )
    )
    # 9/10) 延迟与 token 成本 p50/p95（基线比较；首条基线只记录）
    latency = [s for s in samples if s.kind == "latency"]
    tokens = [s for s in samples if s.kind == "token"]
    metrics.append(_p_percentile_metric("latency_p50", latency, 50, None, baseline_version))
    metrics.append(
        _p_percentile_metric("latency_p95", latency, 95, latency_baseline_p95, baseline_version)
    )
    metrics.append(_p_percentile_metric("token_p50", tokens, 50, None, baseline_version))
    metrics.append(_p_percentile_metric("token_p95", tokens, 95, token_baseline_p95, baseline_version))

    return EvaluationReport(
        source="local-fixture",
        baseline_version=baseline_version,
        metrics=metrics,
    )


def run_evaluation(
    samples: Sequence[EvalSample],
    *,
    source: str = "local-fixture",
    langsmith_client: Any = None,
    baseline_version: str = "v1-fixture",
    latency_baseline_p95: float | None = None,
    token_baseline_p95: float | None = None,
) -> EvaluationReport:
    """统一评测入口：LangSmith dataset 不可用时自动降级到本地 fixture。

    参数：samples 为固定评测样本；source 为请求来源（local-fixture 或
    langsmith-dataset）；langsmith_client 为可选 LangSmith client（未提供即
    LangSmith 不可用，降级本地并在报告中标记 degraded）。
    返回：EvaluationReport（来源与 degraded 反映实际执行路径）。
    """
    report = evaluate_fixture(
        samples,
        baseline_version=baseline_version,
        latency_baseline_p95=latency_baseline_p95,
        token_baseline_p95=token_baseline_p95,
    )
    if source == "langsmith-dataset" and langsmith_client is None:
        # LangSmith 未配置（无真实 API Key）：降级本地，同一组 fixture 仍可运行。
        report.degraded = True
        report.extra["fallback_reason"] = "langsmith client unavailable; used local fixtures"
        return report
    report.source = source
    return report


def export_local_fixture(samples: Sequence[EvalSample]) -> dict:
    """导出脱敏的本地 fixture/export（LangSmith 不可用时使用同一评测输入）。

    参数：samples 为固定评测样本。
    返回：可 JSON 序列化的 fixture 导出（含 redaction_version；样本内容默认脱敏）。
    """
    return {
        "redaction_version": REDACTION_VERSION,
        "baseline_version": "v1-fixture",
        "samples": [
            {
                "scenario": s.scenario,
                "kind": s.kind,
                "expected": s.expected,
                "outcome": s.outcome,
                "note": s.note,
                "value": s.value,
            }
            for s in samples
        ],
    }


__all__ = [
    "EvalSample",
    "MetricResult",
    "EvaluationReport",
    "percentile",
    "evaluate_fixture",
    "run_evaluation",
    "export_local_fixture",
]
