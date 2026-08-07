# runs.spec 场景队列 fixture 修复报告

## 工作范围

修复 `runs.spec.ts` 中 accepted plan 没有映射已创建 scene 的测试契约，避免场景运行前置检查正确阻断后，测试无法显示 `run-panel`。

## 根因

`backend/app/db/e2e_fixtures.py::seed_plan` 原先始终播种空 `scene_briefs`。因此 `/api/chapters/{chapter_id}/workflow` 的 `scenes` 为空，而 `handleStartRun` 按 accepted plan 队列校验时会拒绝不在队列中的 scene。这是 fixture 不完整，不是生产运行逻辑错误。

## TDD 记录

### RED

新增 `backend/tests/runtime/test_e2e_fixtures.py::test_seed_plan_can_link_existing_scene_into_accepted_workflow`，先按 `seed_plan(db, chapter.id, scene.id)` 调用。

命令：

```text
backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/runtime/test_e2e_fixtures.py::test_seed_plan_can_link_existing_scene_into_accepted_workflow -q
```

结果：失败，`TypeError: seed_plan() takes 2 positional arguments but 3 were given`。

### GREEN

实现 `seed_plan(..., scene_id=None)` 的可选 scene 映射、CLI `--scene-id` 参数，并让 `frontend/tests/runs.spec.ts` 的 `seedPlan` helper 在所有场景运行测试中传入 `sceneId`。

同一 RED 命令再次执行：通过，1 passed。

## 改动文件

- `backend/app/db/e2e_fixtures.py`
- `backend/tests/runtime/test_e2e_fixtures.py`
- `frontend/tests/runs.spec.ts`

## 验证

- 后端 fixture 回归：通过。
- `frontend/npm run typecheck`：通过。
- `frontend/npx --no-install playwright test tests/runs.spec.ts --list`：通过，11 个测试被发现。
- `git diff --check`：通过；仅有 CRLF 转换提示。
- 浏览器实际执行：未完成。Playwright 启动时发现 `127.0.0.1:8001` 已被占用，当前配置 `reuseExistingServer: false`，因此未进入断言阶段。

## 未解决事项

需要在可控制 E2E Worker 端口的环境中重跑 `runs.spec.ts`，确认路由拦截场景没有残留请求竞态。当前没有修改生产运行逻辑。

### 并行改动后的补充核对

共享工作区随后加入了 `suppress_scene_execution` fixture 参数及对应回归。该回归目前仍失败：fixture 将 `chapter_plan` outbox 标记为 `failed` 后，`RunWorker._recover_accepted_plan_scene_queues()` 仍会扫描 accepted plan link 并创建 scene run。说明“仅标记 outbox failed”不能抑制 Worker 的状态恢复路径。

当前聚焦结果：Canon/workflow API 与 scene 映射回归通过；新增抑制自动执行回归失败（9 passed, 1 failed）。在决定是否改变 Worker 恢复语义前，不能把该 fixture 标记为完成。
