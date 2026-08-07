# Planner 反馈契约任务交接

## 范围

补齐 Chapter Workbench v2 的 Planner 待回答问题与待确认提案交互，以及前端决策请求中的结构化 `feedback` 字段；保留旧版自由文本 `text` 兼容路径。

## TDD 证据

- RED：新增 `frontend/tests/planner-feedback.spec.ts` 后运行 `npx playwright test tests/planner-feedback.spec.ts`，因页面尚无 `planner-question-question-1` 控件失败。
- GREEN：实现后同一命令通过，`1 passed`；在共享分支出现重复导出后，清理重复的章节回滚 API 与 `base_chapter_revision_id` 字段，再次复验仍为 `1 passed`。
- 类型验证：`frontend/npm run typecheck` 通过。

## 实现落点

- `frontend/src/services/api.ts`：`RunDecisionBody.feedback` 增加 `answers[]` 与 `proposals[]` 的类型契约，提案动作限定为 `accept|modify|reject`。
- `frontend/src/app/page.tsx`：增加 Planner 问题答案、提案动作和值的受控状态；展示问题专属文本框和提案三态按钮；反馈提交时发送结构化数组，并继续发送旧 `text`。
- `frontend/tests/planner-feedback.spec.ts`：通过路由隔离的工作流快照验证控件、修改提案值和最终 POST payload。

## 验收注意

后端 `DecisionRequest.feedback` 与 `generation_runs.submit_run_decision` 已支持上述字段，本任务未改动后端。现有 `answer_planner` 页面仍保留通用自由文本框，便于无结构化内容时反馈；提案动作仅在作者明确点击后加入 payload。
