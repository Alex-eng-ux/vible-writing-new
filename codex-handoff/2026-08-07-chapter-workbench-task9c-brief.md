# Chapter Workbench Task 9C：Story Bible Canon 来源绑定 brief

## 目标

候选读取明确返回当前 accepted revision 对应的最新 Canon run（含 queued/running 且暂无候选），只返回该 run 的候选；前端按服务端 run_id / source_revision_id 展示和提交。

## 共享约束

- 使用 TDD：先补一个能证明缺陷的失败测试，再改生产代码；报告必须记录 RED/GREEN 命令和结果。
- 不恢复旧的 POST /api/chapters/{chapter_id}/plan 初始化入口，不让前端把旧 /plan GET 当作章节工作台权威状态源。
- 不删除用户已有改动；只修改任务范围内的文件。
- 所有版本来源、run_id、幂等和 accepted pointer 语义必须与现有后端契约一致。
- 每个 agent 写自己的报告文件，包含改动、测试、未解决事项；不要提交 commit。

## 范围

所有权：backend/app/api/canon.py、backend/app/api/schemas.py、frontend/src/features/storybible/StoryBiblePanel.tsx、对应 API/Story Bible 测试。

目标：候选读取明确返回当前 accepted revision 对应的最新 Canon run（含 queued/running 且暂无候选），只返回该 run 的候选；前端按服务端 run_id / source_revision_id 展示和提交，不能从任意历史候选反推当前运行。覆盖“活动 run 无候选”“历史候选 + 新 accepted revision”“章节/场景 scope 切换”。

非目标：不改变 confirm/reject/defer 的领域语义，不修改场景级 Canon 对全局 Story Bible 的隔离规则。

验收：后端候选来源测试、Story Bible 前端类型检查和相关回归通过；并发/幂等契约保持。
