# Chapter Workbench Task 9A：工作台统一状态源 brief

## 目标

让场景工作台、场景运行前置校验和章节摘要都从同一份 ChapterWorkflowRead 快照读取，不再把旧 /plan 当作章节状态源。

## 共享约束

- 使用 TDD：先补一个能证明缺陷的失败测试，再改生产代码；报告必须记录 RED/GREEN 命令和结果。
- 不恢复旧的 POST /api/chapters/{chapter_id}/plan 初始化入口，不让前端把旧 /plan GET 当作章节工作台权威状态源。
- 不删除用户已有改动；只修改任务范围内的文件。
- 所有版本来源、run_id、幂等和 accepted pointer 语义必须与现有后端契约一致。
- 每个 agent 写自己的报告文件，包含改动、测试、未解决事项；不要提交 commit。

## 范围

所有权：frontend/src/app/page.tsx、必要的前端类型/API 只读适配和对应 workflow/编辑器测试。

目标：场景选择、场景运行前置检查、计划摘要均来自同一份 ChapterWorkflowRead 快照；场景正文/编辑器详情可以保留场景专属读取，但不得再用 getChapterPlan 或独立 chapterPlans 作为章节状态源。覆盖 accepted plan 变化、队列阻塞、场景切换等路径。

非目标：不改后端领域事务；不恢复旧计划初始化 API。

验收：至少新增或修正一个失败测试证明场景打开/运行不请求旧 /plan，并通过相关前端类型检查和可运行的聚焦测试。
