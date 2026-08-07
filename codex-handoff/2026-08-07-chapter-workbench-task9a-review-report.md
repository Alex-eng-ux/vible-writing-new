### Spec Compliance
- ❌ Issues found: 场景运行前虽刷新 `ChapterWorkflowRead`，但没有用快照中的队列阻断信息拒绝创建运行，未满足“覆盖队列阻塞”的前置校验要求。
- ⚠️ Cannot verify from diff: 实现报告仅叙述 RED/GREEN 意图，没有记录实际 RED 命令及其失败结果；跟踪 diff 也未包含其声称新增的 `frontend/tests/runs.spec.ts`。

### Strengths
- ✅ `frontend/src/app/page.tsx:455-459` 场景选择改为读取 `/workflow`，并移除了独立 `chapterPlans` 状态源。
- ✅ `frontend/src/app/page.tsx:643-657` 每次启动场景运行前重新读取 workflow，并从 `plan.accepted_revision_id` 传递计划版本。
- ✅ `frontend/src/services/api.ts:222-238` 已移除旧 `/plan` 读取及初始化写入适配，改为 `/workflow` 和 `/runs`。
- ✅ `frontend/tests/editor.spec.ts:287-306` 覆盖场景工作台不请求旧 `/plan` 且编辑器仍可保存。

### Issues
#### Critical (Must Fix)
- None.

#### Important (Should Fix)
- `frontend/src/app/page.tsx:643-650`：读取到最新 `workflow` 后只检查 `plan.accepted_revision_id`，没有检查 `workflow.blocking_reasons`、`workflow.active_run` 或当前场景在 `workflow.scenes` 中的阻断/运行状态，随即在 `651-662` 创建运行。工作台在队列已阻断或同章运行占用时仍会发出创建请求，违反任务明确要求的“队列阻塞”覆盖；应由同一份新快照拒绝运行并显示服务端阻断原因，并新增该路径的回归测试。
- `frontend/tests/runs.spec.ts:149-168`：新增测试只断言 workflow GET 次数，既不断言不存在旧 `/plan` 请求，也不模拟 accepted pointer 变更或队列阻断，无法证明刷新结果实际参与前置决策。需要将 test 改为根据第二次 workflow 响应验证提交的 `plan_revision_id`，并分别断言阻断时不产生 `POST /api/scenes/{id}/runs`。

#### Minor (Nice to Have)
- `frontend/src/app/page.tsx:441-480`：快速连续切换不同章节的场景时，较早请求可在较晚请求之后写入 `chapterWorkflow`。计划摘要以章节 id 兜底为空，运行前会再次刷新，当前不会使用错误 accepted pointer；但可用请求序号或取消机制避免短暂的空摘要状态。

### Assessment
**Task quality:** Needs fixes
**Reasoning:** 权威状态源迁移和运行前刷新已完成且类型检查通过，但阻断信息尚未用于运行前置决策，测试也未覆盖任务要求的 accepted plan 变化与队列阻塞路径。

### Checks Run
- ✅ `frontend/npm run typecheck`: passed.
- ✅ 静态核对 `frontend/src/app/page.tsx:643-662`: 刷新后的 `ChapterWorkflowRead` 只读取 accepted plan 指针，未消费阻断状态。
- ✅ 静态核对 `frontend/tests/runs.spec.ts:149-168`: 仅断言 workflow 读取次数。
