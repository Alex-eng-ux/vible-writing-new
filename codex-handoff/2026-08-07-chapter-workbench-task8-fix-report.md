# Chapter Workbench Task 8 修复报告

## 状态

DONE

## 修复内容

- 场景工作台的章节计划展示和场景运行前置校验统一使用 `ChapterWorkflowRead`；前端移除 `getChapterPlan` 和 `chapterPlans` 权威状态。
- 重写旧编辑器 Playwright 用例，验证场景打开不请求 `/plan` 且编辑器仍可保存。
- Canon 候选 API 返回当前 accepted 来源的 `source_revision_id`、最新 `run_id` 和 `run_status`，只返回该 run 的候选；Story Bible 使用该 run id，覆盖 queued 且无候选的状态。
- 更新 Canon 只读端点夹具，使候选与真实 Canon run/source revision 绑定。
- 更新旧 worker/outbox 测试夹具以满足非空章节意图和场景基线持久化契约。

## TDD 证据

先新增了候选来源/run 绑定测试、queued 无候选测试，以及场景工作台不访问旧 `/plan` 的 Playwright 断言；随后实现 API、前端状态和 workflow 读取替换，聚焦测试转绿。

## 验证

- `backend/.venv/Scripts/python.exe -m pytest -q`：全量通过（可选 smoke 测试按条件跳过）。
- `backend/.venv/Scripts/ruff.exe check app tests`：通过。
- `frontend/npm run typecheck`：通过。
- `npx playwright test tests/editor.spec.ts tests/story-bible.spec.ts --list`：列出 11 个测试。
- 实际 Playwright 与 `npm run build`：环境 `spawn EPERM`，无法启动子进程。

## 未解决事项

仅剩浏览器/Next 构建环境阻断，应用代码和后端回归未发现已知失败。
