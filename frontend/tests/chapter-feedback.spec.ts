import { expect, test } from "@playwright/test";

test("chapter feedback submits revision id and accepted base", async ({ page, request }) => {
  const projectResponse = await request.post("/api/projects", {
    data: { name: `chapter-feedback-${Date.now()}`, genre: "drama", target_reader: "adult", default_style: "plain" },
    headers: { "Idempotency-Key": `chapter-project-${Date.now()}` },
  });
  const project = await projectResponse.json();
  const volumeResponse = await request.post(`/api/projects/${project.id}/volumes`, {
    data: { name: "Volume 1", goal: "goal", mainline: "mainline", time_range: "now" },
    headers: { "Idempotency-Key": `chapter-volume-${Date.now()}` },
  });
  const volume = await volumeResponse.json();
  const chapterResponse = await request.post(`/api/volumes/${volume.id}/chapters`, {
    data: { title: "Chapter feedback", pov: "narrator", chapter_intent: { text: "intent" } },
    headers: { "Idempotency-Key": `chapter-chapter-${Date.now()}` },
  });
  const chapter = await chapterResponse.json();

  let rollbackRequested = false;
  const workflow = {
    chapter_id: chapter.id,
    phase: "chapter_feedback",
    chapter_status: "reviewing",
    pending_decision: { target: "chapter", kind: "chapter_feedback", run_id: "chapter-run-123", expected_run_version: 8 },
    intent: { text: "intent", optional_fields: {}, unresolved_questions: [] },
    plan_discussion: { messages: [], pending_questions: [], pending_proposals: [] },
    plan: {
      candidate_revision_id: null,
      accepted_revision_id: "plan-accepted-1",
      candidate_version: null,
      accepted_version: 1,
      status: "accepted",
      contract: null,
      contract_field_provenance: {},
      scene_briefs: [],
    },
    scenes: [],
    chapter_revision: {
      staged_revision_id: "chapter-staged-2",
      accepted_revision_id: "chapter-accepted-1",
      review_run_id: null,
      review_issues: [],
      review_summary: {},
      history: [
        {
          id: "chapter-accepted-1",
          parent_revision_id: null,
          status: "accepted",
          reason: "fixture",
          created_at: "2026-08-07T00:00:00Z",
          scene_versions: [],
          review_issues: [],
          review_summary: {},
          is_current_accepted: true,
        },
        {
          id: "chapter-history-older",
          parent_revision_id: null,
          status: "accepted",
          reason: "older",
          created_at: "2026-08-06T00:00:00Z",
          scene_versions: [],
          review_issues: [],
          review_summary: {},
          is_current_accepted: false,
        },
      ],
    },
    active_run: null,
    affected_scene_ids: ["scene-a"],
    stale_scene_ids: ["scene-z"],
    blocking_reasons: [],
    canon_run_id: null,
    canon: { run_id: null, status: null, source_revision_id: null, pending_candidate_count: 0 },
  };

  await page.route(`/api/chapters/${chapter.id}/workflow`, async (route) => {
    const body = rollbackRequested
      ? {
          ...workflow,
          chapter_revision: {
            ...workflow.chapter_revision,
            history: [
              ...workflow.chapter_revision.history,
              {
                id: "chapter-rollback-staged",
                parent_revision_id: "chapter-history-older",
                status: "staged",
                reason: "rollback",
                created_at: "2026-08-07T00:00:00Z",
                scene_versions: [],
                review_issues: [],
                review_summary: {},
                is_current_accepted: false,
              },
            ],
          },
        }
      : workflow;
    await route.fulfill({ json: body });
  });
  await page.route(`/api/projects/${project.id}/canon`, async (route) => route.fulfill({ json: { project_id: project.id, facts: [], timeline_events: [], plot_threads: [] } }));
  await page.route(`/api/chapters/${chapter.id}/canon-candidates`, async (route) => route.fulfill({ json: { target_type: "chapter", target_id: chapter.id, source_revision_id: null, run_id: null, run_status: null, items: [] } }));

  let decisionBody: Record<string, unknown> | null = null;
  await page.route(`/api/runs/${workflow.pending_decision.run_id}/decisions`, async (route) => {
    decisionBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { run: {}, decision_id: "decision-2", command_id: "command-2" } });
  });
  await page.route(`/api/chapters/${chapter.id}/rollback`, async (route) => {
    rollbackRequested = true;
    await route.fulfill({ json: { id: "chapter-rollback-staged", parent_revision_id: "chapter-history-older" } });
  });

  await page.goto("/");
  await page.getByTestId(`project-toggle-${project.id}`).click();
  await page.getByTestId(`volume-toggle-${volume.id}`).click();
  await page.getByTestId(`chapter-item-${chapter.id}`).click();

  await expect(page.getByTestId("chapter-revision-chapter-accepted-1")).toBeVisible();
  await expect(page.getByTestId("chapter-revision-chapter-history-older")).toBeVisible();
  await page.getByTestId("btn-rollback-chapter-chapter-history-older").click();
  await expect(page.getByTestId("chapter-revision-chapter-rollback-staged")).toBeVisible();
  await expect(page.getByTestId("chapter-revision-chapter-accepted-1")).toBeVisible();

  await page.getByTestId("chapter-review-feedback-input").fill("chapter feedback text");
  await page.getByTestId("btn-chapter-review-feedback").click();
  await expect.poll(() => decisionBody).toMatchObject({
    target: "chapter",
    decision: "feedback",
    chapter_revision_id: "chapter-staged-2",
    base_chapter_revision_id: "chapter-accepted-1",
  });
});
