# Task 3 前端章节工作台报告

状态：DONE_WITH_CONCERNS

## 实现

- `frontend/src/types/index.ts` 新增 `ChapterWorkflowRead` 类型，覆盖服务端阶段、意图、Planner 讨论、候选计划、场景队列、运行和阻断信息。
- `frontend/src/services/api.ts` 新增 `getChapterWorkflow`、`createChapterRun`，并扩展章节/计划决策请求字段；旧 `getChapterPlan` 与 `createChapterPlan` 兼容接口保留。
- `frontend/src/app/page.tsx` 增加章节选择、workflow 读取/轮询、自然语言意图输入、Planner 反馈、候选计划接受/取消和阻断展示；选中场景时仍走原有编辑器、运行、版本和 Story Bible 分支。
- 章节树入口统一为 `chapter-item-${id}` 按钮，文案为“打开章节工作台”；工作台决策入口补齐 `btn-plan-feedback`、`btn-plan-cancel` 等稳定锚点。
- `frontend/src/app/globals.css` 增加章节工作台和讨论/计划/场景队列样式，队列与讨论区内部滚动。
- `frontend/tests/chapter-workflow.spec.ts` 增加章节工作台 UI 测试，使用 API 构造资源后通过 UI 选择章节并断言 workflow 阶段与意图输入。

## 验证

- TDD 红灯：`npx playwright test tests/chapter-workflow.spec.ts --project=chromium --grep "章节工作台从章节入口"`，当前环境返回 `Error: spawn EPERM`，无法启动浏览器进程。
- `npx playwright test tests/chapter-workflow.spec.ts --list`：通过，列出 2 个测试，证明测试文件可解析。
- `npm run typecheck`：通过。
- `npm run build`：当前环境返回 `spawn EPERM`，Next 构建子进程无法启动。

## 未能运行的检查与风险

- 当前执行环境禁止 Playwright/Next 构建子进程启动，未获得真实浏览器 UI 通过证据；需要在允许进程创建的环境重新运行章节工作台测试。
- 页面仍是较大的兼容页面，章节工作台目前以条件分支嵌入 `page.tsx`，后续可按设计拆分为 `ChapterWorkspace` 等组件，但本任务保持旧场景编辑能力优先。
- 旧章节计划初始化按钮处理函数仍保留以支持兼容回归；新章节工作台入口使用 `/runs` 和 `/workflow`，不依赖旧 POST 初始化接口。
