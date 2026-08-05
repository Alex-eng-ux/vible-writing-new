"""Task 5A 资源与作者 ChangeSet 的冻结 API schema。

本模块是 Task 5A 的权威契约：Task 5B 只能追加运行/决策/SSE schema，
Task 5C 只能追加 Canon schema，不得修改已冻结字段的含义或删除字段。
所有字段名、类型和默认值改动都视为破坏性变更。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """创建项目请求体。字段均为必填，长度由 Field 约束。"""

    name: str = Field(min_length=1, max_length=255)
    genre: str = Field(min_length=1, max_length=120)
    target_reader: str = Field(min_length=1, max_length=255)
    default_style: str = Field(min_length=1, max_length=255)


class VolumeCreate(BaseModel):
    """创建卷请求体。"""

    name: str = Field(min_length=1, max_length=255)
    goal: str = Field(min_length=1)
    mainline: str = Field(min_length=1)
    time_range: str = Field(min_length=1, max_length=255)


class ChapterIntent(BaseModel):
    """章节意图，首次 new_chapter 规划时由服务端规范化为共享输入。"""

    text: str = ""
    pov: str = ""
    entry_state: list[Any] = Field(default_factory=list)
    required_beats: list[Any] = Field(default_factory=list)
    forbidden_beats: list[Any] = Field(default_factory=list)
    expected_exit_state: list[Any] = Field(default_factory=list)


class ChapterCreate(BaseModel):
    """创建章请求体。"""

    title: str = Field(min_length=1, max_length=255)
    pov: str = Field(min_length=1, max_length=120)
    chapter_intent: ChapterIntent = Field(default_factory=ChapterIntent)


class SceneCreate(BaseModel):
    """创建场景请求体；scene_brief 由服务端从这些字段组装。"""

    title: str = Field(min_length=1, max_length=255)
    pov: str = Field(min_length=1, max_length=120)
    location: str = ""
    story_time: str = ""
    goal: str = ""
    entry_state: list[Any] = Field(default_factory=list)
    required_beats: list[Any] = Field(default_factory=list)
    forbidden_beats: list[Any] = Field(default_factory=list)
    expected_exit_state: list[Any] = Field(default_factory=list)


class ResourceCreated(BaseModel):
    """资源创建成功响应。资源命令的 run_id 必须为 null。"""

    id: str
    type: str
    parent_id: str | None
    version: int = 1
    created_at: str


class ProjectRead(BaseModel):
    """项目读取响应。"""

    id: str
    name: str
    genre: str
    target_reader: str
    default_style: str
    created_at: str


class VolumeRead(BaseModel):
    """卷读取响应。"""

    id: str
    project_id: str
    name: str
    goal: str
    mainline: str
    time_range: str
    created_at: str


class ChapterRead(BaseModel):
    """章读取响应；只返回明确 accepted 指针，不用数据库最新行代替。"""

    id: str
    volume_id: str
    title: str
    pov: str
    accepted_chapter_revision_id: str | None
    entry_handoff_id: str | None
    created_at: str


class SceneRead(BaseModel):
    """场景读取响应；只返回 accepted 场景版本指针。"""

    id: str
    chapter_id: str
    title: str
    scene_brief: dict
    accepted_scene_revision_id: str | None
    created_at: str


class ChangeSetRequest(BaseModel):
    """作者 ChangeSet 请求。source=author 强制 prosemirror_step 格式。"""

    base_scene_revision_id: str | None = None
    operation_format: Literal["prosemirror_step", "semantic_text"]
    operations: list[Any] = Field(default_factory=list)
    source: Literal["author", "agent", "review"]
    base_content_hash: str | None = None
    location: dict[str, Any] | None = None


class ChangeSetCreated(BaseModel):
    """ChangeSet 创建响应；空场景首稿时携带 draft_artifact_id。"""

    change_set_id: str
    scene_id: str
    base_scene_revision_id: str | None
    operation_format: str
    source: str
    base_content_hash: str
    draft_artifact_id: str | None
    manual_command_id: str


class CommitRequest(BaseModel):
    """提交 ChangeSet 请求；根草稿仅允许 accept 物化。"""

    author_decision: Literal["accept", "feedback", "cancel"] = "accept"


class RollbackRequest(BaseModel):
    """回滚请求；目标父版本必须由作者显式指定。"""

    target_revision_id: str
    author_decision: Literal["accept", "feedback", "cancel", "author"] = "author"


class RevisionRead(BaseModel):
    """场景版本读取响应。"""

    id: str
    parent_revision_id: str | None
    scene_id: str
    content_hash: str
    status: str
    reason: str
    created_at: str


class RevisionDetail(RevisionRead):
    """场景版本详情读取响应（Task 7A 追加，只读）。

    在 RevisionRead 基础上追加正文字段：content 为规范化 ProseMirror JSON
    字符串（与 ChangeSet 基线一致），source_ref 为来源引用。只用于前端
    展示/编辑基线/版本比较，不修改任何领域契约。
    """

    content: str
    source_ref: str


class ChapterRevisionRead(BaseModel):
    """章节版本读取响应。"""

    id: str
    parent_revision_id: str | None
    chapter_id: str
    status: str
    reason: str
    created_at: str


class ChapterHandoffRead(BaseModel):
    """章节 handoff 读取响应；只返回 active 且 in_sync 的承接入口。"""

    id: str
    chapter_id: str
    source_chapter_revision_id: str
    entry_handoff_status: str
    chain_hash: str
    status: str


# ---------------------------------------------------------------------------
# Task 5B：运行 / 决策 / 恢复 / SSE schema（只追加，不修改 5A 已冻结字段）。
# ---------------------------------------------------------------------------


class RunCreateRequest(BaseModel):
    """通用章节/场景运行入口请求。

    `decision_target` 只接受 plan|scene|chapter；`target=canon` 由通用入口
    拒绝（CANON_NOT_ENABLED），Canon 专用入口留给 Task 5C。
    """

    run_scope: Literal["chapter", "scene"]
    request_type: Literal["new_chapter", "continue", "rewrite", "review"] = "continue"
    decision_target: Literal["plan", "scene", "chapter", "canon", None] = None
    plan_revision_id: str | None = None
    scene_id: str | None = None
    preceding_chapter_id: str | None = None
    preceding_accepted_chapter_revision_id: str | None = None
    entry_handoff_id: str | None = None
    entry_source_chapter_revision_id: str | None = None
    entry_handoff_chain_hash: str | None = None
    base_scene_revision_id: str | None = None
    base_chapter_revision_id: str | None = None
    scene_base_revision_ids: dict[str, str | None] = Field(default_factory=dict)
    chapter_intent: dict | None = None
    author_feedback: dict | None = None
    canon_scope: Literal["chapter", "scene", None] = None
    accepted_scene_revision_id: str | None = None


class RunSnapshot(BaseModel):
    """运行快照：HTTP 对外统一返回的运行状态视图。

    `status` 为单个枚举值：queued|running|waiting_feedback|pending_clarification|
    paused|accepted|cancelled|failed|superseded；`completed` 只属于幂等记录。
    `thread_id` 是 `generation_run_id` 的对外别名，不单独持久化。
    """

    run_id: str
    thread_id: str
    project_id: str
    target_id: str
    run_scope: Literal["chapter", "scene"]
    request_type: str
    status: Literal[
        "queued", "running", "waiting_feedback", "pending_clarification",
        "paused", "accepted", "cancelled", "failed", "superseded",
    ]
    run_version: int
    current_scene_id: str | None = None
    current_node: str | None = None
    pending_node: str | None = None
    pause_reason: str | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    last_error_code: str | None = None
    last_event_sequence: int = 0
    created_at: str
    updated_at: str


class CandidateDecisionItem(BaseModel):
    """Canon 候选决策条目（Task 5C 使用；5B 保留字段以保持版本兼容）。"""

    candidate_id: str
    candidate_type: Literal["fact", "timeline_event", "plot_thread"] = "fact"
    local_key: str | None = None
    decision: Literal["confirm", "reject", "defer"]
    scope: Literal["chapter", "scene"] | None = None


class DecisionRequest(BaseModel):
    """作者决策请求（target=canon 在 5B 阶段返回 CANON_NOT_ENABLED）。"""

    idempotency_key: str
    expected_run_version: int = 0
    target: Literal["plan", "scene", "chapter", "canon"]
    decision: Literal["accept", "feedback", "cancel"]
    plan_revision_id: str | None = None
    expected_current_plan_revision_id: str | None = None
    expected_plan_version: int | None = None
    text: str | None = None
    selection: dict | None = None
    operations: list[dict] = Field(default_factory=list)
    canon_scope: Literal["chapter", "scene", None] = None
    accepted_scene_revision_id: str | None = None
    base_scene_revision_id: str | None = None
    base_chapter_revision_id: str | None = None
    # 领域物化定位（Task 5B 追加，向后兼容）：target=scene 的 accept 物化草稿
    # 或 ChangeSet，target=chapter 的 accept 物化 staged 章节版本。
    draft_artifact_id: str | None = None
    change_set_id: str | None = None
    chapter_revision_id: str | None = None
    candidate_decisions: list[CandidateDecisionItem] = Field(default_factory=list)
    canon_feedback: dict | None = None


class ResumeRequest(BaseModel):
    """暂停运行恢复请求：只接受 paused 且必须带 expected_pause_reason。"""

    idempotency_key: str
    expected_run_version: int = 0
    expected_pause_reason: str


class DecisionResponse(BaseModel):
    """决策/恢复成功响应。"""

    run: RunSnapshot
    decision_id: str
    command_id: str


class RunEventEnvelope(BaseModel):
    """SSE 可重放事件信封：id 是稳定唯一身份，sequence 单调递增。"""

    id: str
    sequence: int
    type: str
    run_id: str
    payload_schema: str = "run-event.v1"
    redaction_version: str = "redaction.v1"
    created_at: str
    payload: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Task 5C：Canon 专用运行 schema（只追加，不修改 5A/5B 已冻结字段）。
# ---------------------------------------------------------------------------


class CanonRunCreateRequest(BaseModel):
    """Canon 专用运行创建请求（章节/场景专用入口）。

    章节 Canon 只能使用当前 accepted 且同步的章节版本；场景 Canon 只能使用
    当前 accepted 场景版本。`canon_scope` 唯一决定目标作用域，其余字段由
    服务端按 run_scope 校验对应 accepted 来源。
    """

    canon_scope: Literal["chapter", "scene"]
    accepted_chapter_revision_id: str | None = None
    accepted_scene_revision_id: str | None = None
    chapter_intent: dict | None = None
    author_feedback: dict | None = None
    scene_base_revision_ids: dict[str, str | None] = Field(default_factory=dict)


class CanonDecisionRequest(BaseModel):
    """Canon 决策提交请求：按持久 `candidate_id` 逐条 confirm|reject|defer。

    幂等、CAS 与 API command fence 与通用决策一致；`target=canon` 只允许
    Canon 专用入口，普通入口仍拒绝。

    `cancel` 模式（Task 5C 补完）：
    - `cancel_scope="confirm"`：取消本次确认，`candidate_decisions` 必须为空，
      未决候选保留 pending/deferred，绝不生成正式 Canon。
    - `cancel_scope="run"`：取消整个运行，`candidate_decisions` 必须为空，
      运行转 `cancelled`，未决候选原子转 `discarded`。
    """

    idempotency_key: str
    expected_run_version: int = 0
    canon_scope: Literal["chapter", "scene"]
    decision: Literal["confirm", "reject", "defer", "cancel"] = "confirm"
    cancel_scope: Literal["confirm", "run"] | None = None
    candidate_decisions: list[CandidateDecisionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Task 7B：只读运行上下文 schema（只追加，不修改 5A/5B/5C 已冻结字段）。
# ---------------------------------------------------------------------------


class ChapterPlanRead(BaseModel):
    """章节当前 accepted plan 的只读视图（Task 7B 追加）。

    前端创建场景运行（continue/rewrite/review）需要携带当前 accepted
    plan_revision_id；本端点只读查询 ChapterPlanRevisionLink 指针，不修改任何
    领域契约。无 accepted plan 时 `plan_revision_id` 为 None。
    """

    chapter_id: str
    plan_revision_id: str | None
    plan_status: str | None
    plan_version: int | None
    chapter_contract: dict | None = None
    plan_reason: str | None = None


# ---------------------------------------------------------------------------
# Task 7C：只读 Story Bible / Canon 候选 schema（只追加，不修改冻结字段）。
# ---------------------------------------------------------------------------


class CanonEntryRead(BaseModel):
    """正式 Canon 条目的只读视图（fact / timeline_event / plot_thread）。

    只读查询 status="active" 的正式结构；字段按类型投影：fact 取 fact_text，
    timeline_event 取 event_text + story_time + entities，plot_thread 取
    thread_text + state + planned_resolution。不修改任何领域契约。
    """

    id: str
    type: Literal["fact", "timeline_event", "plot_thread"]
    text: str
    status: str
    created_at: str
    chapter_id: str | None = None
    story_time: dict | None = None
    entities: list | None = None
    state: str | None = None
    planned_resolution: str | None = None


class CanonSnapshotRead(BaseModel):
    """项目正式 Story Bible 的只读快照（三类正式条目分组）。"""

    project_id: str
    facts: list[CanonEntryRead] = Field(default_factory=list)
    timeline_events: list[CanonEntryRead] = Field(default_factory=list)
    plot_threads: list[CanonEntryRead] = Field(default_factory=list)


class CanonCandidateRead(BaseModel):
    """Canon 候选的只读投影（决策 UI 展示来源/作用域/状态/内容）。

    与领域层 `_to_dict` 投影一致；content 保留完整候选载荷（claim /
    paragraph_ref / effective_story_time / narrative_knowledge 等），供前端
    展示三类候选的正文与来源定位。
    """

    id: str
    project_id: str
    chapter_id: str | None = None
    scene_id: str | None = None
    scope: str
    scope_identity: str
    candidate_type: str
    status: str
    source_identity: str
    content: dict = Field(default_factory=dict)
    local_key: str | None = None
    generation_run_id: str | None = None


class CanonCandidateListRead(BaseModel):
    """Canon 候选列表的只读视图（按目标场景/章节查询，含已决策状态）。"""

    target_type: Literal["scene", "chapter"]
    target_id: str
    items: list[CanonCandidateRead] = Field(default_factory=list)
