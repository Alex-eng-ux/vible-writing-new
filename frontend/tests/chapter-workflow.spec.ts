import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const BACKEND = path.resolve(__dirname, "..", "..", "backend");
const FIXTURE_PY = ".venv\\Scripts\\python.exe";

let sequence = 0;

function idempotencyKey(prefix: string): string {
  sequence += 1;
  return `chapter-workflow-${prefix}-${Date.now()}-${sequence}`;
}

async function postJson(request: APIRequestContext, url: string, data: unknown) {
  const response = await request.post(url, {
    data,
    headers: { "Idempotency-Key": idempotencyKey(url) },
  });
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy();
  return response.json();
}

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

function fixtureForChapter(chapterId: string, ...args: string[]): string {
  try {
    return fixture(...args);
  } catch (error) {
    let diagnostics = "fixture diagnostics unavailable";
    try {
      diagnostics = fixture("diagnose", "--chapter-id", chapterId);
    } catch (diagnosticError) {
      diagnostics = `diagnose failed: ${String(diagnosticError)}`;
    }
    throw new Error(`${String(error)}\n${diagnostics}`);
  }
}

async function openChapter(page: Page, projectId: string, volumeId: string, chapterId: string): Promise<void> {
  await page.goto("/");
  await page.getByTestId(`project-toggle-${projectId}`).click();
  await page.getByTestId(`volume-toggle-${volumeId}`).click();
  await page.getByTestId(`chapter-item-${chapterId}`).click();
  const chapterRow = page.locator(".tree-label").filter({ has: page.getByTestId(`chapter-item-${chapterId}`) });
  const expand = chapterRow.getByRole("button", { name: "展开" });
  if (await expand.count()) await expand.click();
  await expect(page.getByTestId("chapter-workspace")).toBeVisible();
}

async function workflow(request: APIRequestContext, chapterId: string): Promise<Record<string, any>> {
  const response = await request.get(`/api/chapters/${chapterId}/workflow`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function activeRunId(request: APIRequestContext, chapterId: string): Promise<string> {
  let runId = "";
  try {
    await expect
      .poll(async () => {
        runId = (await workflow(request, chapterId)).active_run?.run_id ?? "";
        return runId;
      }, { timeout: 30_000 })
      .toMatch(/[0-9a-f-]{20,}/);
    return runId;
  } catch (error) {
    let diagnostics = "poll diagnostics unavailable";
    try {
      diagnostics = fixture("diagnose", "--chapter-id", chapterId);
    } catch (diagnosticError) {
      diagnostics = `diagnose failed: ${String(diagnosticError)}`;
    }
    throw new Error(`${String(error)}\n${diagnostics}`);
  }
}

async function sceneRunId(page: Page): Promise<string> {
  const runId = (await page.getByTestId("run-id").getAttribute("data-full-run-id")) ?? "";
  expect(runId, "场景运行必须暴露非空 run id").toMatch(/[0-9a-f-]{20,}/);
  return runId;
}

test("new_chapter 规划由章节工作区 UI 启动并读取意图", async ({ page, request }) => {
  const project = await postJson(request, "/api/projects", {
    name: `workflow-${Date.now()}`,
    genre: "drama",
    target_reader: "adult",
    default_style: "plain",
  });
  const volume = await postJson(request, `/api/projects/${project.id}/volumes`, {
    name: "Volume 1",
    goal: "goal",
    mainline: "mainline",
    time_range: "now",
  });
  const chapter = await postJson(request, `/api/volumes/${volume.id}/chapters`, {
    title: "Chapter 1",
    pov: "narrator",
    chapter_intent: { text: "" },
  });

  await openChapter(page, project.id, volume.id, chapter.id);
  const intent = "主角必须在钟楼做出不可逆的选择";
  let runRequestBody: Record<string, unknown> | null = null;
  page.on("request", (requestEvent) => {
    if (requestEvent.method() === "POST" && requestEvent.url().endsWith(`/api/chapters/${chapter.id}/runs`)) {
      runRequestBody = requestEvent.postDataJSON() as Record<string, unknown>;
    }
  });
  await page.getByTestId("chapter-intent-input").fill(intent);
  await page.getByTestId("btn-start-chapter-planning").click();
  await expect(page.getByTestId("chapter-workflow-phase")).toHaveText("规划中");

  await expect
    .poll(async () => (await (await request.get(`/api/chapters/${chapter.id}/workflow`)).json()).phase, {
      timeout: 30_000,
    })
    .toMatch(/planning|plan_feedback|blocked/);

  const workflow = await (await request.get(`/api/chapters/${chapter.id}/workflow`)).json();
  expect(workflow.intent.text).toContain("不可逆");
  expect(runRequestBody).toMatchObject({
    run_scope: "chapter",
    request_type: "new_chapter",
    decision_target: "plan",
    chapter_intent: { text: intent },
  });
  expect(workflow.active_run?.run_id).toMatch(/[0-9a-f-]{20,}/);
  expect(workflow.plan_discussion).toBeDefined();
});

test("章节工作台从章节入口展示 workflow 状态", async ({ page, request }) => {
  const project = await postJson(request, "/api/projects", {
    name: `workflow-ui-${Date.now()}`,
    genre: "drama",
    target_reader: "adult",
    default_style: "plain",
  });
  const volume = await postJson(request, `/api/projects/${project.id}/volumes`, {
    name: "Volume 1",
    goal: "goal",
    mainline: "mainline",
    time_range: "now",
  });
  const chapter = await postJson(request, `/api/volumes/${volume.id}/chapters`, {
    title: "Chapter UI",
    pov: "narrator",
    chapter_intent: { text: "主角必须做出选择" },
  });

  await page.goto("/");
  await page.getByTestId(`project-toggle-${project.id}`).click();
  await page.getByTestId(`volume-toggle-${volume.id}`).click();
  await page.getByTestId(`chapter-item-${chapter.id}`).click();

  await expect(page.getByTestId("chapter-workspace")).toBeVisible();
  await expect(page.getByTestId("chapter-canon-summary")).toBeVisible();
  await expect(page.getByTestId("story-bible-panel")).toBeVisible();
  await expect(page.getByTestId("btn-canon-scope-chapter")).toBeVisible();
  await expect(page.getByTestId("chapter-workflow-phase")).toHaveText(/需要章节意图|规划中|计划待决策|已阻断/);
  await expect(page.getByTestId("chapter-intent-input")).toHaveValue("主角必须做出选择");
});

test("章节工作台主旅程：计划接受、场景队列、章节审校与 Canon 来源", async ({ page, request }) => {
  const project = await postJson(request, "/api/projects", {
    name: `journey-${Date.now()}`,
    genre: "drama",
    target_reader: "adult",
    default_style: "plain",
  });
  const volume = await postJson(request, `/api/projects/${project.id}/volumes`, {
    name: "Volume 1",
    goal: "goal",
    mainline: "mainline",
    time_range: "now",
  });
  const chapter = await postJson(request, `/api/volumes/${volume.id}/chapters`, {
    title: "Chapter Journey",
    pov: "narrator",
    chapter_intent: { text: "" },
  });

  await openChapter(page, project.id, volume.id, chapter.id);
  const intent = "主角在暴雨夜发现线索并作出不可逆选择";
  await page.getByTestId("chapter-intent-input").fill(intent);
  await page.getByTestId("btn-start-chapter-planning").click();
  await expect(page.getByTestId("chapter-workflow-phase")).toHaveText("规划中");
  const planningRunId = await activeRunId(request, chapter.id);

  let preAcceptSceneRunPosts = 0;
  page.on("request", (requestEvent) => {
    if (requestEvent.method() === "POST" && /\/api\/scenes\/[^/]+\/runs$/.test(new URL(requestEvent.url()).pathname)) {
      preAcceptSceneRunPosts += 1;
    }
  });
  const candidateRevisionId = fixtureForChapter(chapter.id, "seed-plan-candidate", "--run-id", planningRunId);
  const candidateRevisionIdAgain = fixtureForChapter(chapter.id, "seed-plan-candidate", "--run-id", planningRunId);
  expect(candidateRevisionIdAgain).toBe(candidateRevisionId);
  await expect(page.getByTestId("chapter-workflow-phase")).toHaveText("计划待决策");
  await expect(page.getByTestId("chapter-plan-candidate")).toContainText("观星台");
  await expect(page.getByTestId("chapter-scene-queue")).toContainText("接受计划后");
  await expect(page.getByTestId("chapter-scene-queue").locator("li")).toHaveCount(0);
  await expect(page.getByTestId("btn-review")).toHaveCount(0);
  const beforeAccept = await workflow(request, chapter.id);
  expect(beforeAccept.scenes).toEqual([]);
  expect(preAcceptSceneRunPosts).toBe(0);
  if (await page.getByTestId("btn-review").count()) await page.getByTestId("btn-review").click();
  expect(preAcceptSceneRunPosts).toBe(0);
  await page.getByTestId("btn-accept-chapter-plan").click();
  await expect(page.getByTestId("chapter-scene-queue")).toContainText("第一场");
  await expect(page.getByTestId("chapter-scene-queue")).toContainText("第二场");

  const acceptedPlan = await workflow(request, chapter.id);
  expect(acceptedPlan.plan.accepted_revision_id).toBeTruthy();
  expect(acceptedPlan.scenes).toHaveLength(2);
  const scenes = acceptedPlan.scenes as Array<{ scene_id: string; status: string }>;
  expect(scenes[0].status).toBe("planned");
  expect(scenes[1].status).toBe("planned");

  await openChapter(page, project.id, volume.id, chapter.id);
  await expect(page.getByTestId(`scene-item-${scenes[0].scene_id}`)).toBeVisible();
  let blockedSceneRunPosts = 0;
  page.on("request", (requestEvent) => {
    if (requestEvent.method() === "POST" && /\/api\/scenes\/[^/]+\/runs$/.test(new URL(requestEvent.url()).pathname)) {
      blockedSceneRunPosts += 1;
    }
  });
  await page.getByTestId(`scene-item-${scenes[1].scene_id}`).click();
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("status-message")).toContainText("previous_scene_not_accepted");
  const blockedWorkflow = await workflow(request, chapter.id);
  expect(blockedWorkflow.scenes[1].blocking_reasons).toContain("previous_scene_not_accepted");
  expect(blockedSceneRunPosts).toBe(0);
  await page.getByTestId(`scene-item-${scenes[0].scene_id}`).click();
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  const firstSceneRunId = await sceneRunId(page);

  fixtureForChapter(
    chapter.id,
    "advance",
    "--run-id",
    firstSceneRunId,
    "--to",
    "waiting_feedback",
    "--issues-json",
    "[]",
    "--draft-text",
    "第一场完成后的正文",
  );
  await expect(page.getByTestId("run-status")).toContainText("等待反馈");
  await page.getByTestId("btn-run-accept").click();
  await expect(page.getByTestId("run-status")).toContainText("已接受");

  const afterFirst = await workflow(request, chapter.id);
  expect(afterFirst.scenes[0].status).toBe("accepted");
  expect(afterFirst.scenes[1].blocking_reasons).toEqual([]);

  await page.getByTestId(`scene-item-${scenes[1].scene_id}`).click();
  await page.getByTestId("btn-review").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  const secondSceneRunId = await sceneRunId(page);
  fixtureForChapter(
    chapter.id,
    "advance",
    "--run-id",
    secondSceneRunId,
    "--to",
    "waiting_feedback",
    "--issues-json",
    "[]",
    "--draft-text",
    "第二场完成后的正文",
  );
  await expect(page.getByTestId("run-status")).toContainText("等待反馈");
  await page.getByTestId("btn-run-accept").click();
  await expect(page.getByTestId("run-status")).toContainText("已接受");

  await openChapter(page, project.id, volume.id, chapter.id);
  await expect(page.getByTestId("btn-start-chapter-review")).toBeVisible();
  await page.getByTestId("btn-start-chapter-review").click();
  const reviewRunId = await activeRunId(request, chapter.id);
  const stagedRevisionId = fixtureForChapter(chapter.id, "seed-chapter-review", "--run-id", reviewRunId);
  const stagedRevisionIdAgain = fixtureForChapter(chapter.id, "seed-chapter-review", "--run-id", reviewRunId);
  expect(stagedRevisionIdAgain).toBe(stagedRevisionId);
  await expect(page.getByTestId("chapter-review-issues")).toContainText("结构节奏");
  await expect(page.getByTestId("btn-accept-chapter-revision")).toBeVisible();
  const staged = await workflow(request, chapter.id);
  expect(staged.chapter_revision.staged_revision_id).toBeTruthy();
  await page.getByTestId("btn-accept-chapter-revision").click();
  await expect(page.getByTestId("chapter-workflow-phase")).toHaveText("已完成");
  const accepted = await workflow(request, chapter.id);
  expect(accepted.chapter_revision.accepted_revision_id).toBe(accepted.chapter_revision.history[0].id);

  let canon: {
    target_type?: string;
    target_id?: string;
    source_revision_id?: string;
    run_id?: string;
    run_status?: string;
    items?: Array<{ id?: string; scope?: string; scene_id?: string | null }>;
  } = {};
  canon = await (await request.get(`/api/chapters/${chapter.id}/canon-candidates`)).json();
  if (!canon.run_id) await page.getByTestId("btn-canon-start").click();
  await expect
    .poll(async () => {
      canon = await (await request.get(`/api/chapters/${chapter.id}/canon-candidates`)).json();
      return canon.run_id ?? "";
    }, { timeout: 15_000 })
    .toMatch(/[0-9a-f-]{20,}/);
  const canonRunId = canon.run_id!;
  expect(canon.target_type).toBe("chapter");
  expect(canon.target_id).toBe(chapter.id);
  expect(canon.source_revision_id).toBe(accepted.chapter_revision.accepted_revision_id);
  expect(canon.run_status).toMatch(/queued|running|waiting_feedback/);
  let canonSeedCandidateId = canon.items?.[0]?.id ?? "";
  if (!canonSeedCandidateId) {
    canonSeedCandidateId = fixtureForChapter(chapter.id, "seed-canon-candidates", "--run-id", canonRunId);
  }
  const canonSeedCandidateIdAgain = fixtureForChapter(chapter.id, "seed-canon-candidates", "--run-id", canonRunId);
  expect(canonSeedCandidateIdAgain).toBe(canonSeedCandidateId);
  await page.getByTestId("btn-canon-refresh").click();
  await expect(page.locator("[data-testid^='canon-candidate-']")).toHaveCount(3);
  const canonAfterSeed = await (await request.get(`/api/chapters/${chapter.id}/canon-candidates`)).json();
  expect(canonAfterSeed.source_revision_id).toBe(accepted.chapter_revision.accepted_revision_id);
  expect(canonAfterSeed.run_id).toBe(canonRunId);
  expect(canonAfterSeed.items).toHaveLength(3);
  for (const item of canonAfterSeed.items) {
    expect(item.scope).toBe("chapter");
    expect(item.scene_id).toBeNull();
  }
  const candidates = page.locator("[data-testid^='canon-candidate-']");
  const first = await candidates.nth(0).getAttribute("data-candidate-id");
  const second = await candidates.nth(1).getAttribute("data-candidate-id");
  const third = await candidates.nth(2).getAttribute("data-candidate-id");
  await page.getByTestId(`btn-decide-confirm-${first}`).click();
  await page.getByTestId(`btn-decide-reject-${second}`).click();
  await page.getByTestId(`btn-decide-defer-${third}`).click();
  await page.getByTestId("btn-canon-submit").click();
  await expect(page.locator(".canon-status-accepted")).toHaveCount(1);
  await expect(page.locator(".canon-status-rejected")).toHaveCount(1);
  await expect(page.locator(".canon-status-deferred")).toHaveCount(1);
  await expect(page.getByTestId("canon-official")).toContainText("林默");
});
