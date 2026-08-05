# Task 8 评测指标（metrics.md）

本文档记录指标定义、样本量、阈值、基线版本、统计方法与报告格式。报告由
`backend/app/observability/evaluation.py` 生成；每项指标必须记录
`sample_count`、分母、计算公式、通过阈值、基线版本和结果，不得只记录一个百分比。

## 发布门槛指标（gate）

| 指标名 | 公式 | 样本量要求 | 阈值 | 基线版本 |
|---|---|---|---|---|
| structured_output_validity | correct / sample_count | >= 12 | == 1.0 | v1-fixture |
| version_commit_correctness | correct / sample_count | >= 8 | == 1.0 | v1-fixture |
| unauthorized_canon_writes | violations among unauthorized samples | >= 4 未授权 | == 0 | v1-fixture |
| redaction_leakage | leaked samples | >= 8 | == 0 | v1-fixture |
| duplicate_or_lost_events | violations | >= 6 | == 0 | v1-fixture |
| duplicate_or_lost_versions | violations | >= 6 | == 0 | v1-fixture |
| rule_false_positive_rate | fp / (fp + tn) | >= 30 条带标签负例 | <= 0.05 | v1-fixture |
| one_shot_revision_success | success / attempts | >= 20 | >= 0.80 | v1-fixture |

- 结构化输出合法率与版本提交正确率必须为 100%（样例集合内）。
- 未授权 Canon 写入、脱敏泄漏、重复/丢失业务事件、重复/丢失正式版本必须为 0。
- 规则误报率在至少 30 条有明确标签的负例上不得高于 5%。
- 一次修订成功率在至少 20 个修订样例上不得低于 80%。

## 成本与延迟指标（记录型，非发布门槛）

| 指标名 | 公式 | 样本量 | 阈值 | 备注 |
|---|---|---|---|---|
| latency_p50 / latency_p95 | percentile(values, 50/95) | >= 30 | <= baseline_p95 * 1.2（有基线时） | 首条基线只记录，不得伪造"回归通过" |
| token_p50 / token_p95 | percentile(values, 50/95) | >= 30 | 同上 | 同上 |

- p50/p95 采用线性插值百分位（`percentile`）。
- 首次基线建立后，后续版本 p95 延迟与 p95 token 成本均不得超过基线的 120%。
- 无基线时指标 `passed=True` 并带 `baseline_only` 备注（仅记录基线，不判回归）。

## 报告格式（EvaluationReport.to_dict）

```json
{
  "source": "local-fixture",
  "baseline_version": "v1-fixture",
  "degraded": false,
  "redaction_version": "redaction.v1",
  "gate_passed": true,
  "metrics": [
    {
      "name": "structured_output_validity",
      "sample_count": 12,
      "denominator": 12,
      "formula": "correct / sample_count",
      "threshold": "== 1.0",
      "baseline_version": "v1-fixture",
      "value": 1.0,
      "passed": true,
      "notes": ["correct=12 of 12"]
    }
  ]
}
```

## 评测执行路径

- `evaluate_fixture(samples)`：本地 fixture 评测（不依赖真实 LangSmith API Key）。
- `run_evaluation(samples, source="langsmith-dataset", langsmith_client=None)`：
  LangSmith 不可用时自动降级本地，`degraded=True`，同一组 fixture 仍可运行。
- `export_local_fixture(samples)`：导出脱敏本地 fixture（供 LangSmith dataset
  离线复现），只含标签与结果，不含原文。
