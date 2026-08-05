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
  createChapterPlan,
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
  getChapterPlan,
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
  ChapterPlan,
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
  const [revisions, setRevisions] = useState<SceneRevision[]>([]);
  const [chapterPlans, setChapterPlans] = useState<Record<string, ChapterPlan | null>>({});
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

  /** 初始化并接受章节计划（幂等命令）：使该章节下的场景续写/改写/审校可用。 */
  async function handleCreateChapterPlan(projectId: string, volumeId: string, chapterId: string) {
    if (busy) return;
    setBusy(true);
    setStatus("");
    try {
      const key = createIdempotencyKey("chapter_plan_init", chapterId);
      const plan = await createChapterPlan(chapterId, key);
      setChapterPlans((prev) => ({ ...prev, [chapterId]: plan }));
      setStatus(
        plan.plan_revision_id
          ? `章节计划已就绪（${plan.plan_status}）`
          : "章节计划初始化失败",
      );
      await loadVolumes(projectId);
      await loadChapters(projectId, volumeId);
    } catch (e) {
      if (e instanceof ApiError && e.code === "CONTEXT_SOURCE_UNAVAILABLE") {
        setStatus("当前章节已不存在，请重新展开项目和卷");
        await loadVolumes(projectId).catch(() => undefined);
        return;
      }
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
      const [revs, plan] = await Promise.all([
        listSceneRevisions(scene.id),
        getChapterPlan(scene.chapter_id),
      ]);
      setChapterPlans((prev) => ({ ...prev, [scene.chapter_id]: plan }));
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
      const plan = await getChapterPlan(selectedScene.chapter_id);
      if (!plan.plan_revision_id) {
        setStatus("章节尚无 accepted plan，请先点击章节旁的「生成章节计划」");
        return;
      }
      const key = createIdempotencyKey("scene_run", selectedScene.id);
      const run = await createSceneRun(
        selectedScene.id,
        {
          run_scope: "scene",
          request_type: requestType,
          decision_target: "scene",
          plan_revision_id: plan.plan_revision_id,
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

  const sortedRevisions = useMemo(() => [...revisions].reverse(), [revisions]);
  const selectedSceneBrief = selectedScene?.scene_brief ?? null;
  const selectedChapterPlan = selectedScene ? chapterPlans[selectedScene.chapter_id] : null;
  const planScenes = selectedChapterPlan?.chapter_contract?.scenes ?? [];

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
                                        onClick={() => void handleCreateChapterPlan(project.id, volume.id, chapter.id)}
                                        className="tree-action"
                                        disabled={busy}
                                      >
                                        生成章节计划
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
                  {selectedChapterPlan?.plan_revision_id && (
                    <span>v{selectedChapterPlan.plan_version ?? "?"} · {selectedChapterPlan.plan_status}</span>
                  )}
                </div>
                {selectedChapterPlan?.chapter_contract ? (
                  <>
                    <p className="chapter-plan-outline">
                      {selectedChapterPlan.chapter_contract.outline || selectedChapterPlan.plan_reason || "暂无大纲说明"}
                    </p>
                    {planScenes.length > 0 ? (
                      <ol className="chapter-plan-scenes">
                        {planScenes.map((planScene, index) => (
                          <li key={planScene.scene_id ?? `${index}-${planScene.title ?? "scene"}`}>
                            <strong>{planScene.title || `场景 ${index + 1}`}</strong>
                            {planScene.scene_brief && (
                              <span>{JSON.stringify(planScene.scene_brief)}</span>
                            )}
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="chapter-plan-empty">当前计划还没有场景安排。</p>
                    )}
                  </>
                ) : (
                  <p className="chapter-plan-empty">尚未生成章节计划，请点击左侧章节旁的按钮。</p>
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
          ) : (
            <div className="placeholder">从左侧选择一个场景开始编辑。</div>
          )}
        </section>

        {/* 右侧：运行面板 + Story Bible + 版本历史 / 比较 / 回滚 */}
        <aside className="history-pane" data-testid="history-pane">
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
