# Task 4 修复报告

## 变更范围

- 决策入口重新读取章节当前 accepted plan 指针；旧计划场景 run 的 accept、feedback、cancel 决策统一拒绝。
- `chapter_workflow_read` 仅将当前 accepted plan 的场景 run 作为 active run，并让场景状态优先展示活动 run 状态，再回退到 accepted revision。
- Worker 构建 envelope 时校验持久化场景 accepted revision 与 queued run 创建时保存的 `base_scene_revision_id`；基线变化时 fail-closed 抛出 `SCENE_STALE`。
- accepted-plan outbox replay 复用直接入队的顺序及前置场景 accepted revision 检查，不能越过未接受的前置场景。
- Worker 的 `scene_brief` 从 accepted plan 的固定 `ChapterPlanSceneLink.client_key` 映射和 plan snapshot 读取，不再依赖可变 `Scene.scene_brief`。
- Worker 在 `_process_one()` 入口重新锁定并校验当前 accepted plan pointer；计划替换后旧 queued 场景 run 原子标记为 `superseded`，写入 `PLAN_REVISION_CONFLICT` 事件，且不会领取租约或构建 graph。
- 新增回归测试，覆盖旧 plan active run 过滤、worker 基线重验、活动 feedback 状态优先和旧 queued run 执行前拒绝。

## 验证

- RED：`test_worker_rejects_queued_scene_run_after_plan_replacement` 在修复前观察到旧 run 被执行为 `waiting_feedback`，且进入 graph。
- `pytest tests/runtime/test_chapter_workflow_task4.py -q`：10 passed。
- `pytest tests/runtime/test_chapter_workflow_task4.py tests/runtime/test_chapter_workflow_worker_task2.py tests/api/test_chapter_workflow_api.py tests/api/test_run_lifecycle.py -q`：28 passed。
- `pytest tests/services tests/api -q`：通过。
- Ruff（`app/runtime/run_worker.py` 与 task 4 测试）：通过。
- `python -m compileall -q app tests`：通过。
- 前端 `npm run typecheck`：通过；Playwright 仍因环境 `spawn EPERM` 未能启动。

## 当前缺口

accepted plan 的 `scene_briefs` JSONB 仍没有数据库级不可变保护；当前代码路径不提供 accepted plan 原地编辑入口，Worker 读取的是 accepted plan 映射。未提交 Git commit。
