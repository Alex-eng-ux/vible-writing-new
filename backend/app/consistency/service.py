"""一致性检查与建议审阅服务（Task 6）。

编排规则输入快照、确定性规则执行、ReviewIssue 合并与作者反馈路由。

核心约束：
- 只读取显式版本快照（ConsistencySnapshot）与 ContextManifest；不读取
  当前最新版本、不写数据库、不修改 Canon，也不创建正式版本；
- 每条 ReviewIssue 必须包含 local_key/severity/dimension/text_locator/
  evidence_refs/修复建议；缺少证据或正文定位的问题不得进入自动修订；
- low|medium 问题在当前运行最多触发一次自动修订；high|critical 必须
  转作者反馈，规则服务本身绝不直接提交版本；
- 问题合并使用稳定指纹（local_key + dimension + text_locator +
  evidence_refs），重复问题保留历史状态，不生成第二条等价问题。
"""

from __future__ import annotations

import hashlib
import json

from app.consistency.rules import run_deterministic_rules
from app.consistency.schemas import (
    ConsistencySnapshot,
    ReviewIssue,
    RouteResult,
    RuleConfig,
)
from app.context.models import ContextManifest

# 严重级别排序：数值越大越严重。
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_VALID_SEVERITIES = frozenset(_SEVERITY_RANK)
_VALID_DIMENSIONS = frozenset({"character", "location", "timeline", "state", "rule", "term"})


def _has_usable_locator(text_locator: dict) -> bool:
    """判断 text_locator 是否包含可用的正文定位信息。

    允许：非空引文（quote），或非零字符区间（char_start/char_end），或
    结构化锚点（anchor_id/paragraph_ref/char_anchor 等显式键）。
    """
    if not text_locator:
        return False
    if text_locator.get("quote"):
        return True
    if text_locator.get("char_start") or text_locator.get("char_end"):
        return True
    return any(k in text_locator for k in ("anchor_id", "paragraph_ref", "char_anchor"))


def validate_review_issue(
    issue: ReviewIssue,
    manifest: ContextManifest | None = None,
) -> None:
    """校验一条 ReviewIssue 是否合法且可进入自动修订。

    参数：
        issue: 待校验的结构化审阅问题。
        manifest: 当前运行来源清单；提供时校验每条证据引用都在清单内。

    失败条件（抛 ValueError，稳定校验错误）：
        - local_key 为空或缺失；
        - severity 不在 low|medium|high|critical；
        - dimension 不在已知维度集合；
        - text_locator 缺失或无可用的正文定位；
        - evidence_refs 为空（无依据指控）；
        - 提供 manifest 时 evidence_refs 引用了未登记来源；
        - message 或 suggested_fix 缺失。

    缺少证据或正文定位的问题不得进入自动修订（route 前必须通过本校验）。
    """
    if not issue.get("local_key"):
        raise ValueError("invalid ReviewIssue: local_key is required")
    if issue.get("severity") not in _VALID_SEVERITIES:
        raise ValueError(
            f"invalid ReviewIssue: severity must be one of {sorted(_VALID_SEVERITIES)}"
        )
    if issue.get("dimension") not in _VALID_DIMENSIONS:
        raise ValueError(
            f"invalid ReviewIssue: dimension must be one of {sorted(_VALID_DIMENSIONS)}"
        )
    if not _has_usable_locator(issue.get("text_locator") or {}):
        raise ValueError("invalid ReviewIssue: text_locator must locate the text")
    evidence = issue.get("evidence_refs") or []
    if not evidence:
        raise ValueError(
            "invalid ReviewIssue: evidence_refs is required (no unsupported claims)"
        )
    if manifest is not None:
        registered = {e["source_id"] for e in manifest.get("entries", [])}
        unknown = [ref for ref in evidence if ref not in registered]
        if unknown:
            raise ValueError(
                f"invalid ReviewIssue: evidence_refs reference unregistered sources {unknown}"
            )
    if not issue.get("message"):
        raise ValueError("invalid ReviewIssue: message is required")
    if not issue.get("suggested_fix"):
        raise ValueError("invalid ReviewIssue: suggested_fix is required")


def _issue_fingerprint(issue: ReviewIssue) -> str:
    """计算问题的稳定合并指纹：local_key + dimension + text_locator + evidence_refs。

    同一问题（同一输出内重复出现或规则重复命中）产生相同指纹；指纹不含
    message/suggested_fix/status，保证只有实质定位/证据/维度变化才算新问题。
    """
    locator = issue.get("text_locator") or {}
    canonical = json.dumps(
        {
            "local_key": issue.get("local_key"),
            "dimension": issue.get("dimension"),
            "text_locator": {k: locator.get(k) for k in sorted(locator)},
            "evidence_refs": sorted(issue.get("evidence_refs") or []),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_review_issues(
    existing: list[ReviewIssue],
    new_issues: list[ReviewIssue],
) -> list[ReviewIssue]:
    """合并问题列表：按稳定指纹去重，重复问题保留既有状态。

    参数：
        existing: 已有问题列表（可能含历史处理状态）。
        new_issues: 本轮新产生的问题列表。

    返回：去重后的问题列表；相同指纹的问题只保留 existing 中的一条
    （保留其 status），不生成第二条等价问题。
    """
    by_fingerprint: dict[str, ReviewIssue] = {}
    for issue in existing:
        by_fingerprint[_issue_fingerprint(issue)] = issue
    for issue in new_issues:
        fingerprint = _issue_fingerprint(issue)
        if fingerprint in by_fingerprint:
            continue
        by_fingerprint[fingerprint] = issue
    return list(by_fingerprint.values())


def route_review_issues(
    issues: list[ReviewIssue],
    scene_auto_revision_count: int,
) -> RouteResult:
    """按严重级别路由问题，决定自动修订/作者反馈/直接接受。

    参数：
        issues: 待路由的问题列表（必须先通过 validate_review_issue）。
        scene_auto_revision_count: 当前运行内已发生的自动修订次数（只统计
            自动低风险修订；人工反馈不重置该计数）。

    返回：RouteResult（outcome、issues、路由后的自动修订计数）。

    路由规则：
        - 任一 high|critical -> waiting_feedback（必须转作者反馈，规则服务
          不得直接提交版本），计数不变；
        - 无问题 -> accepted，计数不变；
        - 全部为 low|medium -> 计数为 0 时 auto_revision（触发一次自动修订，
          计数 +1）；计数已 >=1 时 waiting_feedback（最多一次自动修订）。
    """
    # 先做字段级校验：缺少证据或正文定位的问题不得进入自动修订。
    for issue in issues:
        validate_review_issue(issue)
    if any(
        _SEVERITY_RANK[issue["severity"]] >= _SEVERITY_RANK["high"]
        for issue in issues
    ):
        return RouteResult(
            outcome="waiting_feedback",
            issues=issues,
            scene_auto_revision_count=scene_auto_revision_count,
        )
    if not issues:
        return RouteResult(
            outcome="accepted",
            issues=[],
            scene_auto_revision_count=scene_auto_revision_count,
        )
    if scene_auto_revision_count == 0:
        return RouteResult(
            outcome="auto_revision",
            issues=issues,
            scene_auto_revision_count=scene_auto_revision_count + 1,
        )
    return RouteResult(
        outcome="waiting_feedback",
        issues=issues,
        scene_auto_revision_count=scene_auto_revision_count,
    )


def run_consistency_checks(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
    config: RuleConfig | None = None,
) -> list[ReviewIssue]:
    """在显式版本快照与来源清单上执行一致性检查。

    参数：
        snapshot: 显式版本快照（人物/地点/时间线/硬规则/术语/正文）。
        manifest: 当前运行的来源清单，规则只从其中选取证据引用。
        config: 规则配置；未提供的维度默认开启。

    返回：通过校验的结构化 ReviewIssue 列表（每条都含定位与证据引用）。

    约束：纯计算，不读取当前最新版本、不写数据库、不修改 Canon；任何
    缺证据/缺定位的非法问题在此处被 validate_review_issue 拒绝。
    """
    issues = run_deterministic_rules(snapshot, manifest, config)
    validated: list[ReviewIssue] = []
    for issue in issues:
        validate_review_issue(issue, manifest)
        validated.append(issue)
    return validated


__all__ = [
    "validate_review_issue",
    "merge_review_issues",
    "route_review_issues",
    "run_consistency_checks",
]
