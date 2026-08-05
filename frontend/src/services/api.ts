/**
 * Task 7A 统一 API 客户端。
 *
 * 约束：
 * - 所有请求经 Next.js 代理（next.config.mjs rewrites），前端不直接访问
 *   后端端口；
 * - 每次命令动作自动附加 `Idempotency-Key`；同一动作重试必须复用同一键，
 *   新动作必须生成新键；
 * - 统一解析后端 `ErrorEnvelope` 并抛出 `ApiError`，组件不得自行拼接请求。
 */

import type {
  CanonCandidateList,
  CanonDecisionRequest,
  CanonRunCreateRequest,
  CanonSnapshot,
  ChangeSetCreated,
  Chapter,
  ChapterPlan,
  ChapterRevision,
  DecisionResponse,
  ErrorEnvelope,
  Project,
  ProseMirrorOp,
  ResourceCreated,
  RunSnapshot,
  Scene,
  SceneRevision,
  SceneRevisionDetail,
  Volume,
} from "@/types";

/** 后端错误信封解析异常。 */
export class ApiError extends Error {
  code: string;
  retryable: boolean;
  details: unknown;

  constructor(envelope: ErrorEnvelope) {
    super(envelope.message);
    this.name = "ApiError";
    this.code = envelope.code;
    this.retryable = envelope.retryable;
    this.details = envelope.details;
  }
}

let _keyCounter = 0;

/**
 * 为一次用户动作生成稳定幂等键；同一动作重试复用，新动作生成新键。
 * 幂等键会作为 `Idempotency-Key` header 发送，值必须仅含 ISO-8859-1 字符。
 * resource 可能含中文（如项目名），故对 `command:resource` 做 FNV-1a 哈希
 * 转成十六进制，避免非 ASCII 字符进 header。
 */
function fnv1a(input: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

export function createIdempotencyKey(command: string, resource: string): string {
  _keyCounter += 1;
  return `${command}:${fnv1a(resource)}:${_keyCounter}:${Date.now()}`;
}

type RequestInitLike = {
  method?: string;
  body?: unknown;
  idempotencyKey?: string;
};

export async function requestJson<T>(path: string, init: RequestInitLike = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (init.idempotencyKey) {
    headers["Idempotency-Key"] = init.idempotencyKey;
  }
  const res = await fetch(path, {
    method: init.method ?? "GET",
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const envelope = data as ErrorEnvelope | null;
    if (envelope && typeof envelope.code === "string") {
      throw new ApiError(envelope);
    }
    throw new ApiError({
      code: "HTTP_ERROR",
      message: `HTTP ${res.status}`,
      retryable: true,
      run_id: null,
      request_id: "",
      details: null,
    });
  }
  return data as T;
}

// ---------------------------------------------------------------------------
// 资源层级
// ---------------------------------------------------------------------------

export const listProjects = () => requestJson<Project[]>("/api/projects");

export const createProject = (name: string, genre: string, targetReader: string, defaultStyle: string, key: string) =>
  requestJson<ResourceCreated>(
    "/api/projects",
    {
      method: "POST",
      body: { name, genre, target_reader: targetReader, default_style: defaultStyle },
      idempotencyKey: key,
    },
  );

export const deleteProject = (projectId: string, key: string) =>
  requestJson<void>(`/api/projects/${projectId}`, { method: "DELETE", idempotencyKey: key });

export const listVolumes = (projectId: string) =>
  requestJson<Volume[]>(`/api/projects/${projectId}/volumes`);

export const createVolume = (projectId: string, name: string, key: string) =>
  requestJson<ResourceCreated>(`/api/projects/${projectId}/volumes`, {
    method: "POST",
    body: { name, goal: "goal", mainline: "main", time_range: "range" },
    idempotencyKey: key,
  });

export const deleteVolume = (volumeId: string, key: string) =>
  requestJson<void>(`/api/volumes/${volumeId}`, { method: "DELETE", idempotencyKey: key });

export const listChapters = (volumeId: string) =>
  requestJson<Chapter[]>(`/api/volumes/${volumeId}/chapters`);

export const createChapter = (volumeId: string, title: string, pov: string, key: string) =>
  requestJson<ResourceCreated>(`/api/volumes/${volumeId}/chapters`, {
    method: "POST",
    body: { title, pov, chapter_intent: { text: "" } },
    idempotencyKey: key,
  });

export const deleteChapter = (chapterId: string, key: string) =>
  requestJson<void>(`/api/chapters/${chapterId}`, { method: "DELETE", idempotencyKey: key });

export const listScenes = (chapterId: string) =>
  requestJson<Scene[]>(`/api/chapters/${chapterId}/scenes`);

export const createScene = (chapterId: string, title: string, key: string) =>
  requestJson<ResourceCreated>(`/api/chapters/${chapterId}/scenes`, {
    method: "POST",
    body: { title, pov: "p", location: "", story_time: "", goal: "" },
    idempotencyKey: key,
  });

export const deleteScene = (sceneId: string, key: string) =>
  requestJson<void>(`/api/scenes/${sceneId}`, { method: "DELETE", idempotencyKey: key });

// ---------------------------------------------------------------------------
// 版本 / ChangeSet / 回滚
// ---------------------------------------------------------------------------

export const listSceneRevisions = (sceneId: string) =>
  requestJson<SceneRevision[]>(`/api/scenes/${sceneId}/revisions`);

export const deleteSceneRevision = (sceneId: string, revisionId: string, key: string) =>
  requestJson<void>(`/api/scenes/${sceneId}/revisions/${revisionId}`, {
    method: "DELETE",
    idempotencyKey: key,
  });

export const getSceneRevisionDetail = (sceneId: string, revisionId: string) =>
  requestJson<SceneRevisionDetail>(`/api/scenes/${sceneId}/revisions/${revisionId}`);

/** 创建作者 ChangeSet（source=author + prosemirror_step）。 */
export const createChangeSet = (
  sceneId: string,
  payload: {
    base_scene_revision_id: string | null;
    operations: ProseMirrorOp[];
    base_content_hash?: string;
  },
  key: string,
) =>
  requestJson<ChangeSetCreated>(
    `/api/scenes/${sceneId}/changesets`,
    {
      method: "POST",
      body: {
        base_scene_revision_id: payload.base_scene_revision_id,
        operation_format: "prosemirror_step",
        operations: payload.operations,
        source: "author",
        ...(payload.base_content_hash ? { base_content_hash: payload.base_content_hash } : {}),
      },
      idempotencyKey: key,
    },
  );

/** 提交 ChangeSet（author_decision=accept）。 */
export const commitChangeSet = (changeSetId: string, key: string) =>
  requestJson<SceneRevision>(`/api/changesets/${changeSetId}/commit`, {
    method: "POST",
    body: { author_decision: "accept" },
    idempotencyKey: key,
  });

/** 回滚场景到显式目标父版本（author_decision=author）。 */
export const rollbackScene = (sceneId: string, targetRevisionId: string, key: string) =>
  requestJson<SceneRevision>(`/api/scenes/${sceneId}/rollback`, {
    method: "POST",
    body: { target_revision_id: targetRevisionId, author_decision: "author" },
    idempotencyKey: key,
  });

// ---------------------------------------------------------------------------
// Task 7B：运行 / 决策 / 恢复 / plan
// ---------------------------------------------------------------------------

/** 读取章节当前 accepted plan（只读；场景运行创建的前提）。 */
export const getChapterPlan = (chapterId: string) =>
  requestJson<ChapterPlan>(`/api/chapters/${chapterId}/plan`);

/** 初始化并接受章节计划（幂等命令）：已存在 accepted plan 时直接返回当前指针。 */
export const createChapterPlan = (chapterId: string, key: string) =>
  requestJson<ChapterPlan>(`/api/chapters/${chapterId}/plan`, {
    method: "POST",
    idempotencyKey: key,
  });

export type SceneRunRequest = {
  run_scope: "scene";
  request_type: "continue" | "rewrite" | "review";
  decision_target: "scene";
  plan_revision_id: string;
  base_scene_revision_id: string | null;
  /** 选中片段续写/改写时携带选中文本；审校可不带。 */
  author_feedback?: { text: string };
};

/** 创建场景运行（选中片段续写/改写/审校）。 */
export const createSceneRun = (sceneId: string, payload: SceneRunRequest, key: string) =>
  requestJson<RunSnapshot>(`/api/scenes/${sceneId}/runs`, {
    method: "POST",
    body: payload,
    idempotencyKey: key,
  });

/** 读取运行快照。 */
export const getRun = (runId: string) => requestJson<RunSnapshot>(`/api/runs/${runId}`);

export type RunDecisionBody = {
  /** 必须等于请求头 Idempotency-Key（后端强制校验）。 */
  idempotency_key: string;
  expected_run_version: number;
  target: "scene";
  decision: "accept" | "feedback" | "cancel";
  text?: string;
  /** 首稿场景接受时携带（运行产生的 draft，随 SSE 事件下发）。 */
  draft_artifact_id?: string;
  /** 已有 accepted 版本场景接受时携带（运行产生的 ChangeSet）。 */
  change_set_id?: string;
};

/** 作者决策：accept（接受）/ feedback（反馈或澄清回答）/ cancel（取消）。 */
export const submitRunDecision = (runId: string, body: RunDecisionBody, key: string) =>
  requestJson<DecisionResponse>(`/api/runs/${runId}/decisions`, {
    method: "POST",
    body: { ...body, idempotency_key: key },
    idempotencyKey: key,
  });

export type RunResumeBody = {
  /** 必须等于请求头 Idempotency-Key（后端强制校验）。 */
  idempotency_key: string;
  expected_run_version: number;
  expected_pause_reason: string;
};

/** 恢复 paused 运行（只能用于 paused 状态）。 */
export const resumeRun = (runId: string, body: RunResumeBody, key: string) =>
  requestJson<DecisionResponse>(`/api/runs/${runId}/resume`, {
    method: "POST",
    body: { ...body, idempotency_key: key },
    idempotencyKey: key,
  });

// ---------------------------------------------------------------------------
// Task 7C：Story Bible / Canon
// ---------------------------------------------------------------------------

/** 只读返回项目正式 Story Bible（CanonFact / TimelineEvent / PlotThread）。 */
export const getProjectCanon = (projectId: string) =>
  requestJson<CanonSnapshot>(`/api/projects/${projectId}/canon`);

/** 只读返回场景级 Canon 候选（scope=scene，含已决策状态）。 */
export const getSceneCanonCandidates = (sceneId: string) =>
  requestJson<CanonCandidateList>(`/api/scenes/${sceneId}/canon-candidates`);

/** 只读返回章节级 Canon 候选（scope=chapter，scene_id 为空）。 */
export const getChapterCanonCandidates = (chapterId: string) =>
  requestJson<CanonCandidateList>(`/api/chapters/${chapterId}/canon-candidates`);

/** 创建章节 Canon 运行（run_scope=chapter；候选提取由占位 Worker/固定 fixture 完成）。 */
export const createChapterCanonRun = (chapterId: string, payload: CanonRunCreateRequest, key: string) =>
  requestJson<RunSnapshot>(`/api/chapters/${chapterId}/canon-runs`, {
    method: "POST",
    body: payload,
    idempotencyKey: key,
  });

/** 创建场景 Canon 运行（run_scope=scene；候选提取由占位 Worker/固定 fixture 完成）。 */
export const createSceneCanonRun = (sceneId: string, payload: CanonRunCreateRequest, key: string) =>
  requestJson<RunSnapshot>(`/api/scenes/${sceneId}/canon-runs`, {
    method: "POST",
    body: payload,
    idempotencyKey: key,
  });

/** 提交 Canon 逐条决策（confirm|reject|defer；每次决策一批后运行进入终态）。 */
export const submitCanonDecisions = (runId: string, body: CanonDecisionRequest, key: string) =>
  requestJson<DecisionResponse>(`/api/runs/${runId}/canon-decisions`, {
    method: "POST",
    body: { ...body, idempotency_key: key },
    idempotencyKey: key,
  });

// ---------------------------------------------------------------------------
// ProseMirror 文档工具
// ---------------------------------------------------------------------------

/** 从规范化 ProseMirror JSON 提取纯文本（用于编辑基线与 diff）。 */
export function prosemirrorToText(contentJson: string): string {
  try {
    const doc = JSON.parse(contentJson) as { content?: unknown };
    const walk = (node: unknown): string => {
      if (!node || typeof node !== "object") return "";
      const n = node as { type?: string; text?: string; content?: unknown[] };
      if (n.type === "text") return n.text ?? "";
      if (Array.isArray(n.content)) return n.content.map(walk).join("");
      return "";
    };
    return walk(doc).replace(/\u0000/g, "");
  } catch {
    return "";
  }
}

export type { ChapterRevision, ProseMirrorOp };
