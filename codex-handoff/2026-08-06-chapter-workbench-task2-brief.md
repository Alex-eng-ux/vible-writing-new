# 章节工作台任务 2：Worker、迁移与确定性 E2E

## 任务位置

这是计划书阶段 0 的后半段。任务 1 已经增加候选计划、规划讨论、问题/建议、固定场景映射和 `ChapterWorkflowRead` 的领域契约；本任务把这些契约接到真实 Worker、数据库迁移和 Playwright fixture，达到“接受计划后能可靠启动一个场景，Worker 重启后仍可恢复”的阶段 0 退出门槛。

## 先读这些文件

- `docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md`：重点读 3.5、6.1.5、6.2、6.2.1、8.0、阶段 0、9.1、9.2、9.3、11。
- `backend/app/runtime/run_worker.py`
- `backend/app/worker_main.py`
- `backend/app/db/models.py`
- `backend/app/db/e2e_fixtures.py`
- `backend/app/db/e2e_bootstrap.py`
- `frontend/playwright.config.ts`
- `backend/tests/runtime/test_run_worker.py`
- `backend/tests/runtime/test_real_chapter_worker_chain.py`

## 目标

1. 为任务 1 新增的五张业务表和 `ChapterPlanRevision` 新字段补充 Alembic migration；迁移升级/降级语义、索引和唯一约束必须与 SQLAlchemy model 一致。
2. 让 `RunWorker` 对 `new_chapter` Planner 运行读取并投影 `chapter_intent`、规划讨论、待回答问题、待确认建议、作者决策和当前候选计划；这些规划专属字段只注入 `ChapterPlannerAgent`，场景 Agent、章节审校和 Canon 不得收到 `plan_discussion`。
3. Planner 运行完成后调用 `persist_planner_output`，保存 pending 候选并把运行状态/事件写到可恢复的边界；Planner 未 ready 时必须保持 clarification/feedback 状态，不能直接进入场景图。
4. accepted plan 之后不再通过旧 `ChapterGraph` 直接跳到 ChapterReview/Aggregator。增加最小章节队列协调路径：从 `chapter_plan_scene_links.sort_order` 恢复第一个未完成场景，创建或恢复对应 scene run；同一事件重复投递不能创建第二个 scene run。
5. `chapter_plan.accepted` outbox 的消费/重放必须可幂等；Worker 启动时能从 accepted pointer 和最后事件序号重建队列。租约丢失、重复消费和服务重启要有明确测试证据。
6. 提供确定性的 Fake provider/fixture，不访问外部模型。扩展 `backend/app/db/e2e_fixtures.py` 或新增最小 fixture 命令，支持准备章节意图、候选计划、重放 accepted 事件和读取最终状态。
7. `frontend/playwright.config.ts` 的 webServer 或测试前置必须同时启动 API、前端和 Worker，复用相同数据库和 actor 配置，并在测试结束时可靠退出。
8. 新增后端 Worker/迁移/恢复测试，以及一条最小 Playwright 主流程测试：输入非空章节意图，触发 `new_chapter` 规划运行，接受候选计划，验证固定场景映射和第一个场景运行；在第一个场景运行期间重启 Worker/重放 outbox 后，workflow 读取仍指向同一场景。
9. 更新现有旧 `run_lifecycle` 测试 fixture，给 `new_chapter` 运行传非空 `chapter_intent.text`；不要放宽新的非空意图契约。

## 所有权边界

- 可以修改：`backend/app/runtime/run_worker.py`、`backend/app/worker_main.py`、`backend/app/runtime/outbox.py`、`backend/app/services/generation_runs.py`、`backend/app/domain/chapters.py`、`backend/app/db/migrations/versions/*`、`backend/app/db/e2e_fixtures.py`、`backend/app/db/e2e_bootstrap.py`、`frontend/playwright.config.ts`、相关后端 runtime/API 测试和新增的章节工作流 Playwright 测试。
- 不要修改：`frontend/src/app/page.tsx`、`frontend/src/services/api.ts`、`frontend/src/types/index.ts`；前端主页面由下一任务负责。
- 不要删除 `POST /api/chapters/{chapter_id}/plan`；只有完整主流程通过后才能按计划书 6.4 删除。
- 不要新增第二套模型调用协议；复用现有 `ModelProvider`、`RunExecutor`、lease、checkpoint、RunEvent、outbox 和领域事务。
- 不要把业务正文或 Planner 讨论放进外部观测 sink；Fake provider 只输出稳定结构化结果。

## 验收命令

实现后报告必须包含实际命令和输出：

```text
backend/.venv/Scripts/python.exe -m pytest -q <新增/修改的 Worker、迁移、workflow 测试>
backend/.venv/Scripts/python.exe -m ruff check <本任务改动文件>
backend/.venv/Scripts/python.exe -m compileall app
frontend npm run typecheck
frontend npx playwright test tests/chapter-workflow.spec.ts --project=chromium
git diff --check
```

如果本机 PostgreSQL 或浏览器依赖不可用，必须在报告中写明阻塞命令、已完成的替代验证和未覆盖的真实边界，不能把 fixture 单测说成完整 E2E。

## 交付物

- 代码和测试改动。
- `codex-handoff/2026-08-06-chapter-workbench-task2-report.md`，包含状态、改动文件、测试命令/结果、已知风险和未完成项。
- 不提交 Git commit；当前工作区的分支/refs 由宿主环境管理。
