/**
 * Task 7B Playwright 测试：选中片段续写/改写、审校问题展示、接受/反馈/取消、
 * 澄清恢复、暂停恢复、SSE 进度更新（Last-Event-ID 重连 + 去重）、幂等键。
 *
 * 约定：
 * - 资源/plan/场景版本由固定 fixture（backend `app.db.e2e_fixtures`，确定性
 *   数据，不依赖真实模型）播种；前端通过真实 API 交互；
 * - 每个测试使用唯一项目名前缀，E2E 库在 globalSetup 中清空；
 * - 所有由 UI 发起的 POST 命令请求断言携带 Idempotency-Key。
 */

import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

const BACKEND = path.resolve(__dirname, "..", "..", "backend");
const FIXTURE_PY = ".venv\\Scripts\\python.exe";

let _seq = 0;

// 测试结束时页面可能仍有后台 workflow 请求在路由回调中；先解除路由，
// 避免页面销毁导致已在途的 mock 回调抛出 Response/request context disposed。
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

async function apiKey(prefix: string): Promise<string> {
  _seq += 1;
  return `${prefix}-${_seq}-${Date.now()}`;
}

/** 通过代理创建 项目/卷/章/场景 层级，返回各资源 id。 */
async function createHierarchy(request: APIRequestContext, prefix: string) {
  const project = await request.post("/api/projects", {
    data: { name: `${prefix}-P`, genre: "g", target_reader: "r", default_style: "s" },
    headers: { "Idempotency-Key": await apiKey("proj") },
  });
  if (!project.ok()) throw new Error(`create project failed: ${await project.text()}`);
  const p = (await project.json()) as { id: string };

  const volume = await request.post(`/api/projects/${p.id}/volumes`, {
    data: { name: "V", goal: "g", mainline: "m", time_range: "r" },
    headers: { "Idempotency-Key": await apiKey("vol") },
  });
  if (!volume.ok()) throw new Error(`create volume failed: ${await volume.text()}`);
  const v = (await volume.json()) as { id: string };

  const chapter = await request.post(`/api/volumes/${v.id}/chapters`, {
    data: { title: "章", pov: "p", chapter_intent: { text: "" } },
    headers: { "Idempotency-Key": await apiKey("ch") },
  });
  if (!chapter.ok()) throw new Error(`create chapter failed: ${await chapter.text()}`);
  const c = (await chapter.json()) as { id: string };

  const scene = await request.post(`/api/chapters/${c.id}/scenes`, {
    data: { title: "场景", pov: "p", location: "", story_time: "", goal: "" },
    headers: { "Idempotency-Key": await apiKey("sc") },
  });
  if (!scene.ok()) throw new Error(`create scene failed: ${await scene.text()}`);
  const s = (await scene.json()) as { id: string };
  return { projectId: p.id, volumeId: v.id, chapterId: c.id, sceneId: s.id };
}

/** 运行后端确定性 fixture（固定数据，不经 shell 避免转义问题）。 */
function fixture(...args: string[]): string {
  return execFileSync(FIXTURE_PY, ["-m", "app.db.e2e_fixtures", ...args], {
    cwd: BACKEND,
    encoding: "utf-8",
    env: {
      ...process.env,
      E2E_DATABASE_URL: "postgresql+psycopg://postgres:postgres@localhost:5432/novel_e2e",
    },
  }).trim();
}

/** 播种 accepted plan。 */
function seedPlan(chapterId: string, sceneId?: string): string {
  return fixture(
    "seed-plan",
    "--chapter-id",
    chapterId,
    ...(sceneId ? ["--scene-id", sceneId] : []),
  );
}

/** 播种场景 accepted 版本（固定正文）。 */
function seedSceneAccepted(sceneId: string, text: string): string {
  return fixture("seed-scene-accepted", "--scene-id", sceneId, "--text", text);
}

/** 推进运行到指定状态（固定事件 payload）。 */
function advanceRun(runId: string, args: string[]): void {
  fixture("advance", "--run-id", runId, ...args);
}

/** 在导航树中展开层级并点击场景，等待编辑器加载。 */
async function openScene(page: Page, projectName: string, sceneTitle: string) {
  await page.goto("/");
  await expect(page.getByTestId("nav-pane")).toBeVisible();
  await page.locator(".tree-label", { hasText: projectName }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-volume-name")).toBeVisible();
  await page.locator(".tree-label").filter({ has: page.getByText("📖 V", { exact: true }) }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-chapter-title")).toBeVisible();
  await page.locator(".tree-label").filter({ has: page.getByText("📃 章", { exact: true }) }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-scene-title")).toBeVisible();
  await page.locator(".scene-item", { hasText: sceneTitle }).click();
  await expect(page.getByTestId("editor-pane")).toContainText(`场景：${sceneTitle}`);
}

/**
 * 通过独立 API 上下文读取 workflow，避免 page.route 的 route.fetch 响应在
 * 页面后台请求收尾时被 Playwright 提前销毁。
 */
async function fetchWorkflowForRoute(route: Route, request: APIRequestContext) {
  const response = await request.get(route.request().url());
  const body = (await response.json()) as Record<string, unknown>;
  return { response, body };
}

/** 打开场景并启动一个 review 运行（审校）。 */
async function startReviewRun(page: Page, projectName: string): Promise<void> {
  await openScene(page, projectName, "场景");
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  await expect(page.getByTestId("run-status")).toContainText("排队中");
}

test("选中片段续写创建运行（携带选中文本与幂等键）", async ({ page, request }) => {
  const prefix = `r1-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);
  seedSceneAccepted(sceneId, "第一章开篇。");

  let runCreateBody: Record<string, unknown> | null = null;
  page.on("request", (req) => {
    if (req.method() === "POST" && /\/api\/scenes\/[^/]+\/runs$/.test(new URL(req.url()).pathname)) {
      runCreateBody = req.postDataJSON();
    }
  });

  await openScene(page, `${prefix}-P`, "场景");
  // 全选编辑器正文作为"选中片段"。编辑器（immediatelyRender:false）异步挂载，
  // 首个测试冷启动时可能未就绪：先等 .tiptap 渲染出内容，再聚焦与全选。
  await page.locator(".tiptap").click();
  await page.waitForFunction(() => {
    const el = document.querySelector(".tiptap") as HTMLElement | null;
    return !!el && (el.textContent ?? "").length > 0;
  });
  await page.locator(".tiptap").focus();
  await page.keyboard.press("ControlOrMeta+a");
  // 浏览器全选（Ctrl+A）后 DOM selection 异步就绪；等待其生效后再点击续写。
  await page.waitForFunction(() => (window.getSelection()?.toString() ?? "").length > 0);
  await expect(page.locator(".tiptap")).toContainText("第一章开篇");

  await page.getByTestId("btn-continue").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  await expect(page.getByTestId("run-status")).toContainText("排队中");
  await expect(page.getByTestId("run-meta")).toContainText("continue");

  // 运行创建请求携带选中片段文本。
  expect(runCreateBody).not.toBeNull();
  expect(runCreateBody!.request_type).toBe("continue");
  expect((runCreateBody!.author_feedback as { text?: string }).text).toContain("第一章开篇");
  // 无 accepted 版本时不携带基线；有 accepted 版本时基线等于 accepted。
  expect(runCreateBody!.base_scene_revision_id).not.toBeNull();
});

/** 读取当前运行面板的完整 run_id。 */
test("场景运行前置检查使用刷新后的 workflow plan 指针", async ({ page, request }) => {
  const prefix = `workflow-refresh-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);
  seedSceneAccepted(sceneId, "运行前检查需要最新计划");

  let workflowReads = 0;
  let runCreateBody: Record<string, unknown> | null = null;
  await page.route("**/api/chapters/*/workflow", async (route) => {
    workflowReads += 1;
    const { response, body } = await fetchWorkflowForRoute(route, request);
    const workflow = body as {
      plan: { accepted_revision_id: string | null };
      scenes: Array<Record<string, unknown>>;
    };
    workflow.scenes = [
      {
        scene_id: sceneId,
        order: 0,
        title: "场景",
        status: "planned",
        accepted_revision_id: null,
        current_run_id: null,
        blocking_reasons: [],
      },
    ];
    if (workflowReads === 2) workflow.plan.accepted_revision_id = "latest-plan-from-refresh";
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      json: body,
    });
  });
  await page.route("**/api/scenes/*/runs", async (route) => {
    runCreateBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: "run-workflow-refresh",
        thread_id: "run-workflow-refresh",
        project_id: "project",
        target_id: sceneId,
        run_scope: "scene",
        request_type: "review",
        status: "queued",
        run_version: 1,
        current_scene_id: sceneId,
        current_node: null,
        pending_node: null,
        pause_reason: null,
        clarification_questions: [],
        last_error_code: null,
        last_event_sequence: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }),
    });
  });

  await openScene(page, `${prefix}-P`, "场景");
  const readsBeforeRun = workflowReads;
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  expect(workflowReads).toBeGreaterThan(readsBeforeRun);
  expect(runCreateBody).not.toBeNull();
  const capturedBody = runCreateBody as unknown as Record<string, unknown>;
  expect(capturedBody.plan_revision_id).toBe("latest-plan-from-refresh");
});

test("workflow 阻断时不创建场景运行", async ({ page, request }) => {
  const prefix = `workflow-blocked-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);
  seedSceneAccepted(sceneId, "队列阻断时不得运行");

  let runRequests = 0;
  await page.route("**/api/chapters/*/workflow", async (route) => {
    const { response, body } = await fetchWorkflowForRoute(route, request);
    const workflow = body as {
      phase: string;
      blocking_reasons: string[];
      scenes: Array<Record<string, unknown>>;
    };
    workflow.phase = "blocked";
    workflow.blocking_reasons = ["scene_blocked:manual_gate"];
    workflow.scenes = [
      {
        scene_id: sceneId,
        order: 0,
        title: "场景",
        status: "planned",
        accepted_revision_id: null,
        current_run_id: null,
        blocking_reasons: ["manual_gate"],
      },
    ];
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      json: body,
    });
  });
  await page.route("**/api/scenes/*/runs", async (route) => {
    runRequests += 1;
    await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
  });

  await openScene(page, `${prefix}-P`, "场景");
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("status-message")).toContainText("scene_blocked:manual_gate");
  expect(runRequests).toBe(0);
});

test("章节已有活动运行时不创建新的场景运行", async ({ page, request }) => {
  const prefix = `chapter-active-run-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);
  seedSceneAccepted(sceneId, "已有章节运行时不得重复启动");

  let runRequests = 0;
  await page.route("**/api/chapters/*/workflow", async (route) => {
    const { response, body } = await fetchWorkflowForRoute(route, request);
    const workflow = body as {
      blocking_reasons: string[];
      active_run: Record<string, unknown> | null;
      scenes: Array<Record<string, unknown>>;
    };
    workflow.blocking_reasons = [];
    workflow.active_run = {
      run_id: "run-active-chapter",
      status: "running",
      run_scope: "chapter",
      decision_target: "chapter",
    };
    workflow.scenes = [
      {
        scene_id: sceneId,
        order: 0,
        title: "场景",
        status: "planned",
        accepted_revision_id: null,
        current_run_id: null,
        blocking_reasons: [],
      },
    ];
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      json: body,
    });
  });
  await page.route("**/api/scenes/*/runs", async (route) => {
    runRequests += 1;
    await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
  });

  await openScene(page, `${prefix}-P`, "场景");
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("status-message")).toContainText("run-active-chapter");
  expect(runRequests).toBe(0);
});

test("场景处于运行中状态时不创建新的场景运行", async ({ page, request }) => {
  const prefix = `scene-not-runnable-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);
  seedSceneAccepted(sceneId, "运行中的场景不可重复启动");

  let runRequests = 0;
  await page.route("**/api/chapters/*/workflow", async (route) => {
    const { response, body } = await fetchWorkflowForRoute(route, request);
    const workflow = body as {
      scenes: Array<Record<string, unknown>>;
      active_run: Record<string, unknown> | null;
    };
    workflow.active_run = null;
    workflow.scenes = [
      {
        scene_id: sceneId,
        order: 0,
        title: "场景",
        status: "running",
        accepted_revision_id: null,
        current_run_id: "run-current-scene",
        blocking_reasons: [],
      },
    ];
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      json: body,
    });
  });
  await page.route("**/api/scenes/*/runs", async (route) => {
    runRequests += 1;
    await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
  });

  await openScene(page, `${prefix}-P`, "场景");
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("status-message")).toContainText("running");
  expect(runRequests).toBe(0);
});

test("reopening a scene hydrates its active run and decision controls", async ({ page, request }) => {
  const prefix = "run-hydration-" + Date.now();
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);

  await page.route("**/api/chapters/*/workflow", async (route) => {
    const { response, body } = await fetchWorkflowForRoute(route, request);
    const workflow = body as {
      active_run: Record<string, unknown> | null;
      pending_decision: Record<string, unknown>;
      scenes: Array<Record<string, unknown>>;
      blocking_reasons: string[];
    };
    workflow.blocking_reasons = [];
    workflow.active_run = {
      run_id: "run-hydrated-scene",
      thread_id: "run-hydrated-scene",
      project_id: "project",
      target_id: sceneId,
      run_scope: "scene",
      request_type: "review",
      status: "waiting_feedback",
      run_version: 4,
      current_scene_id: sceneId,
      current_node: "review",
      pending_node: "review",
      pause_reason: null,
      clarification_questions: [],
      last_error_code: null,
      last_event_sequence: 0,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      decision_target: "scene",
    };
    workflow.pending_decision = {
      target: "scene",
      kind: "answer_scene",
      run_id: "run-hydrated-scene",
      expected_run_version: 4,
    };
    workflow.scenes = [
      {
        scene_id: sceneId,
        order: 0,
        title: "场景",
        status: "waiting_feedback",
        accepted_revision_id: null,
        current_run_id: "run-hydrated-scene",
        blocking_reasons: [],
      },
    ];
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      json: workflow,
    });
  });
  await page.route("**/api/runs/*/events", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache", Connection: "keep-alive" },
      body: "",
    });
  });

  await openScene(page, prefix + "-P", "场景");
  await expect(page.getByTestId("run-panel")).toBeVisible();
  await expect(page.getByTestId("run-status")).toContainText("等待反馈");
  await expect(page.getByTestId("feedback-form")).toBeVisible();
  await expect(page.getByTestId("btn-run-accept")).toBeVisible();
  await expect(page.getByTestId("btn-run-cancel")).toBeVisible();
});

async function fullRunId(page: Page): Promise<string> {
  return (await page.getByTestId("run-id").getAttribute("data-full-run-id")) ?? "";
}

test("审校问题展示并在服务端确认后接受（accepted 才显示版本）", async ({ page, request }) => {
  const prefix = `r2-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);

  await startReviewRun(page, `${prefix}-P`);
  // accepted 未确认前：版本历史为空，不显示任何版本。
  await expect(page.getByTestId("history-pane")).toContainText("尚无已提交版本");

  // 服务端推进：审校问题 + 首稿草稿（固定 fixture）。
  const runId = await fullRunId(page);
  const issues = JSON.stringify([
    {
      local_key: "state-1",
      severity: "high",
      dimension: "state",
      message: "角色已死亡却再次行动",
      suggested_fix: "删除该行动或恢复角色在场状态",
      text_locator: { quote: "已死亡角色" },
    },
    {
      local_key: "term-1",
      severity: "low",
      dimension: "term",
      message: "术语变体",
      suggested_fix: "统一术语",
    },
  ]);
  advanceRun(runId, [
    "--to", "waiting_feedback",
    "--pending-node", "review",
    "--issues-json", issues,
    "--draft-text", "审校修正后的正文",
  ]);

  // SSE 推送 run_waiting_feedback → 审校问题展示。
  await expect(page.getByTestId("review-issues")).toBeVisible();
  await expect(page.getByTestId("issue-state-1")).toContainText("角色已死亡却再次行动");
  await expect(page.getByTestId("issue-term-1")).toContainText("术语变体");
  await expect(page.getByTestId("run-status")).toContainText("等待反馈");

  // 接受：服务端物化草稿版本后，版本历史才出现 accepted 版本。
  await page.getByTestId("btn-run-accept").click();
  await expect(page.getByTestId("run-status")).toContainText("已接受");
  await expect(page.getByTestId("history-pane").locator(".revision-item")).toHaveCount(1);
  await expect(page.getByTestId("history-pane").locator(".revision-badge").first()).toHaveText("accepted");
});

test("反馈后保持等待反馈状态", async ({ page, request }) => {
  const prefix = `r3-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);

  await startReviewRun(page, `${prefix}-P`);
  const runId = await fullRunId(page);
  advanceRun(runId, ["--to", "waiting_feedback", "--issues-json", "[]"]);

  await expect(page.getByTestId("run-status")).toContainText("等待反馈");
  await page.getByTestId("input-run-feedback").fill("请调整语气");
  await page.getByTestId("btn-run-feedback").click();
  // feedback 决策后仍为 waiting_feedback（后端契约）。
  await expect(page.getByTestId("run-status")).toContainText("等待反馈");
  await expect(page.getByTestId("run-meta")).toContainText("v2");
});

test("澄清恢复：pending_clarification 展示澄清问题，按钮与 API 不混用", async ({ page, request }) => {
  const prefix = `r4-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);

  await startReviewRun(page, `${prefix}-P`);
  const runId = await fullRunId(page);
  advanceRun(runId, [
    "--to", "pending_clarification",
    "--pending-node", "scene_draft_review",
    "--questions-json", JSON.stringify(["请确认本场景的目标角色是谁"]),
  ]);

  // 澄清问题展示。
  await expect(page.getByTestId("clarification-questions")).toBeVisible();
  await expect(page.getByTestId("question-0")).toContainText("目标角色");
  await expect(page.getByTestId("run-status")).toContainText("等待澄清");

  // 按钮不混用：pending_clarification 不能接受、不能恢复，只能提交澄清 + 取消。
  await expect(page.getByTestId("btn-run-accept")).toHaveCount(0);
  await expect(page.getByTestId("btn-run-resume")).toHaveCount(0);
  await expect(page.getByTestId("btn-run-cancel")).toHaveCount(1);
  await expect(page.getByTestId("btn-run-feedback")).toHaveCount(1);

  // 提交澄清（feedback API）→ waiting_feedback。
  await page.getByTestId("input-run-feedback").fill("主角是林默");
  await page.getByTestId("btn-run-feedback").click();
  await expect(page.getByTestId("run-status")).toContainText("等待反馈");
  await expect(page.getByTestId("clarification-questions")).toHaveCount(0);
});

test("暂停恢复：paused 仅显示恢复按钮，resume 后回到运行中", async ({ page, request }) => {
  const prefix = `r5-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);

  await startReviewRun(page, `${prefix}-P`);
  const runId = await fullRunId(page);
  advanceRun(runId, ["--to", "paused", "--reason", "manual", "--pending-node", "writing"]);

  await expect(page.getByTestId("run-status")).toContainText("已暂停");
  // 按钮不混用：paused 只允许恢复，不显示接受/反馈/取消。
  await expect(page.getByTestId("btn-run-resume")).toHaveCount(1);
  await expect(page.getByTestId("btn-run-accept")).toHaveCount(0);
  await expect(page.getByTestId("btn-run-feedback")).toHaveCount(0);
  await expect(page.getByTestId("btn-run-cancel")).toHaveCount(0);

  // 恢复（resume API）→ running。
  await page.getByTestId("btn-run-resume").click();
  await expect(page.getByTestId("run-status")).toContainText("运行中");
});

test("SSE 断线通过 Last-Event-ID 重连并按事件 id 去重", async ({ page, request }) => {
  const prefix = `r6-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);

  await openScene(page, `${prefix}-P`, "场景");

  const lastEventIdHeaders: string[] = [];
  let connectionCount = 0;
  // 先挂 SSE 拦截（匹配任意运行事件流），再创建运行。
  await page.route("**/api/runs/*/events", async (route) => {
    connectionCount += 1;
    lastEventIdHeaders.push(route.request().headers()["last-event-id"] || "");
    if (connectionCount === 1) {
      // 第一次连接：只返回 run_queued 事件后结束流（模拟中途断线）。
      const rid = new URL(route.request().url()).pathname.match(/\/api\/runs\/([^/]+)\/events/)?.[1] ?? "";
      const frame = {
        id: `${rid}:1`,
        sequence: 1,
        type: "run_queued",
        run_id: rid,
        payload: { run_scope: "scene", request_type: "review" },
        payload_schema: "run-event.v1",
        redaction_version: "redaction.v1",
        created_at: new Date().toISOString(),
      };
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache", Connection: "keep-alive" },
        body: `id: ${frame.id}\nevent: run_queued\ndata: ${JSON.stringify(frame)}\n\n`,
      });
    } else {
      // 后续连接：转发真实后端（按 Last-Event-ID 从下一序号重放）。
      await route.continue();
    }
  });

  // 创建运行 → SSE 第一次连接（被拦截伪造 run_queued 后断开）。
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  const runId = await fullRunId(page);

  // 断线前收到 run_queued；客户端按事件 id 去重（只记录 1 条）。
  await expect(page.getByTestId("run-event-log")).toContainText("run_queued");
  await expect(page.getByTestId("run-event-log").locator(".run-event-item")).toHaveCount(1);

  // 重连请求必须携带 Last-Event-ID = 最后收到的序号（runId:1）。
  await expect
    .poll(async () => lastEventIdHeaders.filter((h) => h.length > 0).length, { timeout: 15_000 })
    .toBeGreaterThan(0);
  expect(lastEventIdHeaders.filter((h) => h.length > 0)[0]).toBe(`${runId}:1`);

  // 服务端推进 → 经真实重连推送 run_waiting_feedback；run_queued 不重复出现。
  advanceRun(runId, ["--to", "waiting_feedback", "--issues-json", "[]"]);
  await expect(page.getByTestId("run-event-log")).toContainText("run_waiting_feedback");
  await expect(page.getByTestId("run-event-log").locator(".run-event-item")).toHaveCount(2);
  await expect(page.getByTestId("run-event-log").getByText("run_queued")).toHaveCount(1);
});

test("所有命令请求都携带 Idempotency-Key", async ({ page, request }) => {
  const missing: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/api/")) {
      if (!req.headers()["idempotency-key"]) {
        missing.push(req.url());
      }
    }
  });
  const prefix = `r7-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId, sceneId);

  await openScene(page, `${prefix}-P`, "场景");
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  const runId = await fullRunId(page);
  advanceRun(runId, ["--to", "waiting_feedback", "--issues-json", "[]"]);
  await expect(page.getByTestId("btn-run-accept")).toBeVisible();
  await page.getByTestId("btn-run-cancel").click();
  await expect(page.getByTestId("run-status")).toContainText("已取消");

  expect(missing, "POST /api/* 请求必须携带 Idempotency-Key").toEqual([]);
});
