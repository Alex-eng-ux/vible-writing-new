/**
 * Task 7A Playwright 测试：创建资源、编辑、比较版本、冲突、回滚。
 *
 * 约定：
 * - fixture 层级经 Next.js 代理（http://127.0.0.1:3000/api/*）创建，携带
 *   Idempotency-Key；UI 导航/编辑/保存/比较/回滚全部在页面上完成；
 * - 每个测试使用唯一项目名前缀，E2E 库在 globalSetup 中清空；
 * - 断言所有 POST 命令请求都携带 Idempotency-Key。
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

/** 运行确定性 E2E fixture，创建已接受的章节计划，不调用旧初始化 API。 */
function seedAcceptedPlan(chapterId: string): string {
  return execFileSync(FIXTURE_PY, ["-m", "app.db.e2e_fixtures", "seed-plan", "--chapter-id", chapterId], {
    cwd: BACKEND,
    encoding: "utf-8",
    env: {
      ...process.env,
      E2E_DATABASE_URL: "postgresql+psycopg://postgres:postgres@localhost:5432/novel_e2e",
    },
  }).trim();
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

/** 提交空场景首稿（空操作），返回根版本 id。 */
async function commitRootDraft(
  request: APIRequestContext,
  sceneId: string,
  keyPrefix: string,
): Promise<string> {
  const cs = await request.post(`/api/scenes/${sceneId}/changesets`, {
    data: {
      base_scene_revision_id: null,
      operation_format: "prosemirror_step",
      operations: [],
      source: "author",
    },
    headers: { "Idempotency-Key": await apiKey(`${keyPrefix}-cs`) },
  });
  if (!cs.ok()) throw new Error(`create root changeset failed: ${await cs.text()}`);
  const body = (await cs.json()) as { change_set_id: string };
  const commit = await request.post(`/api/changesets/${body.change_set_id}/commit`, {
    data: { author_decision: "accept" },
    headers: { "Idempotency-Key": await apiKey(`${keyPrefix}-cm`) },
  });
  if (!commit.ok()) throw new Error(`commit root failed: ${await commit.text()}`);
  return ((await commit.json()) as { id: string }).id;
}

/** 基于已接受版本提交一次正文替换（用于推进 accepted 指针/制造冲突）。 */
async function advanceRevision(
  request: APIRequestContext,
  sceneId: string,
  baseRevId: string,
  text: string,
  keyPrefix: string,
): Promise<string> {
  const revs = await request.get(`/api/scenes/${sceneId}/revisions`);
  const list = (await revs.json()) as { id: string; content_hash: string }[];
  const base = list.find((r) => r.id === baseRevId);
  if (!base) throw new Error(`base revision ${baseRevId} not found`);
  const cs = await request.post(`/api/scenes/${sceneId}/changesets`, {
    data: {
      base_scene_revision_id: baseRevId,
      operation_format: "prosemirror_step",
      operations: [{ op: "replace", value: text }],
      source: "author",
      base_content_hash: base.content_hash,
    },
    headers: { "Idempotency-Key": await apiKey(`${keyPrefix}-cs`) },
  });
  if (!cs.ok()) throw new Error(`create changeset failed: ${await cs.text()}`);
  const body = (await cs.json()) as { change_set_id: string };
  const commit = await request.post(`/api/changesets/${body.change_set_id}/commit`, {
    data: { author_decision: "accept" },
    headers: { "Idempotency-Key": await apiKey(`${keyPrefix}-cm`) },
  });
  if (!commit.ok()) throw new Error(`commit changeset failed: ${await commit.text()}`);
  return ((await commit.json()) as { id: string }).id;
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

async function typeInEditor(page: Page, text: string) {
  const editor = page.locator(".tiptap");
  await expect(editor).toBeVisible();
  await editor.click();
  await page.keyboard.type(text, { delay: 5 });
}

test("创建资源并通过 UI 编辑保存首稿", async ({ page }) => {
  const prefix = `t1-${Date.now()}`;
  await page.goto("/");

  // 1. 创建项目
  await page.getByTestId("input-project-name").fill(`${prefix}-P`);
  await page.getByTestId("btn-create-project").click();
  await expect(page.getByTestId("status-message")).toContainText("已创建项目");

  // 2. 展开项目，创建卷
  await page.locator(".tree-label", { hasText: `${prefix}-P` }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-volume-name")).toBeVisible();
  await page.getByTestId("input-volume-name").fill("V");
  await page.getByRole("button", { name: "新建卷" }).click();
  await expect(page.getByTestId("status-message")).toContainText("已创建卷");

  // 3. 展开卷，创建章
  await page.locator(".tree-label", { hasText: "V" }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-chapter-title")).toBeVisible();
  await page.getByTestId("input-chapter-title").fill("章");
  await page.getByRole("button", { name: "新建章" }).click();
  await expect(page.getByTestId("status-message")).toContainText("已创建章节");

  // 4. 展开章，创建场景
  await page.locator(".tree-label", { hasText: "章" }).getByRole("button", { name: "展开" }).click();
  await expect(page.getByTestId("input-scene-title")).toBeVisible();
  await page.getByTestId("input-scene-title").fill("场景");
  await page.getByRole("button", { name: "新建场景" }).click();
  await expect(page.getByTestId("status-message")).toContainText("已创建场景");

  // 5. 打开场景，编辑器首稿基线为空文档
  await page.locator(".scene-item", { hasText: "场景" }).click();
  await expect(page.getByTestId("editor-pane")).toContainText("场景：场景");
  await expect(page.locator(".baseline-hint")).toContainText("空文档");

  // 6. 输入正文并保存 -> 生成首稿版本
  await typeInEditor(page, "第一章开篇。");
  await page.getByTestId("btn-save").click();
  await expect(page.getByTestId("status-message")).toContainText("已保存并提交版本");

  // 7. 断言版本历史出现一条 accepted 版本，场景出现已接受标记
  await expect(page.getByTestId("history-pane").locator(".revision-item")).toHaveCount(1);
  await expect(page.getByTestId("history-pane").locator(".revision-badge").first()).toHaveText("accepted");
});

test("编辑已有版本并比较两个版本", async ({ page, request }) => {
  const prefix = `t2-${Date.now()}`;
  const { sceneId } = await createHierarchy(request, prefix);
  await openScene(page, `${prefix}-P`, "场景");

  // 第一次编辑保存 -> rev1
  await typeInEditor(page, "版本一正文");
  await page.getByTestId("btn-save").click();
  await expect(page.getByTestId("status-message")).toContainText("已保存并提交版本");
  await expect(page.getByTestId("history-pane").locator(".revision-item")).toHaveCount(1);

  // 第二次编辑（全选替换）保存 -> rev2
  await page.locator(".tiptap").click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Delete");
  await page.keyboard.type("版本二正文已更新", { delay: 5 });
  await page.getByTestId("btn-save").click();
  await expect(page.getByTestId("status-message")).toContainText("已保存并提交版本");
  await expect(page.getByTestId("history-pane").locator(".revision-item")).toHaveCount(2);

  // 比较 rev1（左）与 rev2（右）
  await page.getByTestId("btn-compare").click();
  await expect(page.getByTestId("diff-view")).toBeVisible();
  await expect(page.getByTestId("diff-summary")).toContainText("新增");
  await expect(page.getByTestId("diff-view").locator(".diff-added")).toHaveCount(1);
  await expect(sceneId).toBeTruthy();
});

test("过期基线冲突展示并可覆盖提交", async ({ page, request }) => {
  const prefix = `t3-${Date.now()}`;
  const { sceneId } = await createHierarchy(request, prefix);
  // 先提交空首稿 -> rev1，使场景有 accepted 基线。
  await commitRootDraft(request, sceneId, `${prefix}-root`);
  await openScene(page, `${prefix}-P`, "场景");

  // 本地编辑（未保存）
  await typeInEditor(page, "我的本地文本");
  await expect(page.getByTestId("status-message")).toContainText("\u00a0");

  // 服务器端推进 accepted（rev2）
  const revs = await request.get(`/api/scenes/${sceneId}/revisions`);
  const list = (await revs.json()) as { id: string }[];
  const rev1 = list[0].id;
  await advanceRevision(request, sceneId, rev1, "服务器端新文本", `${prefix}-adv`);

  // UI 保存 -> ChangeSet 基线 rev1 已过期 -> 冲突面板
  await page.getByTestId("btn-save").click();
  await expect(page.getByTestId("conflict-panel")).toBeVisible();
  await expect(page.getByTestId("status-message")).toContainText("基线已过期");

  // 覆盖提交：以我的文本基于最新版本提交
  await page.getByTestId("btn-conflict-override").click();
  await expect(page.getByTestId("status-message")).toContainText("覆盖提交");
  await expect(page.getByTestId("conflict-panel")).not.toBeVisible();
  // accepted 现在包含我的本地文本
  await expect(page.getByTestId("history-pane").locator(".revision-item")).toHaveCount(3);
});

test("手动回滚到目标版本", async ({ page, request }) => {
  const prefix = `t4-${Date.now()}`;
  const { sceneId } = await createHierarchy(request, prefix);
  await openScene(page, `${prefix}-P`, "场景");

  // 编辑保存 -> rev1
  await typeInEditor(page, "版本一");
  await page.getByTestId("btn-save").click();
  await expect(page.getByTestId("status-message")).toContainText("已保存并提交版本");
  const firstItem = page.getByTestId("history-pane").locator(".revision-item").nth(0);
  const rev1Id = (await firstItem.getAttribute("data-revision-id")) ?? "";

  // 编辑保存 -> rev2
  await page.locator(".tiptap").click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Delete");
  await page.keyboard.type("版本二", { delay: 5 });
  await page.getByTestId("btn-save").click();
  await expect(page.getByTestId("history-pane").locator(".revision-item")).toHaveCount(2);

  // 回滚到 rev1
  await page.getByTestId(`btn-rollback-${rev1Id}`).click();
  await expect(page.getByTestId("status-message")).toContainText("已回滚到版本");
  // 回滚生成新血缘记录，历史不删除
  await expect(page.getByTestId("history-pane").locator(".revision-item")).toHaveCount(3);
  // 编辑器内容恢复为 rev1 的正文（版本一）
  const editorText = await page.locator(".tiptap").innerText();
  expect(editorText).toContain("版本一");
  await expect(editorText).not.toContain("版本二");
});

test("所有命令请求都携带 Idempotency-Key", async ({ page }) => {
  const missing: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/api/")) {
      if (!req.headers()["idempotency-key"]) {
        missing.push(req.url());
      }
    }
  });
  const prefix = `t5-${Date.now()}`;
  await page.goto("/");
  await page.getByTestId("input-project-name").fill(`${prefix}-P`);
  await page.getByTestId("btn-create-project").click();
  await expect(page.getByTestId("status-message")).toContainText("已创建项目");
  expect(missing, "POST /api/* 请求必须携带 Idempotency-Key").toEqual([]);
});

test("context menu deletes a project after confirmation", async ({ page, request }) => {
  const prefix = `delete-${Date.now()}`;
  await createHierarchy(request, prefix);
  await page.goto("/");

  const project = page.locator(".tree-label", { hasText: `${prefix}-P` });
  await project.click({ button: "right" });
  await expect(page.getByRole("menuitem", { name: "删除" })).toBeVisible();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("menuitem", { name: "删除" }).click();
  await expect(project).not.toBeVisible();
});

test("scene workspace loads plan state from workflow and keeps the editor usable", async ({
  page,
  request,
}) => {
  const prefix = `plan-ui-${Date.now()}`;
  const { chapterId } = await createHierarchy(request, prefix);
  const planRevisionId = seedAcceptedPlan(chapterId);
  expect(planRevisionId).toBeTruthy();

  const legacyPlanRequests: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "GET" && req.url().includes("/api/chapters/") && req.url().endsWith("/plan")) {
      legacyPlanRequests.push(req.url());
    }
  });

  await openScene(page, `${prefix}-P`, "场景");

  await expect(page.getByTestId("chapter-plan-panel")).toBeVisible();
  await expect(page.getByTestId("chapter-plan-panel")).toContainText("accepted");
  await expect(page.getByTestId("chapter-plan-panel")).toContainText("e2e fixture outline");
  expect(legacyPlanRequests).toEqual([]);

  await page.locator(".tiptap").click();
  await page.keyboard.type("计划迁移后仍可编辑", { delay: 5 });
  await page.getByTestId("btn-save").click();
  await expect(page.getByTestId("status-message")).toContainText("已保存并提交版本");
});
