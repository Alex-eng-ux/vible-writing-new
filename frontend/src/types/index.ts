/**
 * Task 7A 前端类型定义：与后端 Task 5A/7A schema 一一对应。
 *
 * 约束：不得创建与后端 schema 不兼容的替代类型；字段名/类型/可选性必须
 * 与 `backend/app/api/schemas.py` 保持同步。
 */

/** 后端统一错误信封（main.py build_envelope）。 */
export type ErrorEnvelope = {
  code: string;
  message: string;
  retryable: boolean;
  run_id: string | null;
  request_id: string;
  details: Record<string, unknown> | null;
};

/** 资源创建响应（project/volume/chapter/scene 通用）。 */
export type ResourceCreated = {
  id: string;
  type: string;
  parent_id: string | null;
  version: number;
  created_at: string;
};

export type Project = {
  id: string;
  name: string;
  genre: string;
  target_reader: string;
  default_style: string;
  created_at: string;
};

export type Volume = {
  id: string;
  project_id: string;
  name: string;
  goal: string;
  mainline: string;
  time_range: string;
  created_at: string;
};

export type Chapter = {
  id: string;
  volume_id: string;
  title: string;
  pov: string;
  accepted_chapter_revision_id: string | null;
  entry_handoff_id: string | null;
  created_at: string;
};

export type Scene = {
  id: string;
  chapter_id: string;
  title: string;
  scene_brief: Record<string, unknown>;
  accepted_scene_revision_id: string | null;
  created_at: string;
};

/** 场景版本列表项（RevisionRead）。 */
export type SceneRevision = {
  id: string;
  parent_revision_id: string | null;
  scene_id: string;
  content_hash: string;
  status: string;
  reason: string;
  created_at: string;
};

/** 场景版本详情（RevisionDetail，含正文 content）。 */
export type SceneRevisionDetail = SceneRevision & {
  content: string;
  source_ref: string;
};

/** ChangeSet 创建响应（ChangeSetCreated）。 */
export type ChangeSetCreated = {
  change_set_id: string;
  scene_id: string;
  base_scene_revision_id: string | null;
  operation_format: string;
  source: string;
  base_content_hash: string;
  draft_artifact_id: string | null;
  manual_command_id: string;
};

/** 章节版本列表项（ChapterRevisionRead）。 */
export type ChapterRevision = {
  id: string;
  parent_revision_id: string | null;
  chapter_id: string;
  status: string;
  reason: string;
  created_at: string;
};

/** 后端最小 prosemirror_step 文档级操作（与 domain/prosemirror.py 一致）。 */
export type ProseMirrorOp =
  | { op: "insert"; value: string }
  | { op: "replace"; value: string }
  | { op: "delete" };

/** ProseMirror 文档节点形状（仅用于解析正文提取纯文本）。 */
export type ProseMirrorNode = {
  type: string;
  text?: string;
  content?: ProseMirrorNode[];
  [key: string]: unknown;
};

// ---------------------------------------------------------------------------
// Task 7B：运行 / 决策 / 恢复 / SSE 类型（与 backend/app/api/schemas.py 同步）
// ---------------------------------------------------------------------------

/** 运行状态枚举（RunSnapshot.status）。 */
export type RunStatus =
  | "queued"
  | "running"
  | "waiting_feedback"
  | "pending_clarification"
  | "paused"
  | "accepted"
  | "cancelled"
  | "failed"
  | "superseded";

/** 运行快照（RunSnapshot）。 */
export type RunSnapshot = {
  run_id: string;
  thread_id: string;
  project_id: string;
  target_id: string;
  run_scope: "chapter" | "scene";
  request_type: string;
  status: RunStatus;
  run_version: number;
  current_scene_id: string | null;
  current_node: string | null;
  pending_node: string | null;
  pause_reason: string | null;
  clarification_questions: string[];
  last_error_code: string | null;
  last_event_sequence: number;
  created_at: string;
  updated_at: string;
};

/** 章节工作台唯一权威读取快照；阶段和待决动作均由服务端返回。 */
export type ChapterWorkflowRead = {
  chapter_id: string;
  phase: "intent_required" | "planning" | "plan_feedback" | "scene_generation" | "scene_feedback" | "chapter_review" | "chapter_feedback" | "canon_feedback" | "completed" | "blocked";
  chapter_status: string;
  pending_decision: {
    target: "plan" | "scene" | "chapter" | "canon" | null;
    kind: string | null;
    run_id: string | null;
    expected_run_version: number | null;
  };
  intent: {
    text: string;
    optional_fields: Record<string, unknown>;
    unresolved_questions: string[];
  };
  plan_discussion: {
    messages: Array<Record<string, unknown>>;
    pending_questions: Array<{ question_id: string; text: string; impact?: string | null }>;
    pending_proposals: Array<{ proposal_id: string; field_path: string; value: unknown; source: string; status: string; rationale?: string | null }>;
  };
  plan: {
    candidate_revision_id: string | null;
    accepted_revision_id: string | null;
    candidate_version: number | null;
    accepted_version: number | null;
    status: "none" | "candidate" | "accepted" | string;
    contract: Record<string, unknown> | null;
    contract_field_provenance: Record<string, unknown>;
    scene_briefs: Array<{ client_key: string; order: number; title: string; brief: Record<string, unknown>; field_provenance: Record<string, unknown>; status: string }>;
  };
  scenes: Array<{ scene_id: string; order: number; title: string; status: string; accepted_revision_id: string | null; current_run_id: string | null; blocking_reasons: string[] }>;
  chapter_revision: {
    staged_revision_id: string | null;
    accepted_revision_id: string | null;
    review_run_id: string | null;
    review_issues: ReviewIssueItem[];
    review_summary: Record<string, unknown>;
    history: Array<{
      id: string;
      parent_revision_id: string | null;
      status: string;
      reason: string | null;
      created_at: string;
      scene_versions: Array<{ scene_id: string; scene_revision_id: string; sort_order: number }>;
      review_issues: ReviewIssueItem[];
      review_summary: Record<string, unknown>;
      is_current_accepted: boolean;
    }>;
  };
  active_run: (RunSnapshot & { decision_target?: string | null }) | null;
  affected_scene_ids: string[];
  stale_scene_ids: string[];
  blocking_reasons: string[];
  canon_run_id: string | null;
  canon: {
    run_id: string | null;
    status: string | null;
    source_revision_id: string | null;
    pending_candidate_count: number;
  };
};

/** SSE 运行事件信封（RunEventEnvelope + 帧 id）。 */
export type RunEvent = {
  /** 稳定唯一事件 id（`run_id:sequence`），用于 Last-Event-ID 与去重。 */
  id: string;
  sequence: number;
  type: string;
  run_id: string;
  payload_schema: string;
  redaction_version: string;
  created_at: string;
  payload: Record<string, unknown>;
};

/** 审校问题展示项（后端固定 payload，来自 RunEvent payload.issues）。 */
export type ReviewIssueItem = {
  local_key: string;
  severity: "low" | "medium" | "high" | "critical";
  dimension: string;
  message: string;
  suggested_fix: string;
  text_locator?: Record<string, unknown>;
};

/** 决策/恢复成功响应（DecisionResponse）。 */
export type DecisionResponse = {
  run: RunSnapshot;
  decision_id: string;
  command_id: string;
};

// ---------------------------------------------------------------------------
// Task 7C：Story Bible / Canon 类型（与 backend/app/api/schemas.py 同步）
// ---------------------------------------------------------------------------

/** 正式 Canon 条目（CanonEntryRead：fact / timeline_event / plot_thread）。 */
export type CanonEntry = {
  id: string;
  type: "fact" | "timeline_event" | "plot_thread";
  text: string;
  status: string;
  created_at: string;
  chapter_id: string | null;
  story_time: Record<string, unknown> | null;
  entities: unknown[] | null;
  state: string | null;
  planned_resolution: string | null;
};

/** 项目正式 Story Bible 快照（CanonSnapshotRead）。 */
export type CanonSnapshot = {
  project_id: string;
  facts: CanonEntry[];
  timeline_events: CanonEntry[];
  plot_threads: CanonEntry[];
};

/** Canon 候选内容（决策前由后端校验，字段与后端候选 content JSONB 一致）。 */
export type CanonCandidateContent = {
  claim: string;
  entity_id?: string | null;
  paragraph_ref: string;
  effective_story_time?: { value: string; precision?: string } | null;
  narrative_knowledge?: string;
  entities?: string[];
  state?: string;
  planned_resolution?: string | null;
  [key: string]: unknown;
};

/** Canon 候选（CanonCandidateRead：fact / timeline_event / plot_thread 同构）。 */
export type CanonCandidate = {
  id: string;
  project_id: string;
  chapter_id: string | null;
  scene_id: string | null;
  scope: "chapter" | "scene";
  scope_identity: string;
  candidate_type: "fact" | "timeline_event" | "plot_thread";
  status: "pending" | "accepted" | "rejected" | "deferred" | "discarded";
  source_identity: string;
  content: CanonCandidateContent;
  local_key: string | null;
  generation_run_id: string | null;
};

/** 场景/章节候选列表（CanonCandidateListRead）。 */
export type CanonCandidateList = {
  target_type: "scene" | "chapter";
  target_id: string;
  source_revision_id: string | null;
  run_id: string | null;
  run_status: string | null;
  items: CanonCandidate[];
};

/** Canon 运行创建请求（CanonRunCreateRequest）。 */
export type CanonRunCreateRequest = {
  canon_scope: "chapter" | "scene";
  accepted_chapter_revision_id?: string | null;
  accepted_scene_revision_id?: string | null;
  chapter_intent?: Record<string, unknown> | null;
  author_feedback?: Record<string, unknown> | null;
  scene_base_revision_ids?: Record<string, string | null>;
};

/** Canon 逐条决策项（confirm|reject|defer）。 */
export type CanonDecisionItem = {
  candidate_id: string;
  candidate_type: string;
  decision: "confirm" | "reject" | "defer";
  local_key: string | null;
};

/** Canon 决策提交请求（CanonDecisionRequest）。 */
export type CanonDecisionRequest = {
  idempotency_key: string;
  expected_run_version: number;
  canon_scope: "chapter" | "scene";
  decision: "confirm" | "reject" | "defer" | "cancel";
  cancel_scope?: "confirm" | "run" | null;
  candidate_decisions: CanonDecisionItem[];
};
