# 章节工作台任务 2 报告

## 状态

DONE_WITH_CONCERNS

复核后补齐：`new_chapter` 的 ready Planner 在候选持久化后暂停；`planner_output` 纳入可恢复运行状态；非 Planner 运行不会收到规划讨论字段；accepted pointer 与 outbox payload 会校验一致，场景运行同时写入 `run_queued` outbox。

## 改动文件

- `backend/app/runtime/run_worker.py`
  - `new_chapter` Worker 从持久化章节意图、规划讨论、待回答问题和待确认建议重建 Planner 输入。
  - Planner 结构化输出在运行事务内调用既有 `persist_planner_output`，候选始终保持 `pending`。
  - 消费 `chapter_plan.accepted` outbox，按 `chapter_plan_scene_links.sort_order` 选择第一个未完成场景并创建 queued scene run。
  - outbox 重放、Worker 重启和已有运行均按 `(chapter_id, scene_id, plan_revision_id)` 查重；首个场景未接受前不推进后续场景。
- `backend/app/agents/chapter_graph.py`
  - 仅把 Planner 结构化输出返回给 Worker 事务持久化；`new_chapter` ready 后暂停，阻止旧图直达章节审校/聚合。
- `backend/app/agents/state.py`
  - 增加可恢复的 `planner_output` 中间字段，避免 LangGraph 状态合并时丢失候选。
- `backend/tests/conftest.py`
  - 清理跨测试提交的 plan outbox，避免 Worker 重新驱动历史章节队列。
- `backend/tests/runtime/test_chapter_workflow_worker_task2.py`
  - 新增 Worker 上下文重建、候选持久化、Planner 路由、accepted outbox 消费、顺序恢复和重复重放测试。
- `backend/tests/runtime/test_real_chapter_worker_chain.py`
  - 将 `new_chapter` 回归调整为候选持久化并暂停，不再断言旧 Planner -> Review 直连。
- `frontend/playwright.config.ts`
  - 增加 `chromium` project，并让 webServer 同时拉起 API、前端和 Worker，共用 E2E 数据库及 actor 配置。
- `frontend/tests/chapter-workflow.spec.ts`
  - 新增最小 Playwright 主流程：创建非空章节意图、启动 `new_chapter` 规划运行并读取 workflow。

任务 1 已提供的五张规划表及 Alembic migration 保持不变，本任务未新增第二套模型协议，也未删除 `POST /api/chapters/{chapter_id}/plan`。

## 实际测试命令与结果

- `backend/.venv/Scripts/python.exe -m pytest -q tests/db/test_migrations.py tests/runtime/test_chapter_workflow_worker_task2.py tests/runtime/test_run_worker.py tests/runtime/test_real_chapter_worker_chain.py`：`19 passed, 1 skipped`。
- `backend/.venv/Scripts/python.exe -m ruff check app/runtime/run_worker.py app/agents/chapter_graph.py app/agents/state.py tests/conftest.py tests/runtime/test_chapter_workflow_worker_task2.py tests/runtime/test_real_chapter_worker_chain.py`：`All checks passed`。
- `backend/.venv/Scripts/python.exe -m compileall -q app`：通过。
- `frontend npm run typecheck`：通过。
- `git diff --check`：通过（仅有既有换行符提示）。
- `frontend npx playwright test tests/chapter-workflow.spec.ts --project=chromium`：阻塞，当前受限环境返回 `spawn EPERM`，未能启动浏览器/webServer。

## 已知风险

- 本地未运行 Playwright：任务 brief 要求同时启动 API、前端和 Worker，但当前环境未确认 PostgreSQL E2E 服务及浏览器依赖，因此未声称完整 E2E 已覆盖。
- Worker 当前按 `GenerationRun.status == "accepted"` 判断场景是否完成；后续若引入新的场景完成终态，需要同步队列推进条件。
- outbox consumer 将业务记录标记为 `consumed`，现有 publisher 仍可独立使用 `pending/publishing/published` 状态；生产部署需确保 consumer 与 publisher 的轮询职责一致。
- Playwright 浏览器进程仍受当前环境 `spawn EPERM` 阻塞，真实 API + 前端 + Worker 集成尚未获得运行时证据。

## 未完成事项

- Playwright 测试和 Worker webServer wiring 已增加，但本地浏览器进程启动被 `spawn EPERM` 阻塞；因此未覆盖真实 API+前端+Worker 集成边界。
- 未提交 Git commit，按任务要求保留工作区改动供主任务整合。
