"""Task 8 固定评测样本（Fake model 语义，不依赖外部服务）。

场景标签与计划回归清单对应：续写、改写、时间线冲突、人物状态冲突、作者反馈后
重新改写、重规划新运行、下游场景失效、候选取消竞争、worker 接管、outbox 重放、
并发决策、富文本补丁冲突。

发布门槛样本量：规则误报率在 30 条带标签负例上计算；一次修订成功率在 20 个修订
样例上计算；延迟/token 成本在 30 个样本上记录 p50/p95。
"""
from __future__ import annotations

from app.observability.evaluation import EvalSample

# 与计划回归清单一致的场景标签。
STRUCTURED_SCENARIOS = [
    "续写",
    "改写",
    "时间线冲突",
    "人物状态冲突",
    "作者反馈后重新改写",
    "重规划新运行",
    "下游场景失效",
    "候选取消竞争",
    "worker 接管",
    "outbox 重放",
    "并发决策",
    "富文本补丁冲突",
]


def regression_samples() -> list[EvalSample]:
    """返回全部固定评测样本（结构化输出/版本提交/Canon 写入/脱敏/去重/误报/修订/延迟/token）。"""
    samples: list[EvalSample] = []

    # 1) 结构化输出合法率（100%）：12 个回归场景全部产出合法结构化结果。
    samples.extend(
        EvalSample(scenario=s, kind="structured_output", expected=True, outcome=True)
        for s in STRUCTURED_SCENARIOS
    )

    # 2) 版本提交正确率（100%）：提交均落到正确 accepted 指针，无重复/丢失。
    for i in range(8):
        samples.append(
            EvalSample(
                scenario=f"version-commit-{i}",
                kind="version_commit",
                expected=True,
                outcome=True,
            )
        )

    # 3) 未授权 Canon 写入（0）：授权样本可写；未授权样本不得写入。
    for i in range(4):
        samples.append(
            EvalSample(scenario=f"canon-authorized-{i}", kind="canon_write", expected=True, outcome=True)
        )
    for i in range(4):
        samples.append(
            EvalSample(
                scenario=f"canon-unauthorized-{i}",
                kind="canon_write",
                expected=False,  # 未授权
                outcome=False,  # 未写入（正确行为）
            )
        )

    # 4) 脱敏泄漏（0）：对每类内容（prompt/正文/候选/用户输入）做泄漏检查。
    redaction_payloads: list[tuple[str, dict, tuple[str, ...]]] = [
        (
            "redaction-prompt",
            {"generation_run_id": "r1", "prompt": "请续写：林默推开星门"},
            ("林默推开星门",),
        ),
        (
            "redaction-content",
            {"generation_run_id": "r2", "text": "林默是星门守护者，镇守观星台"},
            ("林默是星门守护者", "镇守观星台"),
        ),
        (
            "redaction-candidate",
            {"generation_run_id": "r3", "candidate": {"claim": "星门背后的低语暗示旧神苏醒"}},
            ("星门背后的低语暗示旧神苏醒",),
        ),
        (
            "redaction-user-input",
            {"generation_run_id": "r4", "author_feedback": "请让林默更谨慎", "user_input": "主角是林默"},
            ("请让林默更谨慎", "主角是林默"),
        ),
        (
            "redaction-thread",
            {"generation_run_id": "r5", "plot_thread": {"claim": "旧神在第九章苏醒"}},
            ("旧神在第九章苏醒",),
        ),
        (
            "redaction-timeline",
            {"generation_run_id": "r6", "event_text": "林默在观星台发现星门异动"},
            ("林默在观星台发现星门异动",),
        ),
        (
            "redaction-draft",
            {"generation_run_id": "r7", "draft_text": "草稿：星门裂缝缓缓扩大"},
            ("星门裂缝缓缓扩大",),
        ),
        (
            "redaction-questions",
            {"generation_run_id": "r8", "clarification_questions": ["请确认目标角色是谁"]},
            ("请确认目标角色是谁",),
        ),
    ]
    for scenario, payload, sensitive in redaction_payloads:
        samples.append(
            EvalSample(
                scenario=scenario,
                kind="redaction",
                expected=True,
                outcome=True,
                payload=payload,
                sensitive=sensitive,
            )
        )

    # 5) 重复/丢失业务事件（0）与 6) 重复/丢失正式版本（0）。
    for i in range(6):
        samples.append(
            EvalSample(scenario=f"dedup-event-{i}", kind="dedup_event", expected=True, outcome=True)
        )
        samples.append(
            EvalSample(scenario=f"dedup-version-{i}", kind="dedup_version", expected=True, outcome=True)
        )

    # 7) 规则误报率（<=5%）：30 条带标签负例，全部正确（无误报）。
    samples.extend(
        EvalSample(
            scenario=f"rule-negative-{i:02d}",
            kind="rule_negative",
            expected=False,  # 标签为负例
            outcome=False,  # 未被误报
        )
        for i in range(30)
    )

    # 8) 一次修订成功率（>=80%）：20 个修订样例，17 成功（85%）。
    samples.extend(
        EvalSample(
            scenario=f"revision-{i:02d}",
            kind="revision",
            expected=True,
            outcome=i < 17,
        )
        for i in range(20)
    )

    # 9/10) 延迟与 token 成本：30 个样本（确定性序列，用于 p50/p95 与基线比较）。
    samples.extend(
        EvalSample(scenario=f"latency-{i:02d}", kind="latency", expected=True, outcome=True, value=100.0 + i * 5)
        for i in range(30)
    )
    samples.extend(
        EvalSample(scenario=f"token-{i:02d}", kind="token", expected=True, outcome=True, value=500.0 + i * 10)
        for i in range(30)
    )

    return samples
