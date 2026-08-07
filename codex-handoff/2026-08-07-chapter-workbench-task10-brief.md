# Chapter Workbench Task 10 实施 Brief

## 目标

补齐阶段 4 的章节工作台 Playwright 主旅程证据，使验收从章节入口/局部 workflow 读取扩展到计划接受后的场景队列、章节审校、章节接受与 Canon 来源展示。测试必须通过真实 UI 主动作触发流程，API 只能用于建立 fixture 或读取最终断言数据。

## 文件边界

- 主要编辑：`frontend/tests/chapter-workflow.spec.ts`。
- 如确有必要建立确定性数据，只能补充 `frontend/tests/global-setup.ts` 或 `backend/app/db/e2e_fixtures.py` 的最小 fixture 命令；不要修改生产业务流程。
- 不修改 `frontend/src/app/page.tsx`、后端领域服务或 Canon 语义；发现生产缺陷时在报告中列出，不在本任务越界修复。

## 必须覆盖

1. 通过章节工作区输入非空自然语言意图并启动 `new_chapter` 规划，断言 workflow 阶段和 pending decision 来自真实响应。
2. 通过 UI 提交计划反馈或接受，断言 accepted plan 与有序场景队列可见，且未接受计划时不创建场景运行。
3. 使用确定性 fixture/Worker 推进至少一个场景，经过场景决策后断言下游场景或章节审校状态按服务端顺序变化；阻断状态必须阻止 UI 发起场景运行。
4. 所有场景完成后通过 UI 启动章节审校，断言 staged chapter revision、review issues/summary 可见；章节接受动作必须携带服务端 revision 并更新 workflow accepted 指针。
5. 章节接受后断言 Story Bible/Canon 面板显示 `ChapterRevision` 来源和当前 Canon run 状态；至少覆盖 confirm/reject/defer 结果的可见状态，不得把场景级 Canon 当章节级来源。
6. 使用 SSE/状态轮询或现有运行快照等待，不使用固定 `sleep`；失败时输出 phase、run id、pending decision、最后事件序号和未消费 outbox。

## TDD 与验收

- 先新增一个能够证明缺失主旅程的失败测试并运行，记录 RED；再实现最小 fixture/test 代码并运行 GREEN。
- 必须运行：`frontend/npm run typecheck`、相关 Playwright `--list`，并尝试真实 Playwright 执行。若环境仍返回 `Error: spawn EPERM`，如实记录完整命令和阻断位置，不得声称浏览器通过。
- 报告写入 `codex-handoff/2026-08-07-chapter-workbench-task10-report.md`，包含改动文件、RED/GREEN 命令与输出、未解决项。
- 不创建 commit。
