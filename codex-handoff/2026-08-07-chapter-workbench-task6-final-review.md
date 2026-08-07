# Task 6 章节工作台最终审查

审查范围：`RunWorker` 章节接受 outbox 消费、章节工作台 Canon 摘要与章级 Story Bible、章节审校决策按钮、前端类型契约及 Playwright 测试。

## Critical

未发现 Critical 问题。

## Important（已修复）

### 1. 章节审校运行激活时可能被误报为“已完成”

- 文件：`backend/app/domain/chapters.py:560-568`
- 当 `active` 是 `decision_target == "chapter"` 的章节审校运行时，阶段判断没有优先处理该分支，而是继续按场景完成情况落到 `completed`（尤其是已有旧 accepted chapter revision 时）。
- 影响：章节审校运行处于 `queued/running/waiting_feedback` 时，工作台阶段标签可能显示“已完成”；这会误导用户，也会使依赖 `phase === "chapter_review"` 的“启动章节审校”按钮语义不可靠。`pending_decision` 仍可能存在，因此 UI 会同时出现“已完成”阶段和待审校操作。
- 修复：在 `active.decision_target == "chapter"` 分支中按运行状态映射 `chapter_review/chapter_feedback`，优先于已接受版本的 `completed` 推导；新增回归测试 `backend/tests/runtime/test_chapter_workflow_task6.py:109-148`。

### 2. 章节接受后章级 Story Bible 使用过期的导航树 accepted revision

- 文件：`frontend/src/app/page.tsx:551-570, 1419-1432`
- `submitChapterDecision("accept")` 只刷新 `chapterWorkflow`，没有刷新导航树中的 `Chapter.accepted_chapter_revision_id`；右侧章级 `StoryBiblePanel` 却从 `selectedChapterContext.chapter.accepted_chapter_revision_id` 取 Canon 输入版本。
- 影响：接受新的 `ChapterRevision` 后，工作流摘要已经显示新版本，但 Story Bible 仍拿旧版本（或 `null`）。重新提取章节 Canon 的按钮会错误保持禁用，或向后端发送过期 revision，导致章节 Canon 交互无法继续。
- 修复：章级 `StoryBiblePanel` 优先使用 `chapterWorkflow.chapter_revision.accepted_revision_id`，仅在初始快照为空时回退导航树字段，避免接受后继续提交旧 revision。前端 Playwright 流程仍受当前环境 `spawn EPERM` 阻塞，未能执行浏览器级断言。

### 3. Worker 外层基础设施异常仍会终止轮询循环

- 文件：`backend/app/runtime/run_worker.py:101-114, 453-469`
- 章节 outbox 单条业务处理已经使用 savepoint 隔离并记录 `failed/attempt_count/next_attempt_at`，但 `_consume_plan_outbox`、`_consume_chapter_accepted_outbox`、`_recover_accepted_plan_scene_queues` 的数据库连接/事务异常会直接从 `tick()` 冒出；`run_forever()` 也没有兜底。
- 影响：数据库短暂断连、锁/事务基础设施异常会让 Worker 进程退出，无法继续轮询和恢复 outbox。业务 handler 异常虽已隔离，但基础设施异常仍是单点终止条件。
- 修复：`run_forever()` 捕获 `tick()` 外层异常、记录堆栈并按现有 interval 继续轮询；新增 `backend/tests/runtime/test_chapter_workflow_task6.py:277-303` 验证首次异常后第二次 tick 仍执行。

## Minor（未修复）

### 1. 删除当前章节后未清理章节选择状态

- 文件：`frontend/src/app/page.tsx:210-213`
- 删除章节时只在选中场景属于该章节时调用 `clearSelectedScene()`，没有清理 `selectedChapterId`。删除后可能继续显示已不存在章节的工作台和 Story Bible。

### 2. 前端测试覆盖仍偏向首屏可见性

- 文件：`frontend/tests/chapter-workflow.spec.ts:1-87`
- 当前测试覆盖新章节规划读取和章节入口渲染，但未覆盖：接受计划后场景顺序队列、章节审校启动/接受/反馈、章节接受后 Canon outbox 状态、章级/场景级 Story Bible 切换及既有场景工作台回归。

## 通过项

- `backend/app/runtime/run_worker.py:369-424`：章节接受 outbox 只选择 `pending/publishing/published/failed` 且尊重 `next_attempt_at`，成功置 `consumed` 并清理错误/重试时间；handler 异常在 nested transaction 外记录失败、尝试次数和 5 秒退避，后续 tick 可重放。重复消费依赖 Canon 服务的 advisory lock 与 `(chapter_id, accepted_revision_id)` 幂等键。
- `frontend/src/types/index.ts:171-230` 与 `frontend/src/services/api.ts:224-292`：章节工作流 Canon、章节 revision、章节 run/decision 字段已覆盖当前 JSX 使用，TypeScript 类型检查通过。
- `frontend/src/app/page.tsx:1291-1370`：无场景选择时可渲染章节工作台，并提供章节规划、章节审校和章节 revision 决策控件；有场景选择时保留既有场景编辑器分支。
- `frontend/src/app/page.tsx:1377-1410` 与 `StoryBiblePanel`：章节目标使用 `scene: null`，场景目标仍保留场景/章节切换，未发现必然的空场景解引用；静态类型检查通过。

## 验证命令与结果

- 初始审查时，`backend\\.venv\\Scripts\\python.exe -m pytest tests/runtime/test_chapter_workflow_task6.py tests/api/test_chapter_workflow_api.py -q`：4 passed。
- 复修后，同一命令：6 passed。
- `backend\\.venv\\Scripts\\python.exe -m ruff check app/domain/chapters.py app/runtime/run_worker.py tests/runtime/test_chapter_workflow_task6.py`：All checks passed。
- `frontend\\npm run typecheck`：通过（`tsc --noEmit`）。
- `frontend\\npm run lint`：未执行，`package.json` 没有 `lint` script。
- `frontend\\npx playwright test tests/chapter-workflow.spec.ts --reporter=line`：环境失败，浏览器进程 `spawn EPERM`。
- `frontend\\npm run build`：环境失败，Next 构建进程 `spawn EPERM`。
- `git diff --check -- <审查文件>`：通过；仅报告换行符 LF/CRLF 警告。
