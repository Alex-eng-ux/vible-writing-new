# Task 8 固定评测样例（scene-cases.md）

本文件记录固定评测样例、标签、输入来源与预期断言。样例由
`backend/tests/evals/fixtures.py` 提供（Fake model 语义，确定性数据，不依赖外部
服务）；LangSmith 不可用时仍可运行同一组本地 fixture。本文档不保存未脱敏正文。

## 场景标签（与全局计划回归清单一致）

| # | 标签 | 说明 | 指标类别 |
|---|---|---|---|
| 1 | 续写 | 选中片段续写产出合法结构化结果 | structured_output |
| 2 | 改写 | 选中片段改写产出合法结构化结果 | structured_output |
| 3 | 时间线冲突 | 时间线冲突检测输出合法 | structured_output |
| 4 | 人物状态冲突 | 人物状态冲突检测输出合法 | structured_output |
| 5 | 作者反馈后重新改写 | 反馈驱动的重新改写输出合法 | structured_output |
| 6 | 重规划新运行 | 新运行重规划输出合法 | structured_output |
| 7 | 下游场景失效 | 下游场景失效信号合法 | structured_output |
| 8 | 候选取消竞争 | 候选取消竞争处理合法 | structured_output |
| 9 | worker 接管 | worker 接管后继续产出合法结果 | structured_output |
| 10 | outbox 重放 | outbox 重放不重复/不丢失事件 | dedup_event |
| 11 | 并发决策 | 并发决策幂等（无重复版本/事件） | dedup_version / dedup_event |
| 12 | 富文本补丁冲突 | 富文本补丁冲突不产生重复版本 | dedup_version |

## 输入来源与预期断言

- **structured_output / version_commit**：样例携带 `expected=True`（合法/正确）与
  `outcome`（确定性 fixture 结果）；断言 `outcome == expected`，全部正确时指标值
  为 1.0。
- **canon_write**：授权样本 `expected=True`（可写）；未授权样本 `expected=False`
  且 `outcome=False`（不得写入）。断言未授权写入数为 0。
- **redaction**：每类内容（prompt / 正文 / 候选 / 用户输入 / 草稿 / 澄清问题）
  的负载样本携带必须不泄漏的原文片段；评测实际运行 `redact` 后断言片段不出现在
  脱敏输出中（泄漏数为 0）。
- **dedup_event / dedup_version**：`expected=True` 表示无重复/丢失；断言违规数为 0。
- **rule_negative**：30 条带标签负例（`expected=False`），断言误报数/负例总数
  <= 5%。
- **revision**：20 个修订样例（`expected=True`），其中 17 个 `outcome=True`；
  断言成功率 >= 80%。
- **latency / token**：各 30 个确定性数值样本，用于 p50/p95 与基线比较。

## 脱敏边界

- 样例正文与提示词只存在于内存 fixture，不出现在 `docs/evals` 任何文件中。
- 本地导出（`export_local_fixture`）只序列化 `scenario/kind/expected/outcome/
  note/value`，不含 `payload/sensitive` 原文；断言导出 JSON 不含原文片段。
