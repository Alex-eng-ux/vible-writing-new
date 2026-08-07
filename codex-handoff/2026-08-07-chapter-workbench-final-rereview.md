# Chapter Workbench 最终复审

审查基线：`docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md` 第 9.1、9.2、9.3 节，以及 `codex-handoff/2026-08-07-chapter-workbench-task7-fix-report.md`。

本次只读审查当前工作区。`spawn EPERM` 被记录为当前环境无法启动子进程的验证限制，不作为应用代码缺陷。

## Critical

未发现 Critical 问题。

## Important

### 1. 场景工作台仍以旧 `/plan` 读取拼装章节状态，违反 `ChapterWorkflowRead` 的权威读取边界

- 文件：`frontend/src/app/page.tsx:458-462, 645-648, 986-987, 1206-1235`；`frontend/src/services/api.ts:224-226`；`backend/app/api/chapters.py:189-224`
- 技术依据：选择场景时并行读取 `getChapterPlan()`，启动场景运行前再次读取它，并以独立的 `chapterPlans` 状态渲染章节计划。该 GET 仅返回 accepted plan 的简化投影，缺少 workflow 的场景队列、活动运行、阻断原因、stale/affected、章节版本和 Canon 来源。因此同一章节工作台在“打开章节”和“打开场景”两条路径会使用不同状态源，违反设计书“`ChapterWorkflowRead` 一次提供工作区状态，前端不得拼接多个最新记录”的约束。
- 影响：accepted plan 替换、队列推进或阻断状态变化时，场景工作台可展示陈旧的计划摘要；作者只会看到“先完成章节规划并接受计划”，而看不到服务端返回的具体阻断原因和下一步动作。这是用户可见的恢复死路，不是单纯的展示冗余。
- 是否需要修复：需要。场景选择和场景运行前置校验应消费同一 `ChapterWorkflowRead` 快照；仅把 scene detail/正文作为场景专属读取，移除 `chapterPlans` 的独立权威语义。

### 2. 已删除初始化入口的 Playwright 回归仍保留，具备浏览器环境后会确定失败

- 文件：`frontend/tests/editor.spec.ts:294-302`
- 技术依据：用例仍查找并点击“生成章节计划”按钮，并断言 `init-plan`。Task 7 已删除该按钮和 POST；当前页面仅在 `frontend/src/app/page.tsx:1234` 提示先完成章节规划流程。因此该测试不再描述可用行为。
- 影响：当前 `spawn EPERM` 掩盖了该失败；在可启动浏览器的 CI/开发机上，该既有编辑器回归测试将因找不到按钮而失败。这也意味着第 9.3 节要求的旧编辑器回归尚无有效证据。
- 是否需要修复：需要。删除或重写为：经章节工作台接受计划后，打开受计划管理的场景，验证编辑器继续可用；不得恢复旧初始化入口。

### 3. Story Bible 不能可靠定位“当前 accepted 来源”的活动 Canon 运行，活动状态和候选可错配

- 文件：`backend/app/api/canon.py:140-170`；`frontend/src/features/storybible/StoryBiblePanel.tsx:93-110, 200-202, 253-266`
- 技术依据：章节/场景候选读取仅按 `chapter_id`/`scene_id` 查询，未按当前 accepted revision 或 Canon run 过滤；前端又取 `list.items.find(...)` 的第一个 `generation_run_id` 作为当前运行。自动创建的 Canon run 在候选尚未写入时会得到空列表，前端将 `run` 清为 `null`，使状态消失、按钮重新启用。若历史来源已有候选，前端还会把旧来源的第一个候选误当作当前运行。
- 影响：服务端 Task 7 的来源级互斥能安全复用手动请求（不会重复创建活动章节 Canon run），但 UI 仍不能准确显示自动运行、禁用状态或当前来源候选；用户可能看到旧候选，随后提交时因候选不属于当前 run 而失败。该问题直接影响“章节接受后进入 Canon、按 accepted revision 决策”的主流程。
- 是否需要修复：需要。候选 API 或工作流读取应明确返回当前来源的 Canon `run_id` 与候选集合；前端按该 ID/accepted revision 绑定，而不是从任意候选反推。覆盖“queued/running 且无候选”“历史候选 + 新 accepted revision”“场景/章节 scope 切换”三种状态。

## Minor

### 1. 旧 POST 的负向路由测试保留 URL 文本，但不构成调用残留

- 文件：`backend/tests/api/test_chapter_plan_init.py:7-23`
- 技术依据：该测试先验证 GET 别名，再以 POST 断言 405；它不是对旧初始化行为的正向测试。`backend/app`、`frontend/src`、`frontend/tests` 中未发现 `post_chapter_plan`、`createChapterPlan`、`handleCreateChapterPlan`、`chapter_plan_init` 或 POST 创建调用。
- 影响：与设计书“无调用方”的目的相符，但若要求字面上完全无 POST URL 文本，该负向测试会触发搜索结果。
- 是否需要修复：不需要功能修复；可将测试名称和注释保留为移除回归，避免误解为兼容入口测试。

### 2. Canon 自动/手动复用的服务端覆盖良好，但没有前端状态回归

- 文件：`backend/app/services/canon_runs.py:74-81, 318-336, 641-678`；`backend/tests/api/test_canon_api.py:217-256, 1015-1066`
- 技术依据：活动状态集合、共享 advisory lock、手动复用和双消费者并发均有后端实现与测试。当前前端 Playwright 只覆盖章节入口可见性和规划读取，未覆盖 Story Bible 的活动状态、来源切换及按钮禁用。
- 影响：服务端并发正确性有证据，UI 与 API 的状态契约没有浏览器级证据。
- 是否需要修复：建议补测试；与 Important 3 一并处理。

## 已验证项

- 旧 `POST /api/chapters/{chapter_id}/plan` 已从后端路由和前端创建调用移除；保留的 `GET /api/chapters/{chapter_id}/plan` 是只读别名。
- 章节 Canon 手动入口在同一 `(chapter_id, canon_source_revision_id)` 上对 `queued`、`running`、`waiting_feedback`、`pending_clarification`、`paused` 运行复用快照；自动 outbox 消费与手动入口使用同一 advisory lock。该行为由 `backend/tests/api/test_canon_api.py:217-256, 1015-1066` 覆盖。
- `backend/.venv/Scripts/python.exe -m pytest -q tests/api/test_chapter_plan_init.py tests/api/test_canon_api.py tests/runtime/test_chapter_workflow_task6.py`：30 passed。
- `frontend/npm run typecheck`：通过（`tsc --noEmit`）。
- `frontend/npm exec playwright test tests/chapter-workflow.spec.ts --grep "new_chapter" --reporter=line`：启动阶段 `spawn EPERM`，未执行浏览器断言。该环境限制不改变上述 `editor.spec.ts` 已陈旧这一静态事实。

## Assessment

**不建议合并为“已完成的章节工作台 v2”。**

Task 7 已修复旧 POST 创建入口和章节 Canon 服务端来源级幂等/并发问题，且聚焦后端验证通过；但第 9.1 至 9.3 节仍未满足：场景工作台没有统一到 `ChapterWorkflowRead`，旧编辑器 Playwright 用例已失效，Story Bible 在空候选的活动运行和跨 revision 历史候选下会丢失或错配运行状态。应先修复上述 Important 项并在可运行浏览器环境中完成包含 Worker/Fake provider 的真实主流程与既有场景编辑器回归。
