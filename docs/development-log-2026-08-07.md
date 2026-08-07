# 开发日志 2026-08-07

## 工作性质与范围

本次继续执行章节工作台 v2 计划，完成终审报告中的重要问题修复，并继续处理真实 Playwright 重跑暴露的 Worker 启动竞态、章节决策基线校验和前端回滚回归。范围覆盖章节意图持久化、计划接受前场景阻断、按 accepted revision 绑定 Canon、Canon fixture 幂等、诊断 outbox 状态、Story Bible 稳定导航定位、Worker schema 就绪门禁、章节 `base_chapter_revision_id` CAS，以及前端编辑器/运行恢复/回滚测试。

## 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 场景工作台统一使用 `ChapterWorkflowRead` | 用户要求计划执行到落地，终审确认旧 `/plan` 读取会造成状态源分叉 | 前端移除 `getChapterPlan`、`chapterPlans`，场景选择与场景运行前置校验改读 workflow；后端只读 GET 别名暂留兼容 |
| Canon 候选按 accepted revision 和当前 run 绑定 | 避免自动运行尚无候选时状态消失，以及历史候选串到新来源 | 候选列表返回 `source_revision_id`、`run_id`、`run_status`，只返回当前来源最新 run 的候选 |
| 继续用 TDD 和子 agent 验收 | 用户明确要求任务分发、回报、循环验收 | Task 7 由修复 agent 完成，终审 agent 复审；本轮先补回归测试再改生产代码 | 
| 测试路由必须在页面销毁前清理 | 真实提升权限重跑仍复现后台 workflow 回调的 `Response/request context disposed`，根因是测试收尾时路由仍在途 | `runs.spec.ts` 增加 `test.afterEach` 调用 `page.unrouteAll({ behavior: "ignoreErrors" })`，只处理测试 mock 的收尾竞态 |
| 浏览器验证必须取得真实进程证据 | 普通沙箱曾返回 `spawn EPERM`，不能把进程启动失败冒充行为 RED/GREEN | 使用提升权限环境实际启动 API、Worker、Next.js 和 Chromium；章节旅程 3/3、Story Bible 4/4、全量 25/25 通过 |
| E2E 导航必须使用稳定资源身份 | Story Bible 旧 helper 用 `hasText: "V"`，在历史项目名包含相同文本时触发 strict mode | `openScene` 改为传递 project/volume/chapter/scene ID，并使用既有 `data-testid`，业务断言保持不变 |
| 回滚后的后续保存继续使用权威 accepted 基线 | 用户要求计划执行到落地，且回归测试需要验证版本血缘不被 staged 回滚覆盖 | 回滚只创建 staged 记录；workflow 的 accepted 指针保持原 accepted revision，后续 change set 必须以该指针为 `base_scene_revision_id` |

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
- Task 10 第二轮修复：`new_chapter` 意图持久化并由 workflow 回读；计划接受前不生成场景，后续场景 blocker 不再全局阻断首场景；章节审校和 Canon 主旅程改为 UI 启动并验证来源、作用域和状态。
- E2E fixture 改为按 run identity get-or-create；Canon 重复播种不重复 fencing/event；`diagnose` 排除已消费 outbox；`source_revision_id` 按实际 API 顶层契约断言。
- Task 11 清零 `mypy app` 的 22 个类型错误，并补齐 fixture 局部类型标注；未使用 `# type: ignore`，未删除测试。
- Task 12 修复 Story Bible Playwright 模糊导航定位器，新增稳定 ID 传递；真实 RED 复现后修复，未改业务代码。
- 新增验收材料：`codex-handoff/2026-08-07-chapter-workbench-task10-rereview-2.md`、`codex-handoff/2026-08-07-chapter-workbench-task12-report.md`。
- 新增 `codex-handoff/2026-08-07-frontend-test-encoding-repair.md`：修复 editor/runs 新增 Playwright 用例中的 UTF-8 乱码，真实场景活动运行重载和恢复用例通过。
- 新增 `codex-handoff/2026-08-07-backend-migration-race-report.md`：E2E Worker 在 schema 可读前不启动轮询、不开放 ready；章节 accept/feedback 消费并校验 `base_chapter_revision_id`，过期基线返回 `CHAPTER_OUT_OF_SYNC`。
- 新增后端回归测试覆盖 schema 就绪门禁和章节过期基线，前端回滚测试改为断言 accepted 指针而非 staged 回滚记录。
- 新增 `codex-handoff/2026-08-07-conflict-save-fix.md`：冲突刷新时保留作者本地草稿，accepted 基线从 revisions 中取最后一条 accepted，覆盖提交和回滚后继续保存回归通过。

## 验证结果

- 后端全量 `pytest -q`：退出码 0，跳过项为环境/可选 smoke 测试。
- 后端全量 Ruff：通过。
- 后端 `mypy app`：`Success: no issues found in 111 source files`。
- 后端 `compileall -q app`：通过。
- 前端 `npm run typecheck`：通过。
- 提升权限后全量 Playwright：`31 passed`，覆盖章节工作流、编辑器冲突/回滚、运行恢复、Planner/Chapter feedback、Story Bible 与 Canon 旅程。
- 提升权限后 `npm run build`：通过，Next.js 完成静态页生成和生产构建。
- `git diff --check`：通过。
- 旧 `createChapterPlan`、`handleCreateChapterPlan`、`post_chapter_plan` 符号搜索无结果。
- 后端聚焦回归：45 tests passed；Ruff、`mypy app`、`compileall` 和 `git diff --check` 通过。
- 前端新增回归：`editor` 场景活动运行重载通过，`runs` 活动运行恢复通过；回滚后续保存已确认使用原 accepted revision 作为基线。

## 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| Playwright 的 `reuseExistingServer` 与 E2E globalSetup 可能在启动窗口发生服务生命周期竞态 | Worker 启动日志中仍观察到一次数据库 schema 尚未创建时的 `run_outbox_records` 查询错误；本次未影响 ready 门禁或测试结果，但会污染启动日志 | Worker 已在 schema 可读前阻止轮询并 fail-closed；本轮提升权限全量 Playwright `31 passed`，未出现用例失败；后续可将服务启动与数据库重置进一步串行化 |

## 当前未完成事项与下一步

章节工作台 v2 阶段 0-4、Task 10/11/12 已完成；本轮后端 Worker/章节 CAS 修复和前端冲突保存、回滚回归已实现并通过最终门禁。必选未完成事项：无。后续仅保留启动竞态的基础设施优化，不阻断当前功能落地。
