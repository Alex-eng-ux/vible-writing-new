/**
 * Task 7C Playwright 测试：Story Bible UI（正式 Canon + 三类候选 + 逐条决策）。
 *
 * 约定：
 * - 资源/版本/Canon 候选由固定 fixture（backend `app.db.e2e_fixtures`，
 *   确定性数据，不依赖真实模型）播种；前端通过真实 API 交互；
 * - 每个测试使用唯一项目名前缀，E2E 库在 globalSetup 中清空；
 * - 场景级确认不得显示为全局 Story Bible 更新；章节级确认才物化全局条目；
 * - 所有由 UI 发起的决策 POST 断言携带 Idempotency-Key，且失败重试复用同一键。
 */

import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const BACKEND = path.resolve(__dirname, "..", "..", "backend");
const FIXTURE_PY = ".venv\\Scripts\\python.exe";

let _seq = 0;

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

function seedPlan(chapterId: string): string {
  return fixture("seed-plan", "--chapter-id", chapterId);
}

function seedSceneAccepted(sceneId: string, text: string): string {
  return fixture("seed-scene-accepted", "--scene-id", sceneId, "--text", text);
}

function seedChapterAccepted(chapterId: string): string {
  return fixture("seed-chapter-accepted", "--chapter-id", chapterId);
}

/** 为 Canon 运行播种固定三类候选并推进到 waiting_feedback。 */
function seedCanonCandidates(runId: string): string {
  return fixture("seed-canon-candidates", "--run-id", runId);
}

/** 播种项目正式 Story Bible 条目（可选归属章节）。 */
function seedCanonEntries(projectId: string, chapterId?: string): void {
  fixture(
    "seed-canon-entries",
    "--project-id",
    projectId,
    ...(chapterId ? ["--chapter-id", chapterId] : []),
  );
}

/** 在导航树中展开层级并点击场景，等待编辑器加载。 */
async function openScene(page: Page, projectName: string, sceneTitle: string) {
  await page.goto("/");
  await expect(page.getByTestId("nav-pane")).toBeVisible();
  await page.locator(".tree-label", { hasText: projectName }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-volume-name")).toBeVisible();
  await page.locator(".tree-label", { hasText: "V" }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-chapter-title")).toBeVisible();
  await page.locator(".tree-label", { hasText: "章" }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-scene-title")).toBeVisible();
  await page.locator(".scene-item", { hasText: sceneTitle }).click();
  await expect(page.getByTestId("editor-pane")).toContainText(`场景：${sceneTitle}`);
}

/** 点击"提取候选"创建 Canon 运行，返回后端响应的 run_id。 */
async function startCanonExtract(page: Page): Promise<string> {
  const runResp = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().includes("/canon-runs"),
  );
  // 作用域切换会异步刷新 accepted source；先等待按钮真正可用，避免把
  // 正常的状态刷新延迟误判成 Canon 创建失败。
  await expect(page.getByTestId("btn-canon-start")).toBeEnabled();
  await page.getByTestId("btn-canon-start").click();
  const body = (await (await runResp).json()) as { run_id: string };
  return body.run_id;
}

/** 等待章节接受事件自动创建当前 accepted revision 的 Canon 运行。 */
async function waitForCanonRun(
  request: APIRequestContext,
  targetType: "chapter" | "scene",
  targetId: string,
): Promise<string> {
  const path = targetType === "chapter"
    ? `/api/chapters/${targetId}/canon-candidates`
    : `/api/scenes/${targetId}/canon-candidates`;
  let runId = "";
  await expect
    .poll(async () => {
      const response = await request.get(path);
      if (!response.ok()) return "";
      const body = (await response.json()) as { run_id?: string | null };
      runId = body.run_id ?? "";
      return runId;
    }, { timeout: 30_000 })
    .toMatch(/\S+/);
  return runId;
}

test("Story Bible 展示正式 Canon 与三类候选（来源/作用域/状态）", async ({ page, request }) => {
  const prefix = `sb1-${Date.now()}`;
  const { projectId, chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId);
  seedSceneAccepted(sceneId, "第一章开篇。");
  seedChapterAccepted(chapterId);
  seedCanonEntries(projectId, chapterId);

  await openScene(page, `${prefix}-P`, "场景");
  const panel = page.getByTestId("story-bible-panel");
  await expect(panel).toBeVisible();

  // 正式 Canon：三类条目（fact / timeline_event / plot_thread）。
  const official = page.getByTestId("canon-official");
  await expect(official).toContainText("林默是星门守护者");
  await expect(official).toContainText("林默在观星台发现星门异动");
  await expect(official).toContainText("星门背后的低语暗示旧神苏醒");
  await expect(official.locator(".canon-entry-item")).toHaveCount(3);

  // 场景级提取候选（固定 fixture 播种，不接入真实模型 API）。
  const runId = await startCanonExtract(page);
  // 候选尚未写入时，面板仍必须显示当前 queued/running Canon run，不能把状态清空。
  await page.getByTestId("btn-canon-refresh").click();
  await expect(page.getByTestId("canon-run-status")).toBeVisible();
  seedCanonCandidates(runId);
  await page.getByTestId("btn-canon-refresh").click();

  // 三类候选齐全，各 1 条。
  const candidates = page.getByTestId("canon-candidates");
  await expect(candidates.locator(".canon-candidate-item")).toHaveCount(3);
  await expect(candidates.locator('[data-candidate-type="fact"]')).toHaveCount(1);
  await expect(candidates.locator('[data-candidate-type="timeline_event"]')).toHaveCount(1);
  await expect(candidates.locator('[data-candidate-type="plot_thread"]')).toHaveCount(1);

  // 状态（待决策）与作用域（场景级）逐条展示。
  await expect(candidates.locator('[data-candidate-status="pending"]')).toHaveCount(3);
  await expect(candidates.locator(".canon-scope-badge", { hasText: "场景级" })).toHaveCount(3);
  await expect(candidates.locator(".canon-scope-note")).toContainText("场景级确认只更新场景局部 Canon");

  // 来源展示（来源版本短标识）。
  await expect(candidates.locator(".canon-source").first()).toContainText("来源 v");

  // 运行状态展示为等待决策。
  await expect(page.getByTestId("canon-run-status")).toContainText("等待决策");
});

test("场景级确认只更新候选状态，不显示为全局 Canon 更新", async ({ page, request }) => {
  const prefix = `sb2-${Date.now()}`;
  const { projectId, chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId);
  seedSceneAccepted(sceneId, "第一章开篇。");
  seedCanonEntries(projectId);

  await openScene(page, `${prefix}-P`, "场景");
  await expect(page.getByTestId("canon-official").locator(".canon-entry-item")).toHaveCount(3);

  const runId = await startCanonExtract(page);
  seedCanonCandidates(runId);
  await page.getByTestId("btn-canon-refresh").click();

  const fact = page.getByTestId("canon-candidates").locator('[data-candidate-type="fact"]');
  const factId = (await fact.getAttribute("data-candidate-id")) ?? "";

  // 逐条确认 fact 候选并提交。
  await page.getByTestId(`btn-decide-confirm-${factId}`).click();
  await page.getByTestId("btn-canon-submit").click();

  // 候选状态变为已确认；运行进入已接受。
  await expect(page.getByTestId(`canon-candidate-${factId}`)).toHaveAttribute("data-candidate-status", "accepted");
  await expect(page.getByTestId("canon-run-status")).toContainText("已接受");

  // 全局 Story Bible 条目数不变（场景级确认绝不生成全局条目）。
  await expect(page.getByTestId("canon-official").locator(".canon-entry-item")).toHaveCount(3);
});

test("章节级决策更新全局 Canon（confirm 物化，reject/defer 不物化）", async ({ page, request }) => {
  const prefix = `sb3-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId);
  seedSceneAccepted(sceneId, "第一章开篇。");
  seedChapterAccepted(chapterId);

  await openScene(page, `${prefix}-P`, "场景");
  // 切换到章节级目标。
  await page.getByTestId("btn-canon-scope-chapter").click();
  // 章节接受事件会由 Worker 自动创建 Canon run；这里不重复点击提取入口。
  const runId = await waitForCanonRun(request, "chapter", chapterId);
  await expect(page.getByTestId("btn-canon-start")).toBeDisabled();
  seedCanonCandidates(runId);
  await page.getByTestId("btn-canon-refresh").click();

  const candidates = page.getByTestId("canon-candidates");
  await expect(candidates.locator(".canon-candidate-item")).toHaveCount(3);
  await expect(candidates.locator(".canon-scope-badge", { hasText: "章节级" })).toHaveCount(3);

  const factId = (await candidates.locator('[data-candidate-type="fact"]').getAttribute("data-candidate-id")) ?? "";
  const timelineId = (await candidates.locator('[data-candidate-type="timeline_event"]').getAttribute("data-candidate-id")) ?? "";
  const threadId = (await candidates.locator('[data-candidate-type="plot_thread"]').getAttribute("data-candidate-id")) ?? "";

  // 逐条决策：confirm / reject / defer 各一条。
  await page.getByTestId(`btn-decide-confirm-${factId}`).click();
  await page.getByTestId(`btn-decide-reject-${timelineId}`).click();
  await page.getByTestId(`btn-decide-defer-${threadId}`).click();
  await page.getByTestId("btn-canon-submit").click();

  await expect(page.getByTestId(`canon-candidate-${factId}`)).toHaveAttribute("data-candidate-status", "accepted");
  await expect(page.getByTestId(`canon-candidate-${timelineId}`)).toHaveAttribute("data-candidate-status", "rejected");
  await expect(page.getByTestId(`canon-candidate-${threadId}`)).toHaveAttribute("data-candidate-status", "deferred");

  // 章节级 confirm 物化全局条目：fact 出现；reject/defer 不物化。
  await expect(page.getByTestId("canon-official")).toContainText("林默是星门守护者");
  await expect(page.getByTestId("canon-official").locator(".canon-entry-item")).toHaveCount(1);
  await expect(page.getByTestId("canon-official")).not.toContainText("林默在观星台发现星门异动");
});

test("Canon 决策请求携带并在失败重试时复用 Idempotency-Key", async ({ page, request }) => {
  const prefix = `sb4-${Date.now()}`;
  const { chapterId, sceneId } = await createHierarchy(request, prefix);
  seedPlan(chapterId);
  seedSceneAccepted(sceneId, "第一章开篇。");

  await openScene(page, `${prefix}-P`, "场景");
  const runId = await startCanonExtract(page);
  seedCanonCandidates(runId);
  await page.getByTestId("btn-canon-refresh").click();

  const factId = (await page.getByTestId("canon-candidates").locator('[data-candidate-type="fact"]').getAttribute("data-candidate-id")) ?? "";

  // 记录决策请求体与请求头 Idempotency-Key。
  const decisionBodies: Array<Record<string, unknown>> = [];
  const decisionHeaders: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && /\/api\/runs\/[^/]+\/canon-decisions$/.test(new URL(req.url()).pathname)) {
      decisionHeaders.push(req.headers()["idempotency-key"] || "");
    }
  });
  // 拦截 canon-decisions：第一次返回 500（模拟网络/服务失败），第二次放行。
  let failNext = true;
  await page.route("**/api/runs/*/canon-decisions", async (route) => {
    decisionBodies.push(route.request().postDataJSON());
    if (failNext) {
      failNext = false;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          code: "HTTP_ERROR", message: "simulated failure", retryable: true,
          run_id: null, request_id: "x", details: null,
        }),
      });
    } else {
      await route.continue();
    }
  });

  await page.getByTestId(`btn-decide-confirm-${factId}`).click();
  await page.getByTestId("btn-canon-submit").click();
  // 第一次失败：候选保持待决策，错误展示。
  await expect(page.getByTestId("status-message")).toContainText("HTTP_ERROR");
  await expect(page.getByTestId(`canon-candidate-${factId}`)).toHaveAttribute("data-candidate-status", "pending");

  // 重试：同一幂等键复用，请求成功。
  await page.getByTestId("btn-canon-submit").click();
  await expect(page.getByTestId(`canon-candidate-${factId}`)).toHaveAttribute("data-candidate-status", "accepted");

  // 幂等契约：两次请求携带同一键，且 body.idempotency_key 与请求头一致。
  expect(decisionBodies.length).toBe(2);
  expect(decisionHeaders.length).toBe(2);
  expect(decisionBodies[0].idempotency_key).toBe(decisionBodies[1].idempotency_key);
  expect(decisionHeaders[0]).toBe(decisionBodies[0].idempotency_key);
  expect(decisionHeaders[1]).toBe(decisionBodies[1].idempotency_key);
  // 决策请求携带运行版本与逐条候选。
  expect(decisionBodies[0].expected_run_version).toBe(1);
  expect((decisionBodies[0].candidate_decisions as Array<{ candidate_id: string; decision: string }>)[0].decision).toBe("confirm");
});
