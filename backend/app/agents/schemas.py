"""Agent 数据结构模块（Pydantic 模型）。

定义 Agent 图全部共享的输入/输出结构：输入信封 `AgentInputEnvelope`、各类
Agent 输出（`WritingOutput` / `ContinuityOutput` / `ReviewOutput` / `RevisionOutput`）、
列表中的事实、评审问题、文本定位与操作、以及路由结果 `RouterOutcome`。这些
模型是 Agent 间与图节点间交换数据的契约，也是 schema 校验的对象。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TextLocator(BaseModel):
    """文本定位器：用引文与字符区间定位文本中的位置。"""

    quote: str = ""
    char_start: int = 0
    char_end: int = 0


class CandidateFact(BaseModel):
    """候选事实：由 Agent 生成、待确认的持久化事实条目。"""

    candidate_type: Literal["fact"] = "fact"
    local_key: str
    claim: str
    status: Literal["candidate"] = "candidate"
    scope: Literal["scene"] = "scene"
    evidence_refs: list[str] = Field(default_factory=list)


class ContextManifestEntry(BaseModel):
    """上下文清单条目：描述一次引用来源的元数据。"""

    source_id: str
    kind: str
    ref_id: str | None = None
    revision_id: str | None = None
    anchor_id: str | None = None
    excerpt_hash: str | None = None


class RuntimeContext(BaseModel):
    """运行上下文：一次运行的身份与范围信息。

    包含 `generation_run_id`、`agent_run_id`、`thread_id` 等身份标识，以及
    `run_scope` / `decision_target` 等运行范围信息，用于断点续跑与 fencing。
    """

    generation_run_id: str
    agent_run_id: str
    agent_attempt_key: str
    thread_id: str
    volume_id: str | None = None
    chapter_id: str | None = None
    scene_id: str | None = None
    scene_ids: list[str] = Field(default_factory=list)
    parent_generation_run_id: str | None = None
    supersedes_run_id: str | None = None
    parent_plan_revision_id: str | None = None
    run_scope: Literal["chapter", "scene"] = "scene"
    decision_target: Literal["plan", "scene", "chapter", "canon", None] = None


class AuthorFeedback(BaseModel):
    """作者反馈：文本、目标与可选操作列表。"""

    text: str = ""
    target: Literal["plan", "scene", "chapter"] | None = None
    selection: dict | None = None
    operations: list[dict] = Field(default_factory=list)


class AgentInputEnvelope(BaseModel):
    """共享输入信封，镜像 Prompt v1 契约。"""

    project: dict = Field(default_factory=dict)
    runtime_context: RuntimeContext
    volume: dict = Field(default_factory=dict)
    chapter_contract: dict = Field(default_factory=dict)
    # 章节规划运行的最小输入。首次规划允许 chapter_contract 为空，Planner
    # 应从自然语言意图与讨论上下文生成候选，而不是把空契约当作已确认计划。
    chapter_intent: dict = Field(default_factory=dict)
    plan_discussion: list[dict] = Field(default_factory=list)
    pending_plan_questions: list[dict] = Field(default_factory=list)
    pending_plan_proposals: list[dict] = Field(default_factory=list)
    plan_decisions: list[dict] = Field(default_factory=list)
    scene_brief: dict = Field(default_factory=dict)
    request_type: Literal["new_chapter", "continue", "rewrite", "review"] = "continue"
    base_scene_revision_id: str | None = None
    base_chapter_revision_id: str | None = None
    plan_revision_id: str | None = None
    accepted_scene_revision_id: str | None = None
    accepted_chapter_revision_id: str | None = None
    chapter_sync_status: str | None = None
    entry_handoff_status: str | None = None
    predecessor_accepted_chapter_revision_id: str | None = None
    entry_handoff_id: str | None = None
    entry_source_chapter_revision_id: str | None = None
    entry_handoff_chain_hash: str | None = None
    lease_worker_id: str | None = None
    lease_fencing_token: int | None = None
    write_fence_owner_kind: Literal["worker", "api_command"] | None = None
    write_fence_owner_id: str | None = None
    write_fence_fencing_token: int | None = None
    canon_scope: Literal["chapter", "scene", None] = None
    snapshot_before: dict = Field(default_factory=dict)
    context_manifest: list[ContextManifestEntry] = Field(default_factory=list)
    context_pack: list[dict] = Field(default_factory=list)
    accepted_text: str = ""
    draft_artifact_id: str | None = None
    draft_text: str | None = None
    author_feedback: AuthorFeedback = Field(default_factory=AuthorFeedback)
    canon_feedback: dict | None = None
    rule_report: dict = Field(default_factory=dict)
    previous_reports: list[dict] = Field(default_factory=list)
    output_constraints: dict = Field(default_factory=dict)


class ReviewIssue(BaseModel):
    """评审问题：包含类型、严重级别、文本定位、证据与建议动作。"""

    local_key: str
    issue_type: Literal[
        "character", "location", "timeline", "rule", "state", "unknown", "conflict", "pacing", "prose"
    ] = "unknown"
    severity: Literal["low", "medium", "high", "blocking"] = "medium"
    text_locator: TextLocator = Field(default_factory=TextLocator)
    problem: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    affected_scene_keys: list[str] = Field(default_factory=list)
    suggested_action: str = ""
    continuity_impact: str | None = None


class WritingOutput(BaseModel):
    """写作 Agent 输出：草稿内容、候选事实与来源引用。"""

    status: Literal["ready", "needs_clarification"]
    mode: Literal["draft", "continue", "rewrite"]
    content: str = ""
    candidate_facts: list[CandidateFact] = Field(default_factory=list)
    unresolved_assumptions: list[str] = Field(default_factory=list)
    context_source_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


class ContinuityOutput(BaseModel):
    """连续性检查 Agent 输出：状态、场景快照增量与问题列表。"""

    status: Literal["pass", "issues", "needs_author_confirmation", "needs_clarification"]
    scene_snapshot_delta: dict = Field(default_factory=dict)
    issues: list[ReviewIssue] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


class ReviewOutput(BaseModel):
    """评审 Agent 输出：评审问题列表、总体评级与提交标志。"""

    status: Literal["ready", "needs_clarification"]
    review_issues: list[ReviewIssue] = Field(default_factory=list)
    overall_rating: str = ""
    submitted: bool = False
    clarification_questions: list[str] = Field(default_factory=list)


class ChapterPlanOutput(BaseModel):
    """章节规划 Agent 输出：章节契约、场景合同列表与计划理由。"""

    status: Literal["ready", "needs_clarification"]
    chapter_contract: dict = Field(default_factory=dict)
    scene_contracts: list[dict] = Field(default_factory=list)
    reason: str = ""
    clarification_questions: list[str] = Field(default_factory=list)
    # 规划候选的可追溯元数据；旧 Provider 响应不提供这些字段时保持兼容。
    proposals: list[dict] = Field(default_factory=list)
    unresolved_assumptions: list[str] = Field(default_factory=list)
    contract_field_provenance: dict = Field(default_factory=dict)
    scene_field_provenance: dict = Field(default_factory=dict)


class ChapterReviewOutput(BaseModel):
    """章节审校 Agent 输出：章节级评审问题与总体评级。"""

    status: Literal["ready", "needs_clarification"]
    review_issues: list[ReviewIssue] = Field(default_factory=list)
    overall_rating: str = ""
    submitted: bool = False
    clarification_questions: list[str] = Field(default_factory=list)


class TextOperation(BaseModel):
    """文本操作：表示对场景文本的一次替换、插入或删除。"""

    op: Literal["replace", "insert", "delete"]
    anchor_id: str | None = None
    text_locator: TextLocator = Field(default_factory=TextLocator)
    expected_text_hash: str | None = None
    old_text: str = ""
    new_text: str = ""
    reason: str = ""
    source: Literal["author_feedback", "review_issue", "continuity_issue"] = "author_feedback"


class RevisionOutput(BaseModel):
    """修订 Agent 输出：ChangeSet（文本操作列表）与候选事实。"""

    status: Literal["ready", "needs_clarification"]
    base_scene_revision_id: str | None = None
    operation_format: Literal["semantic_text"] = "semantic_text"
    operations: list[TextOperation] = Field(default_factory=list)
    candidate_facts: list[CandidateFact] = Field(default_factory=list)
    remaining_risks: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class RouterOutcome(BaseModel):
    """Agent 输出的归一化路由决策。"""

    status: Literal[
        "continue", "needs_clarification", "feedback", "cancel", "failed", "accept"
    ]
    next_node: str | None = None
    pending_node: str | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class CanonSource(BaseModel):
    """Canon 候选的来源对象（Prompt v1 契约 `source` 字段）。

    `source_id` 必须引用当前 `context_manifest` 中的来源；`paragraph_ref` 与
    `text_locator` 定位到段落与文本区间。CanonAgent 只从已接受版本读取来源，
    不填写草稿/补丁来源。
    """

    chapter_id: str | None = None
    scene_id: str | None = None
    source_id: str = ""
    paragraph_ref: str | None = None
    text_locator: dict = Field(default_factory=dict)


class EffectiveStoryTime(BaseModel):
    """故事内有效时间：值 + 精度。

    `precision` 取 exact|range|relative|unknown；无法判断时填 unknown，
    不得把现实生成时间当作故事时间。
    """

    value: str = ""
    precision: Literal["exact", "range", "relative", "unknown"] = "unknown"


class CanonCandidate(BaseModel):
    """Canon 候选：CanonAgent 提取的待确认设定条目。

    三类候选（fact / timeline_event / plot_thread）共用同一结构，仅
    `candidate_type` 不同。`status` 固定为 `pending_author_confirmation`
    （持久化时归一化为 `pending`）；`candidate_id` 由运行时后置分配，
    模型不得填写正式值。`scope` 只能是 `chapter` 或 `scene`，局部候选不得
    声明为全局已确认。
    """

    candidate_id: str | None = None
    candidate_type: Literal["fact", "timeline_event", "plot_thread"]
    local_key: str
    claim: str
    status: Literal["pending_author_confirmation"] = "pending_author_confirmation"
    scope: Literal["chapter", "scene"]
    source: CanonSource = Field(default_factory=CanonSource)
    effective_story_time: EffectiveStoryTime = Field(default_factory=EffectiveStoryTime)
    narrative_knowledge: Literal[
        "objective", "character_belief", "rumor", "lie", "dream", "metaphor", "unknown"
    ] = "unknown"
    resolution_action: Literal[
        "confirm_existing", "propose_update", "ignore_duplicate"
    ] = "confirm_existing"
    evidence_refs: list[str] = Field(default_factory=list)
    # 结构化字段：仅 timeline_event 使用 entities；仅 plot_thread 使用
    # thread_state/planned_resolution；物化为正式结构时按 candidate_type 读取。
    entities: list[str] = Field(default_factory=list)
    thread_state: Literal["open", "advanced", "resolved", "abandoned"] | None = None
    planned_resolution: str | None = None


class CanonOutput(BaseModel):
    """CanonAgent 输出：三类待确认候选与来源说明。

    与 Prompt v1 契约第 10 节一致：`ready` 表示候选提取完成；
    `needs_clarification` 表示已接受章节版本或来源不足。候选项的
    `pending_author_confirmation` 只表示 Agent 原始输出，持久化状态由运行时
    归一化为 `pending`；`candidate_id` 是运行时后置字段。
    """

    status: Literal["ready", "needs_clarification"]
    fact_candidates: list[CanonCandidate] = Field(default_factory=list)
    timeline_event_candidates: list[CanonCandidate] = Field(default_factory=list)
    plot_thread_updates: list[CanonCandidate] = Field(default_factory=list)
    ambiguous_claims: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class CanonDecision(BaseModel):
    """Canon 逐条决策：作者针对持久候选的 confirm|reject|defer。

    `candidate_id` 是持久候选 ID；`candidate_type` 为 fact|timeline_event|
    plot_thread；`local_key` 仅作为当前 Canon 运行内的兼容别名。决策映射到
    候选持久状态：confirm->accepted、reject->rejected、defer->deferred。
    """

    candidate_id: str
    candidate_type: Literal["fact", "timeline_event", "plot_thread"] = "fact"
    decision: Literal["accepted", "rejected", "deferred"]
    local_key: str | None = None
