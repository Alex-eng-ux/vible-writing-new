"""一致性规则层的数据契约与端口定义。

本模块集中定义规则引擎的输入/输出数据契约（TypedDict）与端口
（Protocol），不含任何业务逻辑或数据库访问。这些类型是规则引擎与
上层（生成运行、候选写回）之间的稳定接口。

核心约束：
- 所有类型均为只读数据形状，不得私自扩展字段；
- RuleEngine 端口只做纯计算评估，不写库；
- RuleIssue 的 severity 限定为 low/medium/high/blocking 四级（Task 4A）；
- Task 6 只能向后兼容追加字段/类型，不得改名或删除 Task 4A 已消费字段；
- ReviewIssue 的 severity 限定为 low|medium|high|critical，status 限定为
  pending|accepted|rejected|deferred（与 RuleIssue 的 blocking 不同）。
"""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict


class RuleEngineInput(TypedDict):
    """规则引擎的稳定输入契约（Task 4A）。

    Task 6 的扩展应向后兼容，不破坏既有字段。

    参数：
        scene_id: 目标场景 ID。
        project_id: 项目 ID。
        draft_text: 待评估的草稿文本，可为空。
        accepted_scene_revision_id: 已接受的场景版本 ID，可为 None。
        rule_report: 规则相关报告数据。
    """

    scene_id: str
    project_id: str
    draft_text: str
    accepted_scene_revision_id: str | None
    rule_report: dict


class RuleIssue(TypedDict):
    """规则引擎输出的单条问题。

    参数：
        rule_id: 命中的规则标识。
        severity: 严重级别，限定为 low/medium/high/blocking。
        message: 问题描述。
        text_locator: 定位问题所在文本的位置信息。
    """

    rule_id: str
    severity: Literal["low", "medium", "high", "blocking"]
    message: str
    text_locator: dict


class RuleEngineOutput(TypedDict):
    """规则引擎的评估输出。

    参数：
        issues: 命中的问题列表；为空即通过。
        passed: 是否通过，等价于 issues 为空。
    """

    issues: list[RuleIssue]
    passed: bool


class RuleEngine(Protocol):
    """规则引擎端口，定义评估草稿的依赖边界。

    由规则引擎实现（如 MinimalRuleEngine）注入；实现必须无副作用、
    不写库。
    """

    def evaluate(self, input: RuleEngineInput) -> RuleEngineOutput: ...


# ---------------------------------------------------------------------------
# Task 6：一致性规则扩展与建议审阅契约（只追加，不改 Task 4A 已消费字段）。
# ---------------------------------------------------------------------------


ReviewSeverity = Literal["low", "medium", "high", "critical"]
ReviewStatus = Literal["pending", "accepted", "rejected", "deferred"]
ReviewDimension = Literal[
    "character", "location", "timeline", "state", "rule", "term"
]


class ReviewIssue(TypedDict):
    """结构化审阅问题（Task 6 新增）。

    确定性规则与 ReviewAgent 的 LLM 输出都必须归一为结构化 `ReviewIssue`；
    正式 `issue_id`/`anchor_id` 由运行时 `IdentityResolutionStep` 生成，本
    契约只承载当前输出内稳定的定位键与证据引用。

    参数：
        local_key: 当前输出内稳定定位键，供合并去重使用。
        severity: 严重级别，low|medium|high|critical。
        dimension: 问题维度（人物/地点/时间线/状态/硬规则/术语）。
        text_locator: 文本位置或结构化定位；不能只用自由文本描述代替。
        evidence_refs: 证明问题的来源引用，必须来自当前 ContextManifest。
        message: 问题描述。
        suggested_fix: 修复建议；缺少证据或定位的问题不得进入自动修订。
        status: 作者或流程对问题的处理状态（默认 pending）。
    """

    local_key: str
    severity: ReviewSeverity
    dimension: ReviewDimension
    text_locator: dict
    evidence_refs: list[str]
    message: str
    suggested_fix: str
    status: ReviewStatus


class CharacterEntry(TypedDict):
    """人物条目：名称、生命周期/在场状态与最近出现地点（规则输入快照）。"""

    name: str
    state: Literal["alive", "dead", "absent", "departed", "unknown"]
    last_seen_location: str | None


class LocationEntry(TypedDict):
    """地点条目：名称与可直接到达的来源地点列表（规则输入快照）。"""

    name: str
    reachable_from: list[str]


class TimelineEntry(TypedDict):
    """时间线条目：事件键与故事内时间（规则输入快照）。"""

    event_key: str
    story_time: str
    subject: str
    detail: str


class WorldRuleEntry(TypedDict):
    """世界硬规则条目：规则文本与禁止出现的模式（规则输入快照）。"""

    rule_key: str
    rule_text: str
    forbidden_patterns: list[str]


class TermEntry(TypedDict):
    """术语条目：规范术语与允许的变体（规则输入快照）。"""

    canonical: str
    variants: list[str]


class ConsistencySnapshot(TypedDict):
    """一致性规则的显式版本快照输入（Task 6 新增）。

    规则只读取该快照与 ContextManifest，绝不读取当前最新版本、不写数据库、
    不修改 Canon。快照内容必须来自已冻结/已接受的显式版本。

    参数：
        scene_id: 目标场景 ID。
        project_id: 项目 ID。
        draft_text: 待评估的正文文本。
        snapshot_revision_ids: 显式版本快照来源 -> 版本 ID 映射。
        characters: 人物条目列表。
        locations: 地点条目列表。
        timeline: 时间线条目列表。
        world_rules: 世界硬规则条目列表。
        terms: 术语条目列表。
        known_names: 已知实体名集合（来自 manifest 登记的 entity/canon 来源）。
    """

    scene_id: str
    project_id: str
    draft_text: str
    snapshot_revision_ids: dict[str, str]
    characters: list[CharacterEntry]
    locations: list[LocationEntry]
    timeline: list[TimelineEntry]
    world_rules: list[WorldRuleEntry]
    terms: list[TermEntry]
    known_names: list[str]


class RuleConfig(TypedDict, total=False):
    """规则配置：各维度检查开关。

    未提供的键默认开启对应检查。路由规则固定为：low|medium 问题在当前
    运行最多触发一次自动修订；high|critical 必须转作者反馈。
    """

    character_checks: bool
    location_checks: bool
    timeline_checks: bool
    state_checks: bool
    world_rule_checks: bool
    terminology_checks: bool


RouteOutcome = Literal["auto_revision", "waiting_feedback", "accepted"]


class RouteResult(TypedDict):
    """问题路由结果（Task 6 新增）。

    参数：
        outcome: auto_revision|waiting_feedback|accepted。
        issues: 路由后的问题列表。
        scene_auto_revision_count: 路由后的自动修订计数。
    """

    outcome: RouteOutcome
    issues: list[ReviewIssue]
    scene_auto_revision_count: int
