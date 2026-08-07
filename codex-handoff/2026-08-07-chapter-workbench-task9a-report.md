# Chapter Workbench Task 9A 报告

## 工作内容

- 将场景工作台的章节状态源统一到 `ChapterWorkflowRead`。
- 场景选择继续读取 workflow；场景运行前置检查改为每次重新读取最新 workflow，避免复用旧缓存里的 accepted plan。
- 新增回归测试，覆盖“运行前必须刷新同章 workflow 快照”的行为。

## TDD 记录

- RED：新增 `frontend/tests/runs.spec.ts` 中的 `场景运行前置检查刷新同章 workflow 快照`，它要求点击场景运行前，`/api/chapters/{chapter_id}/workflow` 至少再读取一次。
- GREEN：将 `frontend/src/app/page.tsx` 的 `handleStartRun` 改为运行前总是重新读取 workflow，并以该快照中的 `plan.accepted_revision_id` 作为场景运行前提。

## 验证

- `frontend/npm run typecheck` 通过。
- `npx playwright test tests/runs.spec.ts --list` 通过并列出新增回归。
- `npx playwright test tests/runs.spec.ts --grep "场景运行前置检查刷新同章 workflow 快照" --reporter=line` 在当前环境被 `spawn EPERM` 拦截，无法实际启动浏览器。

## 当前关注点

- 浏览器级 Playwright 在当前环境无法启动，所以这条回归只能先用类型检查和测试清单确认，不能给出实际浏览器执行结果。

## 追加修复

- 按 review 补齐运行前置门控：`handleStartRun` 现在会先读取 `workflow.blocking_reasons`、`workflow.active_run`、当前场景的 `status/current_run_id/blocking_reasons`，任何一项不满足都直接返回，不再创建 `/api/scenes/{id}/runs`。
- 新增四条回归：刷新后的 workflow plan 指针必须进入场景运行请求、workflow 阻断时不创建场景运行、章节已有活动运行时不创建新的场景运行、场景处于运行中状态时不创建新的场景运行。
- 验证结果：`frontend/npm run typecheck` 通过；`npx --no-install playwright test tests/runs.spec.ts --list` 列出 11 条测试；聚焦浏览器执行（含四条新增回归）仍被 `spawn EPERM` 拦截，无法在当前环境启动浏览器。
