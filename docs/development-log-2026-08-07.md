# 开发日志 2026-08-07

## 工作性质与范围

本次继续执行章节工作台 v2 计划，处理终审报告中的三个 Important 问题，并补齐全量后端回归中暴露的测试夹具契约。随后继续修复 `runs.spec.ts` 的 Playwright 路由收尾竞态。范围覆盖场景工作台读取源、Canon 候选来源绑定、Story Bible 运行状态、旧编辑器 Playwright 用例、E2E Worker 配置、场景运行 fixture 和相关后端测试。

## 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 场景工作台统一使用 `ChapterWorkflowRead` | 用户要求计划执行到落地，终审确认旧 `/plan` 读取会造成状态源分叉 | 前端移除 `getChapterPlan`、`chapterPlans`，场景选择与场景运行前置校验改读 workflow；后端只读 GET 别名暂留兼容 |
| Canon 候选按 accepted revision 和当前 run 绑定 | 避免自动运行尚无候选时状态消失，以及历史候选串到新来源 | 候选列表返回 `source_revision_id`、`run_id`、`run_status`，只返回当前来源最新 run 的候选 |
| 继续用 TDD 和子 agent 验收 | 用户明确要求任务分发、回报、循环验收 | Task 7 由修复 agent 完成，终审 agent 复审；本轮先补回归测试再改生产代码 | 
| 测试路由必须在页面销毁前清理 | 真实提升权限重跑仍复现后台 workflow 回调的 `Response/request context disposed`，根因是测试收尾时路由仍在途 | `runs.spec.ts` 增加 `test.afterEach` 调用 `page.unrouteAll({ behavior: "ignoreErrors" })`，只处理测试 mock 的收尾竞态 |

## 关键规则与取舍

- 新 UI 不再调用旧章节计划初始化 POST，也不再依赖旧 `/plan` 读取作为章节状态源。
- Canon 候选读取必须先解析服务端当前 accepted revision，再按来源选择最新 Canon run；没有 run 时返回空候选和空运行状态。
- 旧测试夹具按新契约补齐：`new_chapter` 必须带非空 `chapter_intent.text`；场景 worker fixture 必须把 `base_scene_revision_id` 写入 `normalized_input`。
- 浏览器 E2E 仍保留真实测试与 Worker 配置，不因当前环境无法启动子进程而删除验证。

## 已完成产出

- 删除旧章节计划初始化 POST 的生产入口和前端调用；保留只读 GET 兼容别名。
- 场景工作台改为 workflow 单一权威读取，并重写旧编辑器回归用例。
- 场景计划面板按候选/accepted 状态分别显示对应 revision/version，避免候选内容标记旧 accepted 版本。
- Canon 候选 API 增加当前来源修订、run id、run status，并按当前 run 过滤候选；Story Bible 使用 API 返回的 run id。
- 新增章节/场景 Canon 候选来源与 queued 无候选回归测试。
- 修正全量回归中两个旧测试 helper 的输入契约。
- 新增交接材料：`codex-handoff/2026-08-07-chapter-workbench-task8-brief.md`。
- 修复 `runs.spec.ts` 中 4 处已消费 `APIResponse` 的 route mock，统一显式回填状态、响应头和 JSON；增加测试结束时的路由清理，并更新 `codex-handoff/2026-08-07-runs-route-fix-report.md`。

## 验证结果

- 后端全量 `pytest -q`：通过，跳过项为环境/可选 smoke 测试。
- 后端全量 Ruff：通过。
- 前端 `npm run typecheck`：通过。
- Playwright 测试文件 `--list`：列出 11 个测试；实际浏览器执行被环境 `Error: spawn EPERM` 阻断。
- 提升权限后 `frontend/tests/runs.spec.ts` 完整执行：`11 passed`；目标 workflow 刷新用例单独执行：`1 passed`。
- Next.js `npm run build`：被同一 `spawn EPERM` 阻断。
- `git diff --check`：通过。
- 旧 `createChapterPlan`、`handleCreateChapterPlan`、`post_chapter_plan` 符号搜索无结果。

## 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| 普通沙箱仍禁止 Playwright/Next 启动子进程 | 常规权限下无法取得浏览器旅程和生产构建证据 | 已复现 `spawn EPERM`；提升权限后 runs 套件已取得真实证据 |
| 浏览器级章节全旅程和其他回归文件尚未在本轮重跑 | 不能把 `runs.spec.ts` 通过等同于全部前端旅程通过 | `runs.spec.ts` 已通过；`chapter-workflow.spec.ts`、`editor.spec.ts`、`story-bible.spec.ts` 仍待同一环境执行 |

## 当前未完成事项与下一步

文档、后端实现、前端类型检查、全量后端回归和 `runs.spec.ts` 已完成验证。下一步在允许启动子进程的环境执行剩余 Playwright 文件和 Next.js 构建，然后进行全分支 changed-surface 复审；若出现失败，按 TDD 逐项复现、修复并更新本日志。
