import { expect, test } from "@playwright/test";

test("Planner 问题与提案反馈通过结构化 feedback 提交", async ({ page, request }) => {
  const project = await (await request.post("/api/projects", {
    data: { name: `planner-feedback-${Date.now()}`, genre: "drama", target_reader: "adult", default_style: "plain" },
    headers: { "Idempotency-Key": `planner-project-${Date.now()}` },
  })).json();
  const volume = await (await request.post(`/api/projects/${project.id}/volumes`, {
    data: { name: "Volume 1", goal: "goal", mainline: "mainline", time_range: "now" },
    headers: { "Idempotency-Key": `planner-volume-${Date.now()}` },
  })).json();
  const chapter = await (await request.post(`/api/volumes/${volume.id}/chapters`, {
    data: { title: "Planner feedback", pov: "narrator", chapter_intent: { text: "intent" } },
    headers: { "Idempotency-Key": `planner-chapter-${Date.now()}` },
  })).json();

  const workflow = {
    chapter_id: chapter.id,
    phase: "plan_feedback",
    chapter_status: "planning",
    pending_decision: { target: "plan", kind: "answer_planner", run_id: "planner-run-1234567890", expected_run_version: 3 },
    intent: { text: "intent", optional_fields: {}, unresolved_questions: [] },
    plan_discussion: {
      messages: [],
      pending_questions: [{ question_id: "question-1", text: "主角为何现在离开？", impact: "剧情动机" }],
      pending_proposals: [{ proposal_id: "proposal-1", field_path: "tone", value: "克制", source: "ai", status: "pending", rationale: "保持悬念" }],
    },
    plan: {
      candidate_revision_id: null,
      accepted_revision_id: null,
      candidate_version: null,
      accepted_version: null,
      status: "none",
      contract: null,
      contract_field_provenance: {},
      scene_briefs: [],
    },
    scenes: [],
    chapter_revision: {
      staged_revision_id: "chapter-revision-staged",
      accepted_revision_id: "chapter-revision-accepted",
      review_run_id: null,
      review_issues: [],
      review_summary: {},
      history: [
        {
          id: "chapter-revision-accepted",
          parent_revision_id: null,
          status: "accepted",
          reason: "fixture",
          created_at: "2026-08-07T00:00:00Z",
          scene_versions: [{ scene_id: "scene-1", scene_revision_id: "scene-rev-1", sort_order: 0 }],
          review_issues: [],
          review_summary: {},
          is_current_accepted: true,
        },
      ],
    },
    active_run: null,
    affected_scene_ids: ["scene-1"],
    stale_scene_ids: ["scene-stale-1"],
    blocking_reasons: [],
    canon_run_id: null,
    canon: {},
  };

  await page.route(`/api/chapters/${chapter.id}/workflow`, async (route) => route.fulfill({ json: workflow }));
  await page.route(`/api/projects/${project.id}/canon`, async (route) => route.fulfill({ json: { project_id: project.id, facts: [], timeline_events: [], plot_threads: [] } }));
  await page.route(`/api/chapters/${chapter.id}/canon-candidates`, async (route) => route.fulfill({ json: { target_type: "chapter", target_id: chapter.id, source_revision_id: null, run_id: null, run_status: null, items: [] } }));

  let decisionBody: Record<string, unknown> | null = null;
  await page.route(`/api/runs/${workflow.pending_decision.run_id}/decisions`, async (route) => {
    decisionBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { run: {}, decision_id: "decision-1", command_id: "command-1" } });
  });

  await page.goto("/");
  await page.getByTestId(`project-toggle-${project.id}`).click();
  await page.getByTestId(`volume-toggle-${volume.id}`).click();
  await page.getByTestId(`chapter-item-${chapter.id}`).click();

  await expect(page.getByTestId("chapter-revision-chapter-revision-accepted")).toBeVisible();
  await expect(page.getByTestId("chapter-affected-scenes")).toContainText("scene-1");
  await expect(page.getByTestId("chapter-stale-scenes")).toContainText("scene-stale-1");

  await expect(page.getByTestId("planner-question-question-1")).toBeVisible();
  await expect(page.getByTestId("planner-proposal-proposal-1")).toBeVisible();
  await page.getByTestId("planner-question-answer-question-1").fill("他收到旧友求救信。");
  await page.getByTestId("planner-proposal-action-proposal-1-modify").click();
  await page.getByTestId("planner-proposal-value-proposal-1").fill("冷峻");
  await page.getByTestId("planner-feedback-input").fill("请据此重新规划");
  await page.getByTestId("btn-plan-feedback").click();

  await expect.poll(() => decisionBody).toMatchObject({
    target: "plan",
    decision: "feedback",
    feedback: {
      text: "请据此重新规划",
      answers: [{ question_id: "question-1", text: "他收到旧友求救信。" }],
      proposals: [{ proposal_id: "proposal-1", action: "modify", value: "冷峻" }],
    },
  });
});
