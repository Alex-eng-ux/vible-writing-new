# Chapter Workbench Task 10 Review

## Critical

### C1. 章节接受后的 Canon 自动运行与手动按钮存在竞态，主旅程可能在无业务错误时失败

- 位置：`frontend/tests/chapter-workflow.spec.ts:230-245`。
- `commit_chapter_version` 会在接受章节的同一事务中写入 `chapter_revision.accepted` outbox；Playwright 配置启动的 E2E Worker 即使关闭 `process_queued_runs`，仍会消费该 outbox 并自动创建章节 Canon run。`StoryBiblePanel` 在 `queued/running/waiting_feedback` 时会把 `btn-canon-start` 设为 disabled。
- 测试在接受章节后无条件执行 `page.getByTestId("btn-canon-start").click()`。当自动运行已被消费并显示为 in-flight 时，Playwright 对 disabled button 的 click 会等待/失败；当尚未消费时又会走手动入口，造成依赖时序的两条路径。
- 应先轮询章节 Canon 快照/运行状态，复用自动创建的 run；仅在明确没有自动 run 且产品契约允许手动兜底时才创建运行。不能把“点击手动按钮后能得到 run”作为章节接受后的既定语义。

## Important

### I1. 新增/保留的规划测试通过 API 直接启动 `new_chapter`，违反真实 UI 主动作约束

- 位置：`frontend/tests/chapter-workflow.spec.ts:83-89`。
- 该测试通过 `POST /api/chapters/{id}/runs` 创建 `new_chapter` 运行，而不是打开章节工作区、填写意图并点击 `btn-start-chapter-planning`。项目 brief 明确 API 只能用于 fixture 建立或最终断言读取；因此该测试不能证明 UI 规划入口可用。
- 应将规划启动改为 UI 动作，API 仅用于读取 workflow/run 断言；项目/卷/章节创建仍可保留为 fixture 建立。

### I2. 未验证“计划未接受前不得创建场景运行”及阻断状态对 UI 的实际阻止

- 位置：`frontend/tests/chapter-workflow.spec.ts:161-180`、`198-205`。
- 测试只检查计划候选和“接受计划后”提示，然后直接接受计划；没有在接受前断言 `workflow.scenes`/场景 run 数为空，也没有尝试对第二场在第一场接受前点击审校并断言 UI 没有调用创建运行。
- 计划接受后只断言第二场 `blocking_reasons` 为空（`200`），这覆盖的是解阻后的状态，未覆盖 brief 要求的阻断状态必须阻止 UI 发起运行。应增加接受前的服务端空队列断言、阻断时点击动作及请求未发出的断言，再验证前置场景接受后解阻。

### I3. Canon 断言没有证明 `ChapterRevision` 来源和章节级作用域

- 位置：`frontend/tests/chapter-workflow.spec.ts:232-258`。
- 测试断言了 accepted revision 指针和三种候选终态，但没有把 `/canon-candidates` 的 `source_revision_id` 与 accepted `ChapterRevision` id 比较，也没有断言每个候选 `scope === "chapter"`、`scene_id === null` 或面板显示的 source identity 对应该版本。只断言正式 Canon 包含“林默”不能排除场景级/历史候选串入。
- 应显式断言 Canon 快照的 source revision、run status、候选作用域和候选来源版本；同时确认章节级确认才改变 Story Bible。

### I4. TDD RED 证据无效：记录的是环境启动错误，不是缺失旅程的行为失败

- 位置：`codex-handoff/2026-08-07-chapter-workbench-task10-report.md:13-25`。
- 报告把 `Error: spawn EPERM`（发生在 Playwright 启动浏览器/webServer 之前）标为 TDD RED。该输出没有执行新增断言，不能证明“先有失败测试，再补最小 fixture/test 实现”；也没有提供实现前的失败断言或提交前后差异证据。
- 报告可以如实记录环境阻断，但应将其标为“无法取得 RED/GREEN 运行证据”，并在可启动浏览器的环境补跑真实失败/通过链路后再宣称 TDD 验收。

### I5. 新 fixture 的重试幂等性和身份 fencing 不完整

- 位置：`backend/app/db/e2e_fixtures.py:185-195`、`465-505`。
- `seed_plan_candidate` 虽由领域函数按 `source_run_id` 复用候选，但每次重试仍无条件把运行 fencing token 加一并追加 `run_waiting_feedback` 事件。`seed_chapter_review` 每次调用都会新建 staged `ChapterRevision`、递增 token 并追加事件，完全没有按 `run_id` 找回已有 revision。进程在 commit 后重试会留下多条 revision/事件，改变 `history[0]`、事件序号和后续断言结果。
- fixture 应按 run identity 做显式 get-or-create，并在状态/当前 token 上执行一致性检查；重复调用应返回同一 revision/候选且不重复推进事件。至少应增加重复调用测试，验证事件序号、revision 数量和 run 状态稳定。

## Minor

### M1. 失败诊断未按 brief 输出关键运行上下文

- 位置：`frontend/tests/chapter-workflow.spec.ts` 全文。
- 测试没有 `testInfo`/失败钩子或统一断言包装来输出 phase、run id、pending decision、最后事件序号和未消费 outbox。Playwright 失败时通常只会显示 locator/期望值，无法满足 brief 的故障诊断要求。
- 应在关键轮询超时和 fixture 异常时读取 workflow/run 快照并附加上述字段；outbox 可通过测试库只读查询或现有诊断接口取得。

### M2. 运行 ID 读取缺少非空断言

- 位置：`frontend/tests/chapter-workflow.spec.ts:60-62`、`181`、`205`。
- `sceneRunId` 在 `data-full-run-id` 缺失时返回空字符串，随后把空值传给 fixture CLI，错误会表现为数据库“run not found”，掩盖真正的 UI/run-panel 渲染问题。应对属性使用 `expect(...).toMatch(...)` 后再调用 fixture。

## 验证范围

- 已阅读 Task 10 brief、实施报告、Playwright 测试、E2E fixture、章节 Canon API/服务、章节接受 outbox 消费者、Story Bible 面板和 Playwright 配置。
- 静态检查确认 `frontend/npm run typecheck` 与 `playwright --list` 在实施报告中有记录；报告同时明确真实 Playwright 被 `spawn EPERM` 阻断，本文未将其视为浏览器通过。

## Approval Verdict

**不批准（Request changes）。**

至少修复 C1，并补齐 I1-I5 中的 UI 触发、阻断/来源断言和 fixture 重试语义；在允许浏览器/子进程启动的环境重新取得真实 Playwright 结果后再验收。
