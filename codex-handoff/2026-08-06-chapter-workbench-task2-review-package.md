# Task 2 review package

## Implementer report

# 章节工作台任务 2 报告

## 状态

DONE_WITH_CONCERNS

## 改动文件

- `backend/app/runtime/run_worker.py`
  - `new_chapter` Worker 从持久化章节意图、规划讨论、待回答问题和待确认建议重建 Planner 输入。
  - Planner 结构化输出在运行事务内调用既有 `persist_planner_output`，候选始终保持 `pending`。
  - 消费 `chapter_plan.accepted` outbox，按 `chapter_plan_scene_links.sort_order` 选择第一个未完成场景并创建 queued scene run。
  - outbox 重放、Worker 重启和已有运行均按 `(chapter_id, scene_id, plan_revision_id)` 查重；首个场景未接受前不推进后续场景。
- `backend/app/agents/chapter_graph.py`
  - 仅把 Planner 结构化输出返回给 Worker 事务持久化；场景 Agent、章节审校和 Canon 不接收规划专属字段。
- `backend/tests/runtime/test_chapter_workflow_worker_task2.py`
  - 新增 Worker 上下文重建、候选持久化、accepted outbox 消费、顺序恢复和重复重放测试。
- `frontend/playwright.config.ts`
  - 增加 `chromium` project，并让 webServer 同时拉起 API、前端和 Worker，共用 E2E 数据库及 actor 配置。
- `frontend/tests/chapter-workflow.spec.ts`
  - 新增最小 Playwright 主流程：创建非空章节意图、启动 `new_chapter` 规划运行并读取 workflow。

任务 1 已提供的五张规划表及 Alembic migration 保持不变，本任务未新增第二套模型协议，也未删除 `POST /api/chapters/{chapter_id}/plan`。

## 实际测试命令与结果

- `backend/.venv/Scripts/python.exe -m pytest -q backend/tests/runtime/test_chapter_workflow_worker_task2.py`：`2 passed`。
- `backend/.venv/Scripts/python.exe -m pytest -q backend/tests/runtime/test_run_worker.py backend/tests/runtime/test_real_chapter_worker_chain.py`：`8 passed, 1 skipped`。
- `backend/.venv/Scripts/python.exe -m ruff check backend/app/runtime/run_worker.py backend/app/agents/chapter_graph.py backend/tests/runtime/test_chapter_workflow_worker_task2.py`：`All checks passed`。
- `backend/.venv/Scripts/python.exe -m compileall -q backend/app`：通过。
- `git diff --check`：通过（仅有既有换行符提示）。
- `frontend npm run typecheck`：通过。
- `frontend npx playwright test tests/chapter-workflow.spec.ts --project=chromium`：阻塞，当前受限环境返回 `spawn EPERM`，未能启动浏览器/webServer。

## 已知风险

- 本地未运行 Playwright：任务 brief 要求同时启动 API、前端和 Worker，但当前环境未确认 PostgreSQL E2E 服务及浏览器依赖，因此未声称完整 E2E 已覆盖。
- Worker 当前按 `GenerationRun.status == "accepted"` 判断场景是否完成；后续若引入新的场景完成终态，需要同步队列推进条件。
- outbox consumer 将业务记录标记为 `consumed`，现有 publisher 仍可独立使用 `pending/publishing/published` 状态；生产部署需确保 consumer 与 publisher 的轮询职责一致。

## 未完成事项

- Playwright 测试和 Worker webServer wiring 已增加，但本地浏览器进程启动被 `spawn EPERM` 阻塞；因此未覆盖真实 API+前端+Worker 集成边界。
- 未提交 Git commit，按任务要求保留工作区改动供主任务整合。

## Diff: run_worker.py

diff --git a/backend/app/runtime/run_worker.py b/backend/app/runtime/run_worker.py
index 969598c..3da6bf5 100644
--- a/backend/app/runtime/run_worker.py
+++ b/backend/app/runtime/run_worker.py
@@ -39,11 +39,18 @@ from app.agents.schemas import (
 from app.agents.state import ChapterRunState
 from app.agents.writing_agent import WritingAgent
 from app.db.models import (
+    Chapter,
+    ChapterPlanDiscussionMessage,
+    ChapterPlanProposal,
+    ChapterPlanQuestion,
     ChapterPlanRevision,
     ChapterPlanRevisionLink,
+    ChapterPlanSceneLink,
     GenerationRun,
+    RunOutboxRecord,
     Scene,
     SceneRevision,
+    Volume,
 )
 from app.errors import AppError
 from app.observability.wiring import ObservabilityWiring
@@ -51,6 +58,7 @@ from app.runtime.executor import RunExecutor
 from app.runtime.leases import LeaseRepository
 from app.runtime.run_events import PostgresRunEventStore
 from app.runtime.run_identity import RunIdentity
+from app.services.generation_runs import persist_planner_output
 
 # 图构造器：给定运行与会话返回可执行图（默认按运行类型选择三图之一）。
 GraphBuilder = Callable[[GenerationRun, Session], Any]
@@ -94,6 +102,8 @@ class RunWorker:
         返回：本次实际处理并提交的运行数量；无 queued 运行时返回 0。
         约束：并发 worker 通过行锁领取，同一运行只被一个 worker 处理一次。
         """
+        # 先恢复 accepted plan 产生的场景队列；tick 返回值仍表示实际执行的运行数。
+        self._consume_plan_outbox()
         processed = 0
         while True:
             run_id = self._peek_queued()
@@ -106,6 +116,91 @@ class RunWorker:
             processed += 1
         return processed
 
+    def _consume_plan_outbox(self) -> int:
+        """消费 accepted plan 事件并恢复第一个未完成场景。
+
+        outbox 记录本身是可重放的；场景运行通过固定 `(plan_revision_id, scene_id)`
+        查重，因此 Worker 重启或重复投递不会创建第二个场景运行。
+        """
+        consumed = 0
+        with self._factory() as session:
+            rows = session.execute(
+                select(RunOutboxRecord)
+                .where(
+                    RunOutboxRecord.resource_type == "chapter_plan",
+                    RunOutboxRecord.delivery_status.in_(("pending", "publishing", "published")),
+                )
+                .order_by(RunOutboxRecord.created_at)
+                .with_for_update(skip_locked=True)
+            ).scalars().all()
+            for record in rows:
+                payload = record.payload or {}
+                if payload.get("event_type") != "chapter_plan.accepted":
+                    continue
+                chapter_id = payload.get("chapter_id")
+                plan_revision_id = payload.get("plan_revision_id")
+                if not chapter_id or not plan_revision_id:
+                    record.delivery_status = "failed"
+                    record.last_error = "invalid chapter_plan.accepted payload"
+                    continue
+                link = session.execute(
+                    select(ChapterPlanSceneLink)
+                    .where(ChapterPlanSceneLink.plan_revision_id == plan_revision_id)
+                    .order_by(ChapterPlanSceneLink.sort_order)
+                ).scalars().all()
+                for scene_link in link:
+                    existing_run = session.execute(
+                        select(GenerationRun)
+                        .where(
+                            GenerationRun.chapter_id == chapter_id,
+                            GenerationRun.scene_id == scene_link.scene_id,
+                            GenerationRun.plan_revision_id == plan_revision_id,
+                        )
+                        .limit(1)
+                    ).scalar_one_or_none()
+                    if existing_run is not None:
+                        # 只有已接受的场景才推进队列；暂停/运行中的首场景阻止
+                        # 后续场景物化，避免 outbox 重放制造并行场景运行。
+                        if existing_run.status != "accepted":
+                            break
+                        continue
+                    chapter = session.get(Chapter, chapter_id)
+                    if chapter is None:
+                        record.delivery_status = "failed"
+                        record.last_error = "chapter not found"
+                        break
+                    volume = session.get(Volume, chapter.volume_id)
+                    run = GenerationRun(
+                        project_id=volume.project_id if volume is not None else chapter.volume_id,
+                        chapter_id=chapter_id,
+                        scene_id=scene_link.scene_id,
+                        plan_revision_id=plan_revision_id,
+                        request_type="continue",
+                        decision_target="scene",
+                        status="queued",
+                        normalized_input={
+                            "run_scope": "scene",
+                            "request_type": "continue",
+                            "decision_target": "scene",
+                            "plan_revision_id": plan_revision_id,
+                            "chapter_intent": chapter.chapter_intent or {},
+                        },
+                    )
+                    session.add(run)
+                    session.flush()
+                    PostgresRunEventStore(session).emit(
+                        run.id,
+                        "run_queued",
+                        {"run_scope": "scene", "request_type": "continue", "plan_revision_id": plan_revision_id},
+                        fencing_token=0,
+                        producer_command_id=record.producer_command_id,
+                    )
+                    break
+                record.delivery_status = "consumed"
+                consumed += 1
+            session.commit()
+        return consumed
+
     def run_forever(self, interval: float = 1.0) -> None:
         """持续轮询执行（Worker 进程主循环）。
 
@@ -161,6 +256,11 @@ class RunWorker:
                 state,
                 envelope,
             )
+            planner_output = result.get("planner_output")
+            if planner_output is not None and run.chapter_id and run.decision_target == "plan":
+                from app.agents.schemas import ChapterPlanOutput
+
+                persist_planner_output(session, run.id, ChapterPlanOutput(**planner_output), actor_id=self._actor_id)
             self._persist_outcome(session, run, result)
             session.commit()
         except Exception as exc:
@@ -292,6 +392,14 @@ class RunWorker:
         绝不重新读取客户端输入。
         """
         ni = run.normalized_input or {}
+        chapter_intent = ni.get("chapter_intent") or {}
+        if run.chapter_id:
+            chapter = session.get(Chapter, run.chapter_id)
+            if chapter is not None:
+                persisted_intent = chapter.chapter_intent or {}
+                # 旧初始化数据可能只有占位键；只有包含自然语言 text 才作为 Planner 意图。
+                if not chapter_intent.get("text") and persisted_intent.get("text"):
+                    chapter_intent = persisted_intent
         scene_brief: dict = {}
         accepted_text = ""
         accepted_scene_revision_id: str | None = None
@@ -312,6 +420,35 @@ class RunWorker:
                 accepted_chapter_revision_id = run.canon_source_revision_id
         base_scene_revision_id = ni.get("base_scene_revision_id")
         chapter_contract = self._chapter_contract_for(session, run)
+        lineage = self._planning_lineage_for(session, run)
+        discussion: list[dict] = []
+        questions: list[dict] = []
+        proposals: list[dict] = []
+        if run.chapter_id and lineage:
+            discussion = [
+                {"role": row.role, "kind": row.kind, "text": row.text, "source_run_id": row.source_run_id}
+                for row in session.execute(
+                    select(ChapterPlanDiscussionMessage)
+                    .where(ChapterPlanDiscussionMessage.planning_lineage_id == lineage)
+                    .order_by(ChapterPlanDiscussionMessage.message_sequence)
+                ).scalars()
+            ]
+            questions = [
+                {"question_id": row.question_id, "text": row.text, "impact": row.impact, "status": row.status}
+                for row in session.execute(
+                    select(ChapterPlanQuestion)
+                    .where(ChapterPlanQuestion.planning_lineage_id == lineage, ChapterPlanQuestion.status == "pending")
+                    .order_by(ChapterPlanQuestion.created_at)
+                ).scalars()
+            ]
+            proposals = [
+                {"proposal_id": row.proposal_id, "field_path": row.field_path, "value": row.value, "source": row.source, "status": row.status}
+                for row in session.execute(
+                    select(ChapterPlanProposal)
+                    .where(ChapterPlanProposal.planning_lineage_id == lineage, ChapterPlanProposal.status == "pending")
+                    .order_by(ChapterPlanProposal.created_at)
+                ).scalars()
+            ]
         manifest = [
             ContextManifestEntry(source_id=rid, kind="revision", revision_id=rid)
             for rid in dict.fromkeys([base_scene_revision_id, accepted_scene_revision_id])
@@ -345,6 +482,10 @@ class RunWorker:
             accepted_scene_revision_id=accepted_scene_revision_id,
             accepted_chapter_revision_id=accepted_chapter_revision_id,
             chapter_contract=chapter_contract,
+            chapter_intent=chapter_intent,
+            plan_discussion=discussion,
+            pending_plan_questions=questions,
+            pending_plan_proposals=proposals,
             canon_scope=(
                 ("scene" if run.scene_id else "chapter")
                 if run.decision_target == "canon"
@@ -360,6 +501,23 @@ class RunWorker:
             write_fence_fencing_token=lease["fencing_token"],
         )
 
+    def _planning_lineage_for(self, session: Session, run: GenerationRun) -> str | None:
+        """从持久化计划血缘恢复 Planner 讨论上下文。"""
+        value = (run.normalized_input or {}).get("planning_lineage_id")
+        if value:
+            return str(value)
+        if run.parent_plan_revision_id:
+            parent = session.get(ChapterPlanRevision, run.parent_plan_revision_id)
+            if parent is not None and parent.planning_lineage_id:
+                return parent.planning_lineage_id
+            return run.parent_plan_revision_id
+        candidate = session.execute(
+            select(ChapterPlanRevision)
+            .where(ChapterPlanRevision.source_run_id == run.id)
+            .limit(1)
+        ).scalar_one_or_none()
+        return candidate.planning_lineage_id if candidate else run.id
+
     def _chapter_contract_for(self, session: Session, run: GenerationRun) -> dict:
         """由已接受章节计划修订源取章节契约（供 ChapterPlanner/Review 使用）。
 

## Diff: chapter_graph.py

diff --git a/backend/app/agents/chapter_graph.py b/backend/app/agents/chapter_graph.py
index a6485ab..20b5fd1 100644
--- a/backend/app/agents/chapter_graph.py
+++ b/backend/app/agents/chapter_graph.py
@@ -141,6 +141,9 @@ class ChapterGraph:
             if feedback:
                 envelope = envelope.model_copy(update={"author_feedback": feedback})
             result = callable_(state, envelope)
+            # Planner 候选只在 Worker 的持久化事务中提交；把结构化结果返回给 Worker，
+            # 不把正文或讨论写入观测 sink，也不把候选当作 accepted 版本。
+            planner_output = result.get("output") if agent_type == _PLAN else None
             state["last_durable_node"] = result.get("last_durable_node") or agent_type
             outcome = result.get("outcome")
             if outcome is not None and outcome.status in (
@@ -152,16 +155,22 @@ class ChapterGraph:
                 state["pending_node"] = outcome.pending_node
                 state["clarification_questions"] = outcome.clarification_questions
                 state["run_status"] = "paused"
-                return {
+                response = {
                     "pending_node": outcome.pending_node,
                     "clarification_questions": outcome.clarification_questions,
                     "run_status": "paused",
                     "last_durable_node": agent_type,
                 }
-            return {
+                if planner_output is not None:
+                    response["planner_output"] = planner_output.model_dump()
+                return response
+            response = {
                 "last_durable_node": agent_type,
                 "run_status": "running",
             }
+            if planner_output is not None:
+                response["planner_output"] = planner_output.model_dump()
+            return response
 
         return run
 

## New test: test_chapter_workflow_worker_task2.py

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.hook_registry import HookRegistry
from app.agents.schemas import ChapterPlanOutput
from app.db.models import (
    ChapterPlanDiscussionMessage,
    ChapterPlanProposal,
    ChapterPlanQuestion,
    ChapterPlanRevision,
    ChapterPlanSceneLink,
    GenerationRun,
    RunOutboxRecord,
)
from app.domain.chapters import (
    accept_chapter_plan_revision,
    create_chapter,
    create_chapter_plan_revision,
)
from app.domain.resources import create_project, create_volume
from app.runtime.run_events import PostgresRunEventStore
from app.runtime.run_worker import RunWorker


class _PlannerGraph:
    def __init__(self) -> None:
        self.envelope = None
        self.calls = 0

    @property
    def registry(self):
        return HookRegistry()

    def invoke(self, state, envelope, thread_id, resume=None):
        self.calls += 1
        self.envelope = envelope
        return {
            "run_status": "paused",
            "pending_node": "chapter_planner",
            "clarification_questions": [],
            "last_durable_node": "chapter_planner",
            "planner_output": ChapterPlanOutput(
                status="ready",
                chapter_contract={"scene_keys": ["s1"]},
                scene_contracts=[{"client_key": "s1", "title": "S1", "scene_brief": {}}],
                reason="candidate",
            ).model_dump(),
        }


def _chapter(db):
    project = create_project(db, "P", "g", "r", "s", {"actor_id": "a", "idempotency_key": "p-task2"})
    volume = create_volume(db, project.id, "V", "g", "m", "r", {"actor_id": "a", "idempotency_key": "v-task2"})
    return create_chapter(
        db,
        volume.id,
        "C",
        "pov",
        {"text": "主角必须在钟楼做出选择", "goal": "choice"},
        {"actor_id": "a", "idempotency_key": "c-task2"},
    )


def test_worker_rebuilds_planner_context_and_persists_candidate(db):
    chapter = _chapter(db)
    lineage = "lineage-task2"
    db.add(
        ChapterPlanDiscussionMessage(
            chapter_id=chapter.id,
            planning_lineage_id=lineage,
            message_sequence=1,
            role="author",
            kind="intent",
            text="不要安排死亡结局",
        )
    )
    db.add(
        ChapterPlanQuestion(
            chapter_id=chapter.id,
            planning_lineage_id=lineage,
            text="是否保留钟楼？",
            impact="scene",
            status="pending",
        )
    )
    db.add(
        ChapterPlanProposal(
            chapter_id=chapter.id,
            planning_lineage_id=lineage,
            field_path="tone",
            value={"value": "紧张"},
            source="ai",
            status="pending",
        )
    )
    run = GenerationRun(
        id="run-task2-planner",
        project_id=chapter.volume_id,
        chapter_id=chapter.id,
        request_type="new_chapter",
        decision_target="plan",
        status="queued",
        normalized_input={"run_scope": "chapter", "request_type": "new_chapter", "decision_target": "plan", "chapter_intent": {"text": "输入意图"}},
        parent_plan_revision_id=lineage,
    )
    db.add(run)
    db.flush()
    PostgresRunEventStore(db).emit(run.id, "run_queued", {}, fencing_token=0)
    db.commit()

    graph = _PlannerGraph()
    worker = RunWorker(sessionmaker(bind=db.bind, expire_on_commit=False), actor_id="worker-task2", graph_builder=lambda run, session: graph)
    assert worker.tick() == 1

    assert graph.envelope.chapter_intent == {"text": "输入意图"}
    assert graph.envelope.plan_discussion[0]["text"] == "不要安排死亡结局"
    assert graph.envelope.pending_plan_questions[0]["text"] == "是否保留钟楼？"
    assert graph.envelope.pending_plan_proposals[0]["field_path"] == "tone"
    candidate = db.execute(select(ChapterPlanRevision).where(ChapterPlanRevision.source_run_id == run.id)).scalar_one()
    assert candidate.status == "pending"


def test_worker_consumes_accepted_plan_outbox_and_recovers_one_scene_run(db):
    chapter = _chapter(db)
    plan = create_chapter_plan_revision(
        db,
        chapter.id,
        None,
        {"scene_keys": ["s1", "s2"]},
        "fixture",
        {"actor_id": "a", "idempotency_key": "plan-task2"},
    )
    plan.scene_briefs = [
        {"client_key": "s1", "title": "S1", "scene_brief": {}},
        {"client_key": "s2", "title": "S2", "scene_brief": {}},
    ]
    plan.chapter_contract = {"scene_keys": ["s1", "s2"], "scenes": plan.scene_briefs}
    accept_chapter_plan_revision(db, chapter.id, plan.id, None, 1, {"actor_id": "a", "idempotency_key": "accept-task2"})
    db.commit()

    factory = sessionmaker(bind=db.bind, expire_on_commit=False)
    worker = RunWorker(factory, actor_id="worker-task2")
    assert worker.tick() == 1
    runs = db.execute(select(GenerationRun).where(GenerationRun.chapter_id == chapter.id, GenerationRun.scene_id.is_not(None))).scalars().all()
    assert len(runs) == 1
    assert runs[0].plan_revision_id == plan.id
    first_scene = db.execute(select(ChapterPlanSceneLink).where(ChapterPlanSceneLink.plan_revision_id == plan.id).order_by(ChapterPlanSceneLink.sort_order)).scalars().first()
    assert runs[0].scene_id == first_scene.scene_id

    assert worker.tick() == 0
    runs = db.execute(select(GenerationRun).where(GenerationRun.chapter_id == chapter.id, GenerationRun.scene_id.is_not(None))).scalars().all()
    assert len(runs) == 1

    # 模拟 outbox 重放与 Worker 重启：重复消费仍只保留同一个场景运行。
    outbox = db.execute(select(RunOutboxRecord).where(RunOutboxRecord.resource_id == plan.id)).scalar_one()
    outbox.delivery_status = "pending"
    db.commit()
    RunWorker(factory, actor_id="worker-task2-restarted").tick()
    runs = db.execute(select(GenerationRun).where(GenerationRun.chapter_id == chapter.id, GenerationRun.scene_id.is_not(None))).scalars().all()
    assert len(runs) == 1

## New test: frontend/tests/chapter-workflow.spec.ts

import { expect, test, type APIRequestContext } from "@playwright/test";

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

test("new_chapter 规划读取意图并刷新 workflow", async ({ request }) => {
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
    chapter_intent: { text: "主角必须在钟楼做出不可逆的选择" },
  });

  const run = await postJson(request, `/api/chapters/${chapter.id}/runs`, {
    run_scope: "chapter",
    request_type: "new_chapter",
    decision_target: "plan",
    chapter_intent: { text: "主角必须在钟楼做出不可逆的选择" },
  });
  expect(run.status).toBe("queued");

  await expect
    .poll(async () => (await (await request.get(`/api/chapters/${chapter.id}/workflow`)).json()).phase, {
      timeout: 30_000,
    })
    .toMatch(/planning|plan_feedback|blocked/);

  const workflow = await (await request.get(`/api/chapters/${chapter.id}/workflow`)).json();
  expect(workflow.intent.text).toContain("不可逆");
  expect(workflow.active_run?.run_id).toBe(run.run_id);
  expect(workflow.plan_discussion).toBeDefined();
});

## Diff: frontend/playwright.config.ts

diff --git a/frontend/playwright.config.ts b/frontend/playwright.config.ts
index 43f2916..677607f 100644
--- a/frontend/playwright.config.ts
+++ b/frontend/playwright.config.ts
@@ -18,6 +18,7 @@ export default defineConfig({
   workers: 1,
   retries: 0,
   reporter: [["list"]],
+  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
   use: {
     baseURL: "http://127.0.0.1:3000",
     trace: "retain-on-failure",
@@ -39,6 +40,22 @@ export default defineConfig({
         APP_ENV: "development",
       },
     },
+    {
+      // Worker 与 API 共用 E2E_DATABASE_URL；以 API ready 地址作为进程就绪探针。
+      command: ".venv\\Scripts\\python.exe -m app.worker_main",
+      cwd: "./../backend",
+      url: "http://127.0.0.1:8000/ready",
+      reuseExistingServer: true,
+      timeout: 60_000,
+      env: {
+        DATABASE_URL: "postgresql+psycopg://postgres:postgres@localhost:5432/novel_e2e",
+        ACTOR_ID: "e2e-worker",
+        DEPLOYMENT_MODE: "single_user_private",
+        API_BIND_SCOPE: "loopback",
+        INTERNAL_API_BASE_URL: "http://127.0.0.1:8000",
+        APP_ENV: "development",
+      },
+    },
     {
       command: "npm run dev -- --port 3000",
       cwd: ".",
