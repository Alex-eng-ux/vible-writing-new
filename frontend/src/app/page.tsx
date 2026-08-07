"use client";

/**
 * Task 7A 写作工作台：作品/卷/章/场景导航 + Tiptap 正文编辑器 + 作者
 * ChangeSet 提交 + 版本比较 + 过期基线冲突展示 + 手动回滚。
 *
 * 约束：
 * - 手工编辑只通过 `source=author` + `prosemirror_step` 的 ChangeSet 接口
 *   提交，绝不直接更新正文或乐观修改 accepted 版本；
 * - 每次命令动作使用独立 `Idempotency-Key`；
 * - 冲突（SCENE_STALE）时只展示本地 vs 服务器差异，由作者选择覆盖或丢弃。
 */

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { Editor } from "@tiptap/react";

import DiffView from "@/features/editor/DiffView";
import ManuscriptEditor from "@/features/editor/ManuscriptEditor";
import RunPanel from "@/features/runs/RunPanel";
import StoryBiblePanel from "@/features/storybible/StoryBiblePanel";
import {
  ApiError,
  commitChangeSet,
  createChangeSet,
  createChapter,
  createChapterRun,
  createIdempotencyKey,
  createProject,
  createScene,
  createSceneRun,
  createVolume,
  deleteChapter,
  deleteProject,
  deleteScene,
  deleteSceneRevision,
  deleteVolume,
  getChapterWorkflow,
  getRun,
  getSceneRevisionDetail,
  listChapters,
  listProjects,
  listSceneRevisions,
  listScenes,
  listVolumes,
  prosemirrorToText,
  resumeRun,
  rollbackScene,
  submitRunDecision,
} from "@/services/api";
import { connectRunEvents, type SseEvent } from "@/services/sse";
import type {
  Chapter,
  ChapterWorkflowRead,
  Project,
  ReviewIssueItem,
  RunSnapshot,
  Scene,
  SceneRevision,
  SceneRevisionDetail,
  Volume,
} from "@/types";

type TreeNode = {
  project: Project;
  volumes: Volume[] | null;
  chapters: Record<string, Chapter[] | null>;
  scenes: Record<string, Scene[] | null>;
};

type ConflictState = {
  serverText: string;
  latestRevisionId: string;
  latestContentHash: string;
} | null;

type DeleteTarget =
  | { kind: "project"; id: string; name: string }
  | { kind: "volume"; id: string; name: string; projectId: string }
  | { kind: "chapter"; id: string; name: string; projectId: string; volumeId: string }
  | { kind: "scene"; id: string; name: string; projectId: string; volumeId: string; chapterId: string }
  | { kind: "revision"; id: string; name: string; sceneId: string };

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tree, setTree] = useState<Record<string, TreeNode>>({});
  const [selectedScene, setSelectedScene] = useState<Scene | null>(null);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [chapterWorkflow, setChapterWorkflow] = useState<ChapterWorkflowRead | null>(null);
  const [chapterIntentDraft, setChapterIntentDraft] = useState("");
  const [plannerFeedback, setPlannerFeedback] = useState("");
  const [revisions, setRevisions] = useState<SceneRevision[]>([]);
  const [acceptedDetail, setAcceptedDetail] = useState<SceneRevisionDetail | null>(null);
  const [localText, setLocalText] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState<ConflictState>(null);
  const [deleteMenu, setDeleteMenu] = useState<{ x: number; y: number; target: DeleteTarget } | null>(null);

  // 版本比较选择。
  const [cmpLeftId, setCmpLeftId] = useState<string>("");
  const [cmpRightId, setCmpRightId] = useState<string>("");
  const [cmpResult, setCmpResult] = useState<{ left: string; right: string; leftLabel: string; rightLabel: string } | null>(null);

  const [newProjectName, setNewProjectName] = useState("");
  const [newVolumeName, setNewVolumeName] = useState("");
  const [newChapterTitle, setNewChapterTitle] = useState("");
  const [newSceneTitle, setNewSceneTitle] = useState("");

  // Task 7B：运行状态机（创建/决策/恢复 + SSE 进度）。
  const [activeRun, setActiveRun] = useState<RunSnapshot | null>(null);
  const [runEvents, setRunEvents] = useState<SseEvent[]>([]);
  const [runIssues, setRunIssues] = useState<ReviewIssueItem[]>([]);
  const [sseStatus, setSseStatus] = useState<"connecting" | "open" | "closed">("closed");
  // 已处理事件 id 去重（SSE 断线重放与实时推送可能交叠）。
  const seenEventIds = useRef(new Set<string>());
  // 运行产生的物化定位：accept 决策必须携带 draft/change_set 之一（后端契约）。
  const [pendingDraftId, setPendingDraftId] = useState<string | null>(null);
  const [pendingChangeSetId, setPendingChangeSetId] = useState<string | null>(null);
  // 编辑器选中片段（由 ManuscriptEditor onSelectionUpdate 上报）。
  const [selectedText, setSelectedText] = useState("");
  // 编辑器实例（同步读取选区兜底：React state 更新有延迟，点击按钮时可能未 flush）。
  const editorRef = useRef<Editor | null>(null);
  // 按钮 mousedown 时捕获的选区：点击 focusable 按钮会使 DOM selection 被清除
  // （mousedown 早于 focus 变化），供 handleStartRun 兜底读取。
  const selectionOnMouseDownRef = useRef("");
  // 页面级 selectionchange 监听：浏览器全选（Ctrl+A）等 DOM selection 变化
  // 同步记录，避免点击按钮时（selection 已清除/ProseMirror 尚未同步）读不到。
  useEffect(() => {
    const onSelectionChange = () => {
      const t = window.getSelection()?.toString() ?? "";
      if (t) selectionOnMouseDownRef.current = t;
    };
    document.addEventListener("selectionchange", onSelectionChange);
    return () => document.removeEventListener("selectionchange", onSelectionChange);
  }, []);

  useEffect(() => {
    const closeMenu = () => setDeleteMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    document.addEventListener("click", closeMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("click", closeMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await listProjects());
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  const baselineText = useMemo(
    () => (acceptedDetail ? prosemirrorToText(acceptedDetail.content) : ""),
    [acceptedDetail],
  );

  function errorMessage(e: unknown): string {
    if (e instanceof ApiError) return `${e.code}: ${e.message}`;
    return e instanceof Error ? e.message : String(e);
  }

  function openDeleteMenu(event: MouseEvent, target: DeleteTarget) {
    event.preventDefault();
    event.stopPropagation();
    setDeleteMenu({ x: event.clientX, y: event.clientY, target });
  }

  function clearSelectedScene() {
    setSelectedScene(null);
    setRevisions([]);
    setAcceptedDetail(null);
    setLocalText("");
    setCmpResult(null);
    setCmpLeftId("");
    setCmpRightId("");
    setConflict(null);
  }

  function sceneLocation(sceneId: string) {
    for (const [projectId, node] of Object.entries(tree)) {
      for (const volume of node.volumes ?? []) {
        for (const chapter of node.chapters[volume.id] ?? []) {
          if ((node.scenes[chapter.id] ?? []).some((scene) => scene.id === sceneId)) {
            return { projectId, volumeId: volume.id, chapterId: chapter.id };
          }
        }
      }
    }
    return null;
  }

  async function handleDelete(target: DeleteTarget) {
    setDeleteMenu(null);
    if (busy || !window.confirm(`确认删除${target.name}？此操作不可撤销。`)) return;
    setBusy(true);
    setStatus("");
    try {
      if (target.kind === "project") {
        await deleteProject(target.id, createIdempotencyKey("project_delete", target.id));
        setProjects((prev) => prev.filter((project) => project.id !== target.id));
        setTree((prev) => {
          const next = { ...prev };
          delete next[target.id];
          return next;
        });
        if (selectedScene && sceneLocation(selectedScene.id)?.projectId === target.id) clearSelectedScene();
      } else if (target.kind === "volume") {
        await deleteVolume(target.id, createIdempotencyKey("volume_delete", target.id));
        if (selectedScene && sceneLocation(selectedScene.id)?.volumeId === target.id) clearSelectedScene();
        await loadVolumes(target.projectId);
      } else if (target.kind === "chapter") {
        await deleteChapter(target.id, createIdempotencyKey("chapter_delete", target.id));
        if (selectedScene && sceneLocation(selectedScene.id)?.chapterId === target.id) clearSelectedScene();
        await loadChapters(target.projectId, target.volumeId);
      } else if (target.kind === "scene") {
        await deleteScene(target.id, createIdempotencyKey("scene_delete", target.id));
        if (selectedScene?.id === target.id) clearSelectedScene();
        await loadScenes(target.projectId, target.volumeId, target.chapterId);
      } else {
        if (!selectedScene || selectedScene.id !== target.sceneId) return;
        await deleteSceneRevision(
          target.sceneId,
          target.id,
          createIdempotencyKey("scene_revision_delete", target.id),
        );
        const { detail } = await refreshSceneLatest();
        if (detail) setLocalText(prosemirrorToText(detail.content));
      }
      setStatus(`已删除${target.name}`);
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  // -------------------------------------------------------------------------
  // 资源创建
  // -------------------------------------------------------------------------

  async function handleCreateProject() {
    const name = newProjectName.trim();
    if (!name || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey("project_create", name);
      const created = await createProject(name, "g", "r", "s", key);
      setNewProjectName("");
      await refreshProjects();
      setStatus(`已创建项目 ${name}（${created.id}）`);
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateVolume(projectId: string) {
    const name = newVolumeName.trim();
    if (!name || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey("volume_create", projectId);
      await createVolume(projectId, name, key);
      setNewVolumeName("");
      await loadVolumes(projectId);
      setStatus(`已创建卷 ${name}`);
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateChapter(projectId: string, volumeId: string) {
    const title = newChapterTitle.trim();
    if (!title || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey("chapter_create", volumeId);
      await createChapter(volumeId, title, "p", key);
      setNewChapterTitle("");
      await loadChapters(projectId, volumeId);
      setStatus(`已创建章节 ${title}`);
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateScene(projectId: string, volumeId: string, chapterId: string) {
    const title = newSceneTitle.trim();
    if (!title || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey("scene_create", chapterId);
      await createScene(chapterId, title, key);
      setNewSceneTitle("");
      await loadScenes(projectId, volumeId, chapterId);
      setStatus(`已创建场景 ${title}`);
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  // -------------------------------------------------------------------------
  // 导航加载
  // -------------------------------------------------------------------------

  async function loadVolumes(projectId: string) {
    const volumes = await listVolumes(projectId);
    setTree((prev) => ({
      ...prev,
      [projectId]: { ...(prev[projectId] ?? emptyNode(projectId)), volumes },
    }));
  }

  async function loadChapters(projectId: string, volumeId: string) {
    const chapters = await listChapters(volumeId);
    setTree((prev) => {
      const node = prev[projectId] ?? emptyNode(projectId);
      return {
        ...prev,
        [projectId]: { ...node, chapters: { ...node.chapters, [volumeId]: chapters } },
      };
    });
  }

  async function loadScenes(projectId: string, volumeId: string, chapterId: string) {
    const scenes = await listScenes(chapterId);
    setTree((prev) => {
      const node = prev[projectId] ?? emptyNode(projectId);
      const chapters: Record<string, Chapter[] | null> = { ...node.chapters };
      chapters[volumeId] = chapters[volumeId] ?? null;
      return {
        ...prev,
        [projectId]: { ...node, chapters, scenes: { ...node.scenes, [chapterId]: scenes } },
      };
    });
  }

  function emptyNode(projectId: string): TreeNode {
    return {
      project: projects.find((p) => p.id === projectId) ?? {
        id: projectId, name: "?", genre: "", target_reader: "", default_style: "", created_at: "",
      },
      volumes: null,
      chapters: {},
      scenes: {},
    };
  }

  /** 项目展开/收起：已加载卷则收起，否则加载卷。 */
  async function toggleProject(projectId: string) {
    const node = tree[projectId];
    if (node && node.volumes) {
      setTree((prev) => ({
        ...prev,
        [projectId]: {
          ...(prev[projectId] ?? emptyNode(projectId)),
          volumes: null,
          chapters: {},
          scenes: {},
        },
      }));
      return;
    }
    try {
      await loadVolumes(projectId);
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }

  /** 卷展开/收起：已展开则收起（清空该卷章节及其场景缓存），否则加载章节。 */
  async function toggleVolume(projectId: string, volumeId: string) {
    const node = tree[projectId];
    if (node && node.chapters[volumeId]) {
      setTree((prev) => {
        const cur = prev[projectId] ?? emptyNode(projectId);
        const chapters = { ...cur.chapters };
        const scenes = { ...cur.scenes };
        const chapterIds = new Set((chapters[volumeId] ?? []).map((c) => c.id));
        for (const cid of Object.keys(scenes)) {
          if (chapterIds.has(cid)) {
            delete scenes[cid];
          }
        }
        delete chapters[volumeId];
        return { ...prev, [projectId]: { ...cur, chapters, scenes } };
      });
      return;
    }
    try {
      await loadChapters(projectId, volumeId);
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }

  /** 章展开/收起：已展开则收起（清空该章场景缓存），否则加载场景。 */
  async function toggleChapter(projectId: string, volumeId: string, chapterId: string) {
    const node = tree[projectId];
    if (node && node.scenes[chapterId]) {
      setTree((prev) => {
        const cur = prev[projectId] ?? emptyNode(projectId);
        const scenes = { ...cur.scenes };
        delete scenes[chapterId];
        return { ...prev, [projectId]: { ...cur, scenes } };
      });
      return;
    }
    try {
      await loadScenes(projectId, volumeId, chapterId);
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }

  // -------------------------------------------------------------------------
  // 场景选择与版本
  // -------------------------------------------------------------------------

  async function selectScene(scene: Scene) {
    setSelectedScene(scene);
    setConflict(null);
    setCmpResult(null);
    setStatus("");
    // 切换场景：清空运行状态机与 SSE 订阅（useEffect 依 run_id 自动断开）。
    setActiveRun(null);
    setRunEvents([]);
    setRunIssues([]);
    setPendingDraftId(null);
    setPendingChangeSetId(null);
    seenEventIds.current.clear();
    setSelectedText("");
    try {
      const [revs, workflow] = await Promise.all([
        listSceneRevisions(scene.id),
        getChapterWorkflow(scene.chapter_id),
      ]);
      setChapterWorkflow(workflow);
      const latest = revs[revs.length - 1] ?? null;
      setRevisions(revs);
      if (latest) {
        const detail = await getSceneRevisionDetail(scene.id, latest.id);
        setAcceptedDetail(detail);
        setLocalText(prosemirrorToText(detail.content));
        setCmpLeftId(revs[revs.length - 2]?.id ?? latest.id);
        setCmpRightId(latest.id);
      } else {
        setAcceptedDetail(null);
        setLocalText("");
        setCmpLeftId("");
        setCmpRightId("");
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === "CONTEXT_SOURCE_UNAVAILABLE") {
        clearSelectedScene();
        setStatus("当前章节已不存在，请重新展开项目和卷");
        return;
      }
      setStatus(errorMessage(e));
    }
  }

  async function loadChapterWorkflow(chapterId: string) {
    const workflow = await getChapterWorkflow(chapterId);
    setChapterWorkflow(workflow);
    setChapterIntentDraft(workflow.intent.text);
  }

  async function selectChapter(chapterId: string) {
    setSelectedChapterId(chapterId);
    setSelectedScene(null);
    setChapterWorkflow(null);
    setStatus("");
    try {
      await loadChapterWorkflow(chapterId);
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }

  async function startChapterPlanning() {
    if (!selectedChapterId || busy || !chapterIntentDraft.trim()) {
      setStatus("请输入章节意图后再启动规划");
      return;
    }
    setBusy(true);
    try {
      await createChapterRun(
        selectedChapterId,
        {
          run_scope: "chapter",
          request_type: "new_chapter",
          decision_target: "plan",
          chapter_intent: { text: chapterIntentDraft.trim() },
        },
        createIdempotencyKey("chapter_plan_run", selectedChapterId),
      );
      await loadChapterWorkflow(selectedChapterId);
      setStatus("章节规划已启动");
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitChapterDecision(decision: "accept" | "feedback" | "cancel") {
    if (!chapterWorkflow?.pending_decision.run_id || !selectedChapterId || busy) return;
    const runId = chapterWorkflow.pending_decision.run_id;
    const plan = chapterWorkflow.plan;
    const key = createIdempotencyKey(`chapter_${decision}`, runId);
    const target = chapterWorkflow.pending_decision.target === "chapter" ? "chapter" : "plan";
    setBusy(true);
    try {
      await submitRunDecision(
        runId,
        {
          idempotency_key: key,
          expected_run_version: chapterWorkflow.pending_decision.expected_run_version ?? 0,
          target,
          decision,
          text: decision === "feedback" ? plannerFeedback.trim() : undefined,
          plan_revision_id: target === "plan" && decision === "accept" ? plan.candidate_revision_id ?? undefined : undefined,
          expected_current_plan_revision_id: target === "plan" ? plan.accepted_revision_id : undefined,
          expected_plan_version: target === "plan" ? plan.candidate_version : undefined,
          chapter_revision_id: target === "chapter" && decision === "accept"
            ? chapterWorkflow.chapter_revision.staged_revision_id ?? undefined
            : undefined,
        },
        key,
      );
      setPlannerFeedback("");
      await loadChapterWorkflow(selectedChapterId);
      setStatus(decision === "accept" ? "计划已提交接受" : decision === "feedback" ? "反馈已提交" : "规划已取消");
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function startChapterReview() {
    if (!selectedChapterId || busy || !chapterWorkflow?.plan.accepted_revision_id) return;
    setBusy(true);
    try {
      await createChapterRun(
        selectedChapterId,
        {
          run_scope: "chapter",
          request_type: "review",
          decision_target: "chapter",
          plan_revision_id: chapterWorkflow.plan.accepted_revision_id,
        },
        createIdempotencyKey("chapter_review_run", selectedChapterId),
      );
      await loadChapterWorkflow(selectedChapterId);
      setStatus("章节审校已启动");
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!selectedChapterId || !chapterWorkflow?.active_run) return;
    const status = chapterWorkflow.active_run.status;
    if (!["queued", "running"].includes(status)) return;
    const timer = window.setInterval(() => {
      void loadChapterWorkflow(selectedChapterId).catch((e) => setStatus(errorMessage(e)));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [selectedChapterId, chapterWorkflow?.active_run?.status, chapterWorkflow?.active_run?.run_id]);

  /** 重新拉取场景列表与最新版本，返回最新 accepted detail（用于冲突恢复）。 */
  async function refreshSceneLatest(): Promise<{ detail: SceneRevisionDetail | null; revs: SceneRevision[] }> {
    if (!selectedScene) return { detail: null, revs: [] };
    const revs = await listSceneRevisions(selectedScene.id);
    setRevisions(revs);
    const latest = revs[revs.length - 1] ?? null;
    if (!latest) {
      setAcceptedDetail(null);
      setCmpLeftId("");
      setCmpRightId("");
      return { detail: null, revs };
    }
    const detail = await getSceneRevisionDetail(selectedScene.id, latest.id);
    setAcceptedDetail(detail);
    // 同步比较选择：左=倒数第二个版本（若存在），右=最新，避免保存/回滚后
    // 左右仍指向旧版本而无法比较。
    setCmpLeftId(revs[revs.length - 2]?.id ?? latest.id);
    setCmpRightId(latest.id);
    return { detail, revs };
  }

  // -------------------------------------------------------------------------
  // Task 7B：运行创建 / SSE 订阅 / 决策 / 恢复
  // -------------------------------------------------------------------------

  /** 启动场景运行：continue（续写选中片段）/ rewrite（改写）/ review（审校）。 */
  async function handleStartRun(requestType: "continue" | "rewrite" | "review") {
    if (!selectedScene || busy) return;
    // 续写/改写必须选中文本。selectedText 由 onSelectionUpdate 异步上报，按钮
    // 点击时 React state 与 ProseMirror state 可能尚未同步（DOM selection 已就绪
    // 但点击 focusable 按钮会在 focus 变化时清除 DOM selection）。兜底读取顺序：
    // ProseMirror state → mousedown 时捕获的选区 → 当前 DOM selection。
    let selection = selectedText.trim();
    if (!selection && editorRef.current) {
      const { from, to } = editorRef.current.state.selection;
      selection = editorRef.current.state.doc.textBetween(from, to, "\n").trim();
    }
    if (!selection) selection = selectionOnMouseDownRef.current;
    if (!selection) {
      selection = window.getSelection()?.toString().trim() ?? "";
    }
    if (requestType !== "review" && !selection) {
      setStatus("请先在编辑器中选择要续写/改写的文本");
      return;
    }
    try {
      // 运行前重新读取权威工作流快照，避免 accepted plan 或队列阻断状态在缓存后发生变化。
      const workflow = await getChapterWorkflow(selectedScene.chapter_id);
      setChapterWorkflow(workflow);
      if (workflow.blocking_reasons.length > 0) {
        setStatus(`当前工作流已阻断：${workflow.blocking_reasons.join("；")}`);
        return;
      }
      if (workflow.active_run) {
        setStatus(`当前章节已有进行中的运行：${workflow.active_run.run_id}`);
        return;
      }
      const workflowScene = workflow.scenes.find((scene) => scene.scene_id === selectedScene.id);
      if (!workflowScene) {
        setStatus("当前场景不在 accepted plan 队列中，无法启动运行");
        return;
      }
      const runnableSceneStatuses = new Set(["planned", "accepted"]);
      if (
        !runnableSceneStatuses.has(workflowScene.status)
        || workflowScene.current_run_id
        || workflowScene.blocking_reasons.length > 0
      ) {
        const reasons = workflowScene.blocking_reasons.length > 0
          ? workflowScene.blocking_reasons.join("；")
          : `status=${workflowScene.status}`;
        setStatus(`当前场景暂不可运行：${reasons}`);
        return;
      }
      const acceptedPlanRevisionId = workflow.plan.accepted_revision_id;
      if (!acceptedPlanRevisionId) {
        setStatus("章节尚无 accepted plan，请先完成章节规划并接受计划");
        return;
      }
      const key = createIdempotencyKey("scene_run", selectedScene.id);
      const run = await createSceneRun(
        selectedScene.id,
        {
          run_scope: "scene",
          request_type: requestType,
          decision_target: "scene",
          plan_revision_id: acceptedPlanRevisionId,
          base_scene_revision_id: selectedScene.accepted_scene_revision_id,
          ...(selection ? { author_feedback: { text: selection } } : {}),
        },
        key,
      );
      // 新运行：清空事件/审校问题/物化定位，挂载 SSE。
      seenEventIds.current.clear();
      setRunEvents([]);
      setRunIssues([]);
      setPendingDraftId(null);
      setPendingChangeSetId(null);
      setActiveRun(run);
      setStatus(`运行已创建（${run.status}）`);
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }

  /** SSE 事件处理：去重 + 更新运行快照 + accepted 后刷新版本（服务端确认后显示）。 */
  async function handleRunEvent(event: SseEvent) {
    if (!activeRun) return;
    if (seenEventIds.current.has(event.id)) return;
    seenEventIds.current.add(event.id);
    setRunEvents((prev) => [...prev, event]);
    // 审校问题与草稿定位来自 run_waiting_feedback 事件 payload（固定 fixture 数据）。
    if (event.event === "run_waiting_feedback") {
      const payload = (JSON.parse(event.data || "{}") as {
        payload?: { issues?: ReviewIssueItem[]; draft_artifact_id?: string; change_set_id?: string };
      }).payload;
      setRunIssues(payload?.issues ?? []);
      if (payload?.draft_artifact_id) setPendingDraftId(payload.draft_artifact_id);
      if (payload?.change_set_id) setPendingChangeSetId(payload.change_set_id);
    }
    try {
      const snap = await getRun(activeRun.run_id);
      setActiveRun(snap);
      if (snap.status === "accepted") {
        // accepted 只在服务端确认版本后显示：刷新场景与版本历史。
        await refreshSceneLatest();
        setStatus("运行已接受，服务端版本已确认");
      }
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }

  // SSE 订阅：activeRun 变化时连接，卸载/切换时断开。
  useEffect(() => {
    if (!activeRun) return;
    setSseStatus("connecting");
    const disconnect = connectRunEvents(activeRun.run_id, {
      onEvent: (event) => void handleRunEvent(event),
      onStatus: (s) => setSseStatus(s),
    });
    return disconnect;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRun?.run_id]);

  /** 作者决策：accept / feedback（含澄清回答）/ cancel。 */
  async function handleDecision(decision: "accept" | "feedback" | "cancel", text?: string) {
    if (!activeRun || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey(`run_${decision}`, activeRun.run_id);
      const resp = await submitRunDecision(
        activeRun.run_id,
        {
          idempotency_key: key,
          expected_run_version: activeRun.run_version,
          target: "scene",
          decision,
          ...(text ? { text } : {}),
          // 接受时必须携带服务端确认的物化定位（draft/change_set 二选一）。
          ...(decision === "accept" && pendingDraftId ? { draft_artifact_id: pendingDraftId } : {}),
          ...(decision === "accept" && pendingChangeSetId ? { change_set_id: pendingChangeSetId } : {}),
        },
        key,
      );
      setActiveRun(resp.run);
      setStatus(
        decision === "accept"
          ? "运行已接受"
          : decision === "cancel"
            ? "运行已取消"
            : "反馈已提交，等待继续",
      );
      if (resp.run.status === "accepted") {
        await refreshSceneLatest();
        setStatus("运行已接受，服务端版本已确认");
      }
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  /** 恢复 paused 运行（resume API，仅 paused 状态）。 */
  async function handleResume() {
    if (!activeRun || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey("run_resume", activeRun.run_id);
      const resp = await resumeRun(
        activeRun.run_id,
        {
          idempotency_key: key,
          expected_run_version: activeRun.run_version,
          expected_pause_reason: activeRun.pause_reason ?? "manual",
        },
        key,
      );
      setActiveRun(resp.run);
      setStatus("运行已恢复");
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  // -------------------------------------------------------------------------
  // 保存（ChangeSet 创建 + 提交）
  // -------------------------------------------------------------------------

  async function handleSave() {
    if (!selectedScene || busy) return;
    if (localText === baselineText) {
      setStatus("没有需要保存的变更");
      return;
    }
    setBusy(true);
    setStatus("");
    try {
      // 基线取当前已加载的 accepted 版本详情（保存后已刷新），不能依赖
      // selectedScene.accepted_scene_revision_id：该字段仅在场景列表刷新时
      // 更新，连续两次保存会因基线仍为 null 而误报 SCENE_STALE。
      const baseRevId = acceptedDetail?.id ?? null;
      // 生成后端最小解释器支持的操作：首稿用 insert，已接受版本用 replace/delete。
      const ops =
        baseRevId === null
          ? localText === ""
            ? []
            : [{ op: "insert" as const, value: localText }]
          : localText === ""
            ? [{ op: "delete" as const }]
            : [{ op: "replace" as const, value: localText }];
      const csKey = createIdempotencyKey("scene_changeset", selectedScene.id);
      const changeSet = await createChangeSet(
        selectedScene.id,
        {
          base_scene_revision_id: baseRevId,
          operations: ops,
          base_content_hash: acceptedDetail?.content_hash,
        },
        csKey,
      );
      const commitKey = createIdempotencyKey("changeset_commit", changeSet.change_set_id);
      const rev = await commitChangeSet(changeSet.change_set_id, commitKey);
      setStatus(`已保存并提交版本 ${rev.id}（status=${rev.status}）`);
      // 刷新场景与版本（accepted 指针随提交推进）。
      const { detail } = await refreshSceneLatest();
      if (detail) {
        setLocalText(prosemirrorToText(detail.content));
      }
    } catch (e) {
      if (e instanceof ApiError && (e.code === "SCENE_STALE" || e.code === "SCENE_STATE_INCOMPATIBLE")) {
        await enterConflict();
      } else {
        setStatus(errorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }

  /** 基线过期：加载服务器最新内容并进入冲突面板。 */
  async function enterConflict() {
    setStatus("基线已过期：服务器上已有更新的版本");
    try {
      const { detail } = await refreshSceneLatest();
      if (detail) {
        setConflict({
          serverText: prosemirrorToText(detail.content),
          latestRevisionId: detail.id,
          latestContentHash: detail.content_hash,
        });
      }
    } catch (e) {
      setStatus(errorMessage(e));
    }
  }

  /** 冲突：以本地文本为基准，基于最新 accepted 版本重新创建并提交 ChangeSet。 */
  async function handleConflictOverride() {
    if (!selectedScene || !conflict || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const ops =
        localText === ""
          ? [{ op: "delete" as const }]
          : [{ op: "replace" as const, value: localText }];
      const csKey = createIdempotencyKey("scene_changeset_override", selectedScene.id);
      const changeSet = await createChangeSet(
        selectedScene.id,
        {
          base_scene_revision_id: conflict.latestRevisionId,
          operations: ops,
          base_content_hash: conflict.latestContentHash,
        },
        csKey,
      );
      const commitKey = createIdempotencyKey("changeset_commit_override", changeSet.change_set_id);
      await commitChangeSet(changeSet.change_set_id, commitKey);
      setConflict(null);
      setStatus("已基于服务器最新版本覆盖提交");
      const { detail } = await refreshSceneLatest();
      if (detail) setLocalText(prosemirrorToText(detail.content));
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  /** 冲突：丢弃本地更改，编辑器恢复为服务器最新内容。 */
  function handleConflictDiscard() {
    if (!conflict) return;
    setConflict(null);
    setLocalText(conflict.serverText);
    setStatus("已丢弃本地更改，恢复为服务器版本");
  }

  // -------------------------------------------------------------------------
  // 版本比较
  // -------------------------------------------------------------------------

  async function handleCompare() {
    if (!selectedScene || !cmpLeftId || !cmpRightId || cmpLeftId === cmpRightId) {
      setStatus("请选择两个不同版本进行比较");
      return;
    }
    setBusy(true);
    try {
      const [left, right] = await Promise.all([
        getSceneRevisionDetail(selectedScene.id, cmpLeftId),
        getSceneRevisionDetail(selectedScene.id, cmpRightId),
      ]);
      setCmpResult({
        left: prosemirrorToText(left.content),
        right: prosemirrorToText(right.content),
        leftLabel: shortId(left.id),
        rightLabel: shortId(right.id),
      });
      setStatus("");
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  // -------------------------------------------------------------------------
  // 回滚
  // -------------------------------------------------------------------------

  async function handleRollback(revisionId: string) {
    if (!selectedScene || busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey("scene_rollback", selectedScene.id);
      const rev = await rollbackScene(selectedScene.id, revisionId, key);
      setStatus(`已回滚到版本 ${shortId(revisionId)}（新记录 ${shortId(rev.id)}）`);
      const { detail } = await refreshSceneLatest();
      if (detail) {
        setLocalText(prosemirrorToText(detail.content));
        // 场景列表刷新 accepted 指针。
        const node = selectedScene ? tree[Object.keys(tree)[0]] : null;
        void node;
        if (selectedScene.chapter_id) {
          const projectId = projects.find((p) =>
            (tree[p.id]?.volumes ?? []).some((v) =>
              (tree[p.id]?.chapters[v.id] ?? []).some((c) => c.id === selectedScene.chapter_id),
            ),
          )?.id;
          if (projectId) {
            const volume = (tree[projectId]?.volumes ?? []).find((v) =>
              (tree[projectId]?.chapters[v.id] ?? []).some((c) => c.id === selectedScene.chapter_id),
            );
            if (volume) {
              await loadScenes(projectId, volume.id, selectedScene.chapter_id);
            }
          }
        }
      }
    } catch (e) {
      setStatus(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function shortId(id: string): string {
    return id.slice(0, 8);
  }

  function phaseLabel(phase: ChapterWorkflowRead["phase"]): string {
    const labels: Record<ChapterWorkflowRead["phase"], string> = {
      intent_required: "需要章节意图",
      planning: "规划中",
      plan_feedback: "计划待决策",
      scene_generation: "场景生成中",
      scene_feedback: "场景待反馈",
      chapter_review: "章节待审校",
      chapter_feedback: "章节待反馈",
      canon_feedback: "Canon 待决策",
      completed: "已完成",
      blocked: "已阻断",
    };
    return labels[phase];
  }

  const sortedRevisions = useMemo(() => [...revisions].reverse(), [revisions]);
  const selectedSceneBrief = selectedScene?.scene_brief ?? null;
  const selectedChapterWorkflow =
    selectedScene && chapterWorkflow?.chapter_id === selectedScene.chapter_id ? chapterWorkflow : null;
  const selectedPlan = selectedChapterWorkflow?.plan ?? null;
  const selectedPlanRevisionId =
    selectedPlan?.status === "candidate"
      ? selectedPlan.candidate_revision_id
      : selectedPlan?.accepted_revision_id ?? selectedPlan?.candidate_revision_id;
  const selectedPlanVersion =
    selectedPlan?.status === "candidate"
      ? selectedPlan.candidate_version
      : selectedPlan?.accepted_version ?? selectedPlan?.candidate_version;
  const selectedPlanOutline =
    typeof selectedPlan?.contract?.outline === "string" ? selectedPlan.contract.outline : null;
  const planScenes = selectedPlan?.scene_briefs ?? [];

  // Task 7C：从导航树定位当前场景所属项目与章节（Story Bible 目标）。
  const sceneChapter = useMemo(() => {
    if (!selectedScene) return null;
    for (const [pid, node] of Object.entries(tree)) {
      for (const volume of node.volumes ?? []) {
        const chapter = (node.chapters[volume.id] ?? []).find(
          (c) => c.id === selectedScene.chapter_id,
        );
        if (chapter) return { projectId: pid, chapter };
      }
    }
    return null;
  }, [selectedScene, tree]);
  const selectedChapterContext = useMemo(() => {
    if (!selectedChapterId) return null;
    for (const [projectId, node] of Object.entries(tree)) {
      for (const volume of node.volumes ?? []) {
        const chapter = (node.chapters[volume.id] ?? []).find((c) => c.id === selectedChapterId);
        if (chapter) return { projectId, chapter };
      }
    }
    return null;
  }, [selectedChapterId, tree]);

  return (
    <main className="workspace">
      <header className="workspace-header">
        <h1>Novel Studio — 写作工作台</h1>
        <span className="status-message" data-testid="status-message">{status || "\u00a0"}</span>
      </header>

      <div className="workspace-body">
        {/* 左侧：导航树 */}
        <aside className="nav-pane" data-testid="nav-pane">
          <section className="pane-section">
            <h2>项目</h2>
            <div className="inline-form">
              <input
                data-testid="input-project-name"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                placeholder="项目名"
              />
              <button data-testid="btn-create-project" onClick={handleCreateProject} disabled={busy}>
                新建
              </button>
            </div>
            <ul className="tree">
              {projects.map((project) => {
                const node = tree[project.id];
                return (
                  <li key={project.id} className="tree-item">
                    <div
                      className="tree-label"
                      onContextMenu={(event) => openDeleteMenu(event, { kind: "project", id: project.id, name: project.name })}
                    >
                      <span>📁 {project.name}</span>
                      <button
                        onClick={() => void toggleProject(project.id)}
                        data-testid={`project-toggle-${project.id}`}
                        className="tree-action"
                        disabled={busy}
                      >
                        {node?.volumes ? "收起" : "展开"}
                      </button>
                    </div>
                    {node?.volumes && (
                      <ul className="tree">
                        <li className="tree-item">
                          <div className="inline-form compact">
                            <input
                              value={newVolumeName}
                              onChange={(e) => setNewVolumeName(e.target.value)}
                              placeholder="卷名"
                              data-testid="input-volume-name"
                            />
                            <button onClick={() => void handleCreateVolume(project.id)} disabled={busy}>
                              新建卷
                            </button>
                          </div>
                        </li>
                        {node.volumes.map((volume) => (
                          <li key={volume.id} className="tree-item">
                            <div
                              className="tree-label"
                              onContextMenu={(event) =>
                                openDeleteMenu(event, {
                                  kind: "volume",
                                  id: volume.id,
                                  name: volume.name,
                                  projectId: project.id,
                                })
                              }
                            >
                              <span>📖 {volume.name}</span>
                              <button
                                onClick={() => void toggleVolume(project.id, volume.id)}
                                data-testid={`volume-toggle-${volume.id}`}
                                className="tree-action"
                                disabled={busy}
                              >
                                {node?.chapters[volume.id] ? "收起" : "展开"}
                              </button>
                            </div>
                            {node.chapters[volume.id] && (
                              <ul className="tree">
                                <li className="tree-item">
                                  <div className="inline-form compact">
                                    <input
                                      value={newChapterTitle}
                                      onChange={(e) => setNewChapterTitle(e.target.value)}
                                      placeholder="章节标题"
                                      data-testid="input-chapter-title"
                                    />
                                    <button
                                      onClick={() => void handleCreateChapter(project.id, volume.id)}
                                      disabled={busy}
                                    >
                                      新建章
                                    </button>
                                  </div>
                                </li>
                                {(node.chapters[volume.id] ?? []).map((chapter) => (
                                  <li key={chapter.id} className="tree-item">
                                    <div
                                      className="tree-label"
                                      onContextMenu={(event) =>
                                        openDeleteMenu(event, {
                                          kind: "chapter",
                                          id: chapter.id,
                                          name: chapter.title,
                                          projectId: project.id,
                                          volumeId: volume.id,
                                        })
                                      }
                                    >
                                      <span>📃 {chapter.title}</span>
                                      <button
                                        onClick={() => void selectChapter(chapter.id)}
                                        data-testid={`chapter-item-${chapter.id}`}
                                        className="tree-action"
                                        disabled={busy}
                                      >
                                        打开章节工作台
                                      </button>
                                      <button
                                        onClick={() => void toggleChapter(project.id, volume.id, chapter.id)}
                                        className="tree-action"
                                        disabled={busy}
                                      >
                                        {node?.scenes[chapter.id] ? "收起" : "展开"}
                                      </button>
                                    </div>
                                    {node.scenes[chapter.id] && (
                                      <ul className="tree">
                                        <li className="tree-item">
                                          <div className="inline-form compact">
                                            <input
                                              value={newSceneTitle}
                                              onChange={(e) => setNewSceneTitle(e.target.value)}
                                              placeholder="场景标题"
                                              data-testid="input-scene-title"
                                            />
                                            <button
                                              onClick={() =>
                                                void handleCreateScene(project.id, volume.id, chapter.id)
                                              }
                                              disabled={busy}
                                            >
                                              新建场景
                                            </button>
                                          </div>
                                        </li>
                                        {(node.scenes[chapter.id] ?? []).map((scene) => (
                                          <li key={scene.id} className="tree-item">
                                            <button
                                              className="scene-item"
                                              data-testid={`scene-item-${scene.id}`}
                                              onClick={() => void selectScene(scene)}
                                              onContextMenu={(event) =>
                                                openDeleteMenu(event, {
                                                  kind: "scene",
                                                  id: scene.id,
                                                  name: scene.title,
                                                  projectId: project.id,
                                                  volumeId: volume.id,
                                                  chapterId: chapter.id,
                                                })
                                              }
                                            >
                                              ✍️ {scene.title}
                                              {scene.accepted_scene_revision_id ? " ✓" : ""}
                                            </button>
                                          </li>
                                        ))}
                                      </ul>
                                    )}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        </aside>

        {/* 中间：编辑器 */}
        <section className="editor-pane" data-testid="editor-pane">
          {selectedScene ? (
            <>
              <h2>场景：{selectedScene.title}</h2>
              <section className="chapter-plan-panel" data-testid="chapter-plan-panel">
                <div className="chapter-plan-heading">
                  <h3>章节计划</h3>
                  {selectedPlanRevisionId && (
                    <span>v{selectedPlanVersion ?? "?"} · {selectedPlan?.status}</span>
                  )}
                </div>
                {selectedPlan?.contract ? (
                  <>
                    <p className="chapter-plan-outline">
                      {selectedPlanOutline || "暂无大纲说明"}
                    </p>
                    {planScenes.length > 0 ? (
                      <ol className="chapter-plan-scenes">
                        {planScenes.map((planScene, index) => (
                          <li key={planScene.client_key || `${index}-${planScene.title || "scene"}`}>
                            <strong>{planScene.title || `场景 ${index + 1}`}</strong>
                            {Object.keys(planScene.brief).length > 0 && (
                              <span>{JSON.stringify(planScene.brief)}</span>
                            )}
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="chapter-plan-empty">当前计划还没有场景安排。</p>
                    )}
                   </>
                 ) : (
                   <p className="chapter-plan-empty">尚未生成章节计划，请先完成章节规划流程。</p>
                 )}
               </section>
              {/* Task 7B：选中片段续写/改写/审校 */}
              <div className="run-create-actions" data-testid="run-create-actions">
                <button
                  data-testid="btn-continue"
                  onClick={() => void handleStartRun("continue")}
                  onMouseDown={() => {
                    selectionOnMouseDownRef.current = window.getSelection()?.toString().trim() ?? "";
                  }}
                  disabled={busy}
                >
                  续写选中片段
                </button>
                <button
                  data-testid="btn-rewrite"
                  onClick={() => void handleStartRun("rewrite")}
                  onMouseDown={() => {
                    selectionOnMouseDownRef.current = window.getSelection()?.toString().trim() ?? "";
                  }}
                  disabled={busy}
                >
                  改写选中片段
                </button>
                <button data-testid="btn-review" onClick={() => void handleStartRun("review")} disabled={busy}>
                  审校
                </button>
                {activeRun && <span className="sse-status" data-testid="sse-status">{sseStatus}</span>}
              </div>
              <ManuscriptEditor
                doc={acceptedDetail?.content ?? null}
                onChange={setLocalText}
                onSelectionText={setSelectedText}
                onEditorReady={(editor) => {
                  editorRef.current = editor;
                }}
              />
              <div className="editor-actions">
                <button data-testid="btn-save" onClick={() => void handleSave()} disabled={busy}>
                  保存（创建并提交 ChangeSet）
                </button>
                <span className="baseline-hint">
                  基线：{acceptedDetail ? shortId(acceptedDetail.id) : "空文档（首稿）"}
                </span>
              </div>

              {conflict && (
                <div className="conflict-panel" data-testid="conflict-panel">
                  <h3>基线冲突</h3>
                  <p>服务器上已有更新的版本；你的本地修改与服务器版本不同。</p>
                  <DiffView
                    left={conflict.serverText}
                    right={localText}
                    leftLabel="服务器最新"
                    rightLabel="我的本地"
                  />
                  <div className="conflict-actions">
                    <button data-testid="btn-conflict-override" onClick={() => void handleConflictOverride()} disabled={busy}>
                      以我的文本覆盖提交
                    </button>
                    <button data-testid="btn-conflict-discard" onClick={handleConflictDiscard} disabled={busy}>
                      丢弃我的更改
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : selectedChapterId ? (
            <section className="chapter-workspace" data-testid="chapter-workspace">
              {chapterWorkflow && (
                <section className="chapter-review-actions" data-testid="chapter-review-actions">
                  {chapterWorkflow.phase === "chapter_review" && chapterWorkflow.plan.accepted_revision_id && !chapterWorkflow.active_run && !chapterWorkflow.chapter_revision.staged_revision_id && (
                    <button data-testid="btn-start-chapter-review" onClick={() => void startChapterReview()} disabled={busy}>
                      启动章节审核
                    </button>
                  )}
                  {(chapterWorkflow.pending_decision.kind === "accept_chapter" || chapterWorkflow.pending_decision.kind === "chapter_feedback") && (
                    <>
                      {chapterWorkflow.chapter_revision.review_issues.length > 0 && (
                        <ul className="chapter-review-issues" data-testid="chapter-review-issues">
                          {chapterWorkflow.chapter_revision.review_issues.map((issue) => (
                            <li key={issue.local_key}><strong>{issue.severity}</strong> {issue.message}</li>
                          ))}
                        </ul>
                      )}
                      <textarea
                        data-testid="chapter-review-feedback-input"
                        value={plannerFeedback}
                        onChange={(event) => setPlannerFeedback(event.target.value)}
                        rows={3}
                        placeholder="章节审核反馈"
                      />
                      {chapterWorkflow.pending_decision.kind === "accept_chapter" && chapterWorkflow.chapter_revision.staged_revision_id && (
                        <button data-testid="btn-accept-chapter-revision" onClick={() => void submitChapterDecision("accept")} disabled={busy}>
                          接受章节版本
                        </button>
                      )}
                      <button data-testid="btn-chapter-review-feedback" onClick={() => void submitChapterDecision("feedback")} disabled={busy || !plannerFeedback.trim()}>
                        提交审核反馈
                      </button>
                    </>
                  )}
                </section>
              )}
              <div className="chapter-workspace-heading">
                <h2>章节工作台</h2>
                {chapterWorkflow && <span data-testid="chapter-workflow-phase">{phaseLabel(chapterWorkflow.phase)}</span>}
              </div>
              {chapterWorkflow && (
                <section className="chapter-canon-summary" data-testid="chapter-canon-summary">
                  <div className="chapter-canon-heading">
                    <h3>章节 Canon</h3>
                    <span className="chapter-canon-status">{chapterWorkflow.canon.status ?? "尚未运行"}</span>
                  </div>
                  <dl className="chapter-canon-metrics">
                    <div>
                      <dt>来源版本</dt>
                      <dd>{chapterWorkflow.canon.source_revision_id ? shortId(chapterWorkflow.canon.source_revision_id) : "暂无"}</dd>
                    </div>
                    <div>
                      <dt>待决候选</dt>
                      <dd>{chapterWorkflow.canon.pending_candidate_count}</dd>
                    </div>
                    <div>
                      <dt>当前章节版本</dt>
                      <dd>{chapterWorkflow.chapter_revision.accepted_revision_id ? shortId(chapterWorkflow.chapter_revision.accepted_revision_id) : "未接受"}</dd>
                    </div>
                  </dl>
                </section>
              )}
              {!chapterWorkflow ? <p>正在读取章节状态...</p> : (
                <>
                  <label htmlFor="chapter-intent-input">章节意图</label>
                  <textarea id="chapter-intent-input" data-testid="chapter-intent-input" value={chapterIntentDraft} onChange={(event) => setChapterIntentDraft(event.target.value)} rows={4} disabled={chapterWorkflow.phase !== "intent_required" || busy} />
                  <div className="chapter-workflow-actions">
                    {chapterWorkflow.phase === "intent_required" && <button data-testid="btn-start-chapter-planning" onClick={() => void startChapterPlanning()} disabled={busy}>启动章节规划</button>}
                    {chapterWorkflow.pending_decision.kind === "answer_planner" && <><textarea data-testid="planner-feedback-input" value={plannerFeedback} onChange={(event) => setPlannerFeedback(event.target.value)} rows={3} placeholder="回答 Planner 的问题" /><button data-testid="btn-plan-feedback" onClick={() => void submitChapterDecision("feedback")} disabled={busy || !plannerFeedback.trim()}>提交反馈</button></>}
                    {chapterWorkflow.pending_decision.kind === "accept_plan" && chapterWorkflow.plan.candidate_revision_id && <><button data-testid="btn-accept-chapter-plan" onClick={() => void submitChapterDecision("accept")} disabled={busy}>接受候选计划</button><textarea data-testid="planner-plan-feedback-input" value={plannerFeedback} onChange={(event) => setPlannerFeedback(event.target.value)} rows={2} placeholder="计划反馈（可选）" /><button data-testid="btn-plan-feedback" onClick={() => void submitChapterDecision("feedback")} disabled={busy || !plannerFeedback.trim()}>反馈并重新规划</button></>}
                    {chapterWorkflow.pending_decision.run_id && chapterWorkflow.phase === "plan_feedback" && <button data-testid="btn-plan-cancel" onClick={() => void submitChapterDecision("cancel")} disabled={busy}>取消规划</button>}
                  </div>
                  {chapterWorkflow.blocking_reasons.length > 0 && <div className="chapter-workflow-blocked" role="alert"><strong>当前阻断</strong><ul>{chapterWorkflow.blocking_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>}
                  <section className="chapter-discussion" data-testid="chapter-plan-discussion" aria-live="polite"><h3>Planner 讨论</h3>{chapterWorkflow.plan_discussion.messages.length === 0 ? <p>暂无讨论记录。</p> : chapterWorkflow.plan_discussion.messages.map((message) => <p key={String(message.message_id)}><strong>{String(message.role ?? "")}</strong> {String(message.text ?? "")}</p>)}{chapterWorkflow.plan_discussion.pending_questions.map((question) => <p key={question.question_id}>待回答：{question.text}</p>)}</section>
                  <section className="chapter-plan-candidate" data-testid="chapter-plan-candidate"><h3>计划候选</h3>{chapterWorkflow.plan.scene_briefs.length === 0 ? <p>尚未形成场景候选。</p> : <ol>{chapterWorkflow.plan.scene_briefs.map((brief) => <li key={brief.client_key}><strong>{brief.title}</strong><span>{Object.entries(brief.brief).map(([key, value]) => `${key}: ${String(value)}`).join("；")}</span></li>)}</ol>}</section>
                  <section className="chapter-scene-queue" data-testid="chapter-scene-queue"><h3>场景队列</h3>{chapterWorkflow.scenes.length === 0 ? <p>接受计划后，场景会按顺序进入队列。</p> : <ol>{chapterWorkflow.scenes.map((scene) => <li key={scene.scene_id}>{scene.order + 1}. {scene.title} <span>{scene.status}</span></li>)}</ol>}</section>
                </>
              )}
            </section>
          ) : (
            <div className="placeholder">从左侧选择一个场景开始编辑。</div>
          )}
        </section>

        {/* 右侧：运行面板 + Story Bible + 版本历史 / 比较 / 回滚 */}
        <aside className="history-pane" data-testid="history-pane">
          {!selectedScene && selectedChapterContext && (
            <StoryBiblePanel
              target={{
                projectId: selectedChapterContext.projectId,
                scene: null,
                chapter: {
                  id: selectedChapterContext.chapter.id,
                  title: selectedChapterContext.chapter.title,
                  // 工作流快照是章节接受后的权威版本；导航树刷新存在异步窗口，不能继续使用旧指针。
                  acceptedRevisionId: chapterWorkflow
                    ? chapterWorkflow.chapter_revision.accepted_revision_id
                    : selectedChapterContext.chapter.accepted_chapter_revision_id,
                },
              }}
              onStatus={setStatus}
            />
          )}
          {selectedScene && (
            <>
              {selectedScene && sceneChapter && (
                <StoryBiblePanel
                  target={{
                    projectId: sceneChapter.projectId,
                    scene: {
                      id: selectedScene.id,
                      title: selectedScene.title,
                      acceptedRevisionId: selectedScene.accepted_scene_revision_id,
                    },
                    chapter: {
                      id: sceneChapter.chapter.id,
                      title: sceneChapter.chapter.title,
                      acceptedRevisionId: sceneChapter.chapter.accepted_chapter_revision_id,
                    },
                  }}
                  onStatus={setStatus}
                />
              )}
              {activeRun && (
                <RunPanel
                  run={activeRun}
                  issues={runIssues}
                  events={runEvents}
                  busy={busy}
                  onAccept={() => void handleDecision("accept")}
                  onFeedback={(text) => void handleDecision("feedback", text)}
                  onCancel={() => void handleDecision("cancel")}
                  onResume={() => void handleResume()}
                />
              )}
              <h2>版本历史（{revisions.length}）</h2>
              {sortedRevisions.length === 0 && <p>尚无已提交版本。</p>}
              <ul className="revision-list">
                {sortedRevisions.map((rev) => (
                  <li
                    key={rev.id}
                    className="revision-item"
                    data-testid={`revision-item-${rev.id}`}
                    data-revision-id={rev.id}
                    onContextMenu={(event) =>
                      openDeleteMenu(event, {
                        kind: "revision",
                        id: rev.id,
                        name: `版本 ${shortId(rev.id)}`,
                        sceneId: rev.scene_id,
                      })
                    }
                  >
                    <span className="revision-badge">{rev.status}</span>
                    <span className="revision-id">{shortId(rev.id)}</span>
                    <span className="revision-hash">{rev.content_hash.slice(0, 8)}</span>
                    <button
                      data-testid={`btn-rollback-${rev.id}`}
                      onClick={() => void handleRollback(rev.id)}
                      disabled={busy || rev.id === (acceptedDetail?.id ?? "")}
                    >
                      回滚到此处
                    </button>
                  </li>
                ))}
              </ul>

              <h3>版本比较</h3>
              <div className="compare-form">
                <select
                  data-testid="select-rev-left"
                  value={cmpLeftId}
                  onChange={(e) => setCmpLeftId(e.target.value)}
                >
                  {sortedRevisions.map((rev) => (
                    <option key={rev.id} value={rev.id}>
                      左：{shortId(rev.id)}（{rev.status}）
                    </option>
                  ))}
                </select>
                <select
                  data-testid="select-rev-right"
                  value={cmpRightId}
                  onChange={(e) => setCmpRightId(e.target.value)}
                >
                  {sortedRevisions.map((rev) => (
                    <option key={rev.id} value={rev.id}>
                      右：{shortId(rev.id)}（{rev.status}）
                    </option>
                  ))}
                </select>
                <button data-testid="btn-compare" onClick={() => void handleCompare()} disabled={busy}>
                  比较
                </button>
              </div>
              {cmpResult && (
                <DiffView
                  left={cmpResult.left}
                  right={cmpResult.right}
                  leftLabel={cmpResult.leftLabel}
                  rightLabel={cmpResult.rightLabel}
                />
              )}
              {selectedSceneBrief && (
                <div className="scene-brief">
                  <h3>场景设定</h3>
                  <pre>{JSON.stringify(selectedSceneBrief, null, 2)}</pre>
                </div>
              )}
            </>
          )}
        </aside>
      </div>
      {deleteMenu && (
        <div
          className="delete-context-menu"
          style={{ left: deleteMenu.x, top: deleteMenu.y }}
          onClick={(event) => event.stopPropagation()}
          role="menu"
        >
          <button role="menuitem" onClick={() => void handleDelete(deleteMenu.target)} disabled={busy}>
            删除
          </button>
        </div>
      )}
    </main>
  );
}
