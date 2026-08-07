# Task 3: Chapter Workbench Frontend

## Goal

把现有前端从“旧的章节计划初始化按钮”迁移为章节工作台入口，接入后端 `ChapterWorkflowRead` 权威视图。用户可以在章节上下文输入非空自然语言意图、启动 `new_chapter` / `decision_target=plan` 规划运行、看到规划讨论和候选 `SceneBrief[]`，并在计划候选等待时提交反馈或接受计划。页面必须保留既有场景编辑、运行面板、版本和 Story Bible 能力。

## Required behavior

1. 新增 `ChapterWorkflowRead` 及其嵌套数据的前端类型，字段与 `backend/app/api/schemas.py` 和 `backend/app/domain/chapters.py` 一致。
2. API client 新增：
   - `getChapterWorkflow(chapterId)` -> `GET /api/chapters/{chapterId}/workflow`。
   - `createChapterPlanningRun(chapterId, chapterIntent, key)` -> `POST /api/chapters/{chapterId}/runs`，body 必须包含 `run_scope=chapter`、`request_type=new_chapter`、`decision_target=plan` 和非空 `chapter_intent.text`。
   - 章节计划决策请求必须使用通用 `POST /api/runs/{run_id}/decisions`，携带 `target=plan`、服务端的 candidate revision id、`expected_run_version`，重复动作复用同一 idempotency key。
3. 章节工作台以 `ChapterWorkflowRead` 为权威状态来源，不根据多个“最新记录”自行推断 phase。至少展示：
   - `intent_required` / 无意图提示；
   - `planning`、`plan_feedback`、`pending_clarification`、`waiting_feedback` 等等待状态；
   - `blocked` 和 `blocking_reasons`；
   - `plan_discussion.messages`、`pending_questions`、`pending_proposals`；
   - `plan.candidate_revision_id`、`plan.accepted_revision_id`、`plan.scene_briefs`（按 order）；
   - `scenes` 队列的 order/title/status/current_run_id；
   - `active_run` 的 run id/version/status/pause reason。
4. 规划运行启动后按轮询或现有 SSE 能力刷新 workflow，不能只展示一次 POST 返回值。轮询必须有停止条件和错误提示。
5. `plan_feedback` 时允许作者输入反馈并提交；候选存在且等待接受时允许提交 accept。accept 必须使用服务端返回的 candidate revision id、expected run version/plan version（若有）和目标 `plan`。候选未接受前不得创建场景运行或把候选当 accepted plan。
6. 新 UI 不得调用兼容迁移接口 `POST /api/chapters/{chapter_id}/plan` 或 `createChapterPlan()`。旧函数可暂时保留供未迁移代码，但章节工作台主路径不得使用。
7. 使用现有 CSS/组件风格增量实现，避免引入新依赖或破坏旧测试选择器。补至少一条前端测试，覆盖“输入意图 -> 启动规划运行 -> workflow 显示候选/等待决策”的 UI 状态链；已有环境若 Playwright 因 `spawn EPERM` 无法启动，要在报告中如实说明。
8. 代码中的公共类型、函数和非直观状态转换补充中文注释；不为简单 JSX 添加注释。

## Non-goals

- 不修改后端 schema、Worker、数据库迁移或删除兼容 API。
- 不实现任务 4-6 的场景逐项决策、章节聚合/审校/Canon 闭环；这里只展示队列占位和已有能力入口。
- 不重写 `ManuscriptEditor`、`RunPanel`、`StoryBiblePanel`。

## Verification

在 `frontend` 目录运行：

- `npm run typecheck`
- 与新增测试对应的 Playwright 命令（若环境阻塞，报告完整错误）

必要时运行已有前端测试，确认旧编辑器/版本/Story Bible 选择器仍可编译。

## Report

完成后将实现说明、关键接口、测试命令/输出、未能运行的检查和遗留风险写入：

`codex-handoff/2026-08-06-chapter-workbench-task3-report.md`

返回状态只需说明 DONE / DONE_WITH_CONCERNS / BLOCKED、改动文件、测试摘要和 concerns；不要提交 Git commit。
