"""一致性规则引擎实现。

本模块提供确定性的规则引擎（Task 4A 最小实现 + Task 6 扩展），用于对草稿
做场景级候选事实提取与基础语义审查。规则引擎只做纯计算，绝不直接写库、
绝不修改 Canon。

核心约束：
- evaluate 无副作用，不访问数据库，不写任何数据；
- 规则只依据输入 draft_text 判定，输出 RuleIssue 列表与 passed 标志；
- Task 6 规则只读取显式版本快照（ConsistencySnapshot）与 ContextManifest，
  不读取当前最新版本、不写数据库或 Canon，不接入真实模型 API；
- 后续规则集扩展须保持与现有 RuleEngine 协议及输入契约向后兼容。
"""

from __future__ import annotations

import re

from app.consistency.schemas import (
    ConsistencySnapshot,
    ReviewIssue,
    RuleConfig,
    RuleEngine,
    RuleEngineInput,
    RuleEngineOutput,
    RuleIssue,
)
from app.context.models import ContextManifest

# Task 6 维度对应的 manifest 来源类型（用于从 ContextManifest 选取证据引用）。
_EVIDENCE_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "character": ("entity", "canon", "scene"),
    "location": ("entity", "canon", "scene"),
    "timeline": ("timeline", "canon", "scene"),
    "state": ("entity", "canon", "scene"),
    "rule": ("canon", "entity"),
    "term": ("canon", "entity", "style"),
}


class MinimalRuleEngine:
    """Task 4A 最小确定性规则引擎。

    只进行场景级候选事实提取与基础语义审查端口；规则永不写库，
    Task 6 扩展规则集。
    """

    def evaluate(self, input: RuleEngineInput) -> RuleEngineOutput:
        """对输入草稿执行一致性规则评估。

        参数：
            input: 规则输入，含 scene_id、project_id、draft_text 等。

        返回：
            RuleEngineOutput：包含命中的问题列表与是否通过（issues 为空
            即 passed=True）。

        当前规则：
            - min_length：draft_text 去除空白后不足 3 个字符时判定为
              medium 级别问题。
        """
        issues: list[RuleIssue] = []
        if input["draft_text"] and len(input["draft_text"].strip()) < 3:
            issues.append(
                RuleIssue(
                    rule_id="min_length",
                    severity="medium",
                    message="draft is too short",
                    text_locator={"quote": ""},
                )
            )
        return RuleEngineOutput(issues=issues, passed=len(issues) == 0)


def build_rule_engine() -> RuleEngine:
    """构造默认规则引擎实例。

    返回：
        一个 MinimalRuleEngine 实例，作为 RuleEngine 协议注入。
    """
    return MinimalRuleEngine()


# ---------------------------------------------------------------------------
# Task 6：确定性一致性规则（只读快照 + manifest，纯计算）。
# ---------------------------------------------------------------------------

# 行动动词模式：用于从草稿中提取"正在行动的人物名"（确定性近似识别）。
_ACTION_VERBS = (
    "说|道|喊|叫|走|进|来|去|推|开|打|杀|唱|笑|哭|站|坐|看|听|举|拔|点|"
    "飞|跑|跳|死|死去了|死去|倒下"
)
_ACTION_RE = re.compile(rf"([\u4e00-\u9fa5]{{1,4}})(?:{_ACTION_VERBS})")

# 抵达地点模式：{人物}抵达/来到/到达/走进/进入{地点}。
_ARRIVAL_RE = re.compile(
    r"([\u4e00-\u9fa5]{1,4})(?:抵达|来到|到达|走进|进入)([\u4e00-\u9fa5]{1,6})"
)

# 故事内时间模式：第N章/第N日/第N年/N年后/N年前（按倍率换算为可比较数值）。
_TIME_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"第(\d+)章"), 1000),
    (re.compile(r"第(\d+)日"), 100),
    (re.compile(r"第(\d+)年"), 1_000_000),
    (re.compile(r"(\d+)年后"), 1),
    (re.compile(r"(\d+)年前"), -1),
]


def _parse_story_time(value: str) -> int | None:
    """把故事内时间字符串解析为可比较数值；无法解析返回 None。"""
    for pattern, scale in _TIME_PATTERNS:
        match = pattern.search(value)
        if match:
            return int(match.group(1)) * scale
    return None


def _manifest_source_ids(
    manifest: ContextManifest, source_types: tuple[str, ...] | None = None
) -> list[str]:
    """从 ContextManifest 收集证据来源 ID。

    只允许使用 manifest 已登记的来源作为证据（禁止引用草稿或未授权来源）；
    manifest 无匹配条目时返回空列表（validate 会拒绝无证据问题进入自动修订）。
    """
    entries = manifest.get("entries", [])
    if source_types is None:
        return [e["source_id"] for e in entries]
    return [e["source_id"] for e in entries if e.get("source_type") in source_types]


def _locator(draft_text: str, start: int, end: int) -> dict:
    """构造文本定位：引文 + 字符区间。"""
    return {"quote": draft_text[start:end], "char_start": start, "char_end": end}


def _issue(
    *,
    local_key: str,
    severity: str,
    dimension: str,
    text_locator: dict,
    evidence_refs: list[str],
    message: str,
    suggested_fix: str,
) -> ReviewIssue:
    """构造结构化 ReviewIssue（status 固定 pending）。"""
    return ReviewIssue(
        local_key=local_key,
        severity=severity,  # type: ignore[typeddict-item]
        dimension=dimension,  # type: ignore[typeddict-item]
        text_locator=text_locator,
        evidence_refs=evidence_refs,
        message=message,
        suggested_fix=suggested_fix,
        status="pending",
    )


def _check_character_existence(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
    known_names: list[str],
) -> list[ReviewIssue]:
    """人物存在性：草稿中行动的人物若不在任何已知名单内则标记未知人物。

    已知名单 = 快照人物 + manifest 登记的实体名。issue 为 medium 级别
    （人物存在性），证据取自 manifest 的 entity/canon/scene 来源。
    """
    issues: list[ReviewIssue] = []
    draft_text = snapshot["draft_text"]
    known = set(known_names)
    evidence = _manifest_source_ids(manifest, _EVIDENCE_SOURCE_TYPES["character"])
    for match in _ACTION_RE.finditer(draft_text):
        name = match.group(1)
        if name in known:
            continue
        # 已知名单之外出现的行动者 -> 未知人物（确定性近似，避免误伤地点名）。
        issues.append(
            _issue(
                local_key=f"unknown_character:{name}",
                severity="medium",
                dimension="character",
                text_locator=_locator(draft_text, match.start(1), match.end(1)),
                evidence_refs=evidence,
                message=f"草稿中出现未登记人物「{name}」",
                suggested_fix="将该人物补充到人物名单，或改用已登记人物名",
            )
        )
        known.add(name)
    return issues


def _check_character_state(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
) -> list[ReviewIssue]:
    """死亡/离场状态：已死亡或已离场（departed/absent）的人物不得再次行动。"""
    issues: list[ReviewIssue] = []
    draft_text = snapshot["draft_text"]
    evidence = _manifest_source_ids(manifest, _EVIDENCE_SOURCE_TYPES["state"])
    for character in snapshot["characters"]:
        name = character["name"]
        if character["state"] not in ("dead", "departed", "absent"):
            continue
        pos = draft_text.find(name)
        if pos < 0:
            continue
        issues.append(
            _issue(
                local_key=f"state:{name}:acting_after_{character['state']}",
                severity="high",
                dimension="state",
                text_locator=_locator(draft_text, pos, pos + len(name)),
                evidence_refs=evidence,
                message=(
                    f"人物「{name}」状态为 {character['state']}，草稿中却再次出现并行动"
                ),
                suggested_fix="删除该人物的行动，或先在 Canon 中更新其状态为存活",
            )
        )
    return issues


def _check_location_reachability(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
) -> list[ReviewIssue]:
    """地点可达性：角色抵达的地点必须从其最近地点可直接到达。"""
    issues: list[ReviewIssue] = []
    draft_text = snapshot["draft_text"]
    evidence = _manifest_source_ids(manifest, _EVIDENCE_SOURCE_TYPES["location"])
    locations = {loc["name"]: loc for loc in snapshot["locations"]}
    for character in snapshot["characters"]:
        last_seen = character.get("last_seen_location")
        if not last_seen:
            continue
        for match in _ARRIVAL_RE.finditer(draft_text):
            name, dest = match.group(1), match.group(2)
            if name != character["name"]:
                continue
            dest_entry = locations.get(dest)
            if dest_entry is None:
                continue
            if dest == last_seen:
                continue
            reachable = set(dest_entry["reachable_from"])
            if last_seen not in reachable:
                issues.append(
                    _issue(
                        local_key=f"location:{character['name']}:{dest}:unreachable",
                        severity="high",
                        dimension="location",
                        text_locator=_locator(draft_text, match.start(2), match.end(2)),
                        evidence_refs=evidence,
                        message=(
                            f"人物「{name}」从最近地点「{last_seen}」无法直接到达「{dest}」"
                        ),
                        suggested_fix="调整路线或先经过可达中转地点，或更新地点可达关系",
                    )
                )
                break
    return issues


def _check_timeline_order(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
) -> list[ReviewIssue]:
    """时间线先后：草稿时间不得早于快照中已发生的最后事件时间。"""
    issues: list[ReviewIssue] = []
    draft_text = snapshot["draft_text"]
    evidence = _manifest_source_ids(manifest, _EVIDENCE_SOURCE_TYPES["timeline"])
    max_seen: int | None = None
    for entry in snapshot["timeline"]:
        value = _parse_story_time(entry["story_time"])
        if value is not None and (max_seen is None or value > max_seen):
            max_seen = value
    if max_seen is None:
        return issues
    for pattern, _scale in _TIME_PATTERNS:
        for match in pattern.finditer(draft_text):
            value = _parse_story_time(match.group(0))
            if value is None or value >= max_seen:
                continue
            issues.append(
                _issue(
                    local_key=f"timeline:before_latest:{match.group(0)}",
                    severity="high",
                    dimension="timeline",
                    text_locator=_locator(draft_text, match.start(), match.end()),
                    evidence_refs=evidence,
                    message=(
                        f"草稿时间「{match.group(0)}」早于已发生的最新事件时间"
                    ),
                    suggested_fix="调整时间标记，使草稿不早于已确认的最新事件",
                )
            )
            break
        if issues:
            break
    return issues


def _check_world_rules(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
) -> list[ReviewIssue]:
    """世界硬规则：草稿不得包含规则禁止的模式（critical）。"""
    issues: list[ReviewIssue] = []
    draft_text = snapshot["draft_text"]
    evidence = _manifest_source_ids(manifest, _EVIDENCE_SOURCE_TYPES["rule"])
    for rule in snapshot["world_rules"]:
        for pattern in rule["forbidden_patterns"]:
            if not pattern:
                continue
            pos = draft_text.find(pattern)
            if pos < 0:
                continue
            issues.append(
                _issue(
                    local_key=f"rule:{rule['rule_key']}:{pattern}",
                    severity="critical",
                    dimension="rule",
                    text_locator=_locator(draft_text, pos, pos + len(pattern)),
                    evidence_refs=evidence,
                    message=f"草稿违反世界硬规则「{rule['rule_text']}」",
                    suggested_fix="移除或改写违反硬规则的内容",
                )
            )
            break
    return issues


def _levenshtein(a: str, b: str) -> int:
    """计算两个字符串的编辑距离（用于术语形近变体识别）。"""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (ca != cb),
                )
            )
        previous = current
    return previous[-1]


def _check_terminology(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
) -> list[ReviewIssue]:
    """术语一致性：草稿使用规范术语的形近变体且不在允许变体名单内则标记。"""
    issues: list[ReviewIssue] = []
    draft_text = snapshot["draft_text"]
    evidence = _manifest_source_ids(manifest, _EVIDENCE_SOURCE_TYPES["term"])
    for term in snapshot["terms"]:
        canonical = term["canonical"]
        variants = set(term.get("variants") or [])
        if len(canonical) < 2:
            continue
        # 草稿已使用规范术语或允许变体 -> 视为一致。
        if canonical in draft_text or any(v in draft_text for v in variants if v):
            continue
        # 扫描与规范术语长度相近的汉字子串，寻找形近但未登记的拼写。
        window_sizes = range(max(2, len(canonical) - 1), len(canonical) + 2)
        matched: set[str] = set()
        for size in window_sizes:
            for pos in range(0, len(draft_text) - size + 1):
                window = draft_text[pos : pos + size]
                if not all("\u4e00" <= ch <= "\u9fa5" for ch in window):
                    continue
                if window == canonical or window in variants:
                    continue
                if _levenshtein(window, canonical) <= 1:
                    matched.add(window)
        for window in sorted(matched):
            pos = draft_text.find(window)
            issues.append(
                _issue(
                    local_key=f"term:{canonical}:{window}",
                    severity="low",
                    dimension="term",
                    text_locator=_locator(draft_text, pos, pos + len(window)),
                    evidence_refs=evidence,
                    message=(
                        f"草稿使用术语「{window}」，与规范术语「{canonical}」"
                        "不一致且不在允许变体名单内"
                    ),
                    suggested_fix=f"统一改为规范术语「{canonical}」，或将「{window}」登记为允许变体",
                )
            )
    return issues


def run_deterministic_rules(
    snapshot: ConsistencySnapshot,
    manifest: ContextManifest,
    config: RuleConfig | None = None,
) -> list[ReviewIssue]:
    """在显式版本快照与 ContextManifest 上执行全部确定性一致性规则。

    参数：
        snapshot: 显式版本快照（人物/地点/时间线/硬规则/术语/正文）。
        manifest: 当前运行的来源清单，规则只从其中选取证据引用。
        config: 规则配置；未提供的维度默认开启。

    返回：命中的结构化 ReviewIssue 列表（每条都含定位与证据引用）。

    约束：纯计算，不读取当前最新版本、不写数据库、不修改 Canon。
    """
    config = config or {}
    checks_enabled = {
        "character_checks": config.get("character_checks", True),
        "location_checks": config.get("location_checks", True),
        "timeline_checks": config.get("timeline_checks", True),
        "state_checks": config.get("state_checks", True),
        "world_rule_checks": config.get("world_rule_checks", True),
        "terminology_checks": config.get("terminology_checks", True),
    }
    known_names = list(snapshot.get("known_names") or [])
    known_names.extend(c["name"] for c in snapshot.get("characters") or [])
    issues: list[ReviewIssue] = []
    if checks_enabled["character_checks"]:
        issues.extend(_check_character_existence(snapshot, manifest, known_names))
    if checks_enabled["state_checks"]:
        issues.extend(_check_character_state(snapshot, manifest))
    if checks_enabled["location_checks"]:
        issues.extend(_check_location_reachability(snapshot, manifest))
    if checks_enabled["timeline_checks"]:
        issues.extend(_check_timeline_order(snapshot, manifest))
    if checks_enabled["world_rule_checks"]:
        issues.extend(_check_world_rules(snapshot, manifest))
    if checks_enabled["terminology_checks"]:
        issues.extend(_check_terminology(snapshot, manifest))
    return issues
