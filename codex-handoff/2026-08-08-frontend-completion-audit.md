# 前端章节工作台完成度审计

审计范围：设计文档第 7 至第 11 节、当前前端章节工作台、相关 API 类型和 Playwright 测试。
本次不修改生产代码。当前实跑章节相关 Playwright：5 passed。

## 结论

当前实现已经具备章节意图入口、workflow 快照读取、候选计划接受、场景队列顺序阻断、章节审校和章节 Canon 的可操作骨架；旧的 POST /api/chapters/{chapter_id}/plan 生产路由也已删除。

但不能据此宣称第 7 至第 11 节全部完成。最重要的未闭环是：章节级 active_run 没有在章节工作区显示运行面板或恢复动作；Planner 多轮反馈目前只有客户端 payload 测试，没有真实后端多轮、lineage、未确认建议阻断的 Playwright 证据；选中场景会替换掉章节工作区主视图；来源/确认状态、阶段进度和移动端抽屉布局没有按设计落地。

## 已证实

### 1. 自然语言意图入口和新规划命令

- frontend/src/app/page.tsx:582-600 只允许非空意图启动规划，并发送 run_scope=chapter、request_type=new_chapter、decision_target=plan 和 chapter_intent.text。
- frontend/tests/chapter-workflow.spec.ts:94-140 通过章节工作区 UI 填写自然语言意图，断言请求字段和 workflow.intent.text。
- 本次命令：npx playwright test tests/chapter-workflow.spec.ts tests/planner-feedback.spec.ts tests/chapter-feedback.spec.ts --reporter=list
- 结果：5 passed。

### 2. 章节主状态使用 workflow 读取

- frontend/src/app/page.tsx:552-568 章节入口读取 getChapterWorkflow，阶段、计划、讨论、场景队列和章节版本均来自快照。
- frontend/src/types/index.ts:159-220 定义了包含 phase、pending_decision、plan_discussion、plan、scenes、chapter_revision、active_run、blocking_reasons 和 Canon 来源的快照类型。
- frontend/src/app/page.tsx:697-703 在章节 active run 期间刷新 workflow，而不是自行按“最新记录”推断阶段。

### 3. 计划接受、场景顺序、章节审校和 Canon 主旅程

- frontend/tests/chapter-workflow.spec.ts:175-354 通过 UI 启动规划、接受候选计划、验证计划未接受前没有场景、验证第二场在第一场接受前被阻断、逐场接受、启动章节审校、接受章节版本、启动章节 Canon 并确认/拒绝/暂缓候选。
- 该测试证明了主动作和后端状态边界的一部分，但使用 fixture/API 注入候选计划、运行状态和 Canon 候选，不能替代真实 Worker 重启/事件重放证明。

### 4. 旧初始化 POST 已删除

- backend/app/api/chapters.py 只有 GET plan、GET workflow、场景创建和 rollback 等路由，没有旧 POST plan 路由。
- backend/tests/api/test_chapter_plan_init.py:8-27 将 POST plan 断言为 405，GET plan 保留为只读别名。
- rg 未发现 createChapterPlan 或生产代码中的 /api/chapters/{chapter_id}/plan POST 调用；frontend/tests/editor.spec.ts:486-498 只守护 GET plan 不被调用。

## 缺口

### P1. 章节级运行进度、澄清、暂停恢复没有真正接入章节工作区

证据：

- frontend/src/app/page.tsx:1668-1679 的 RunPanel 只在 selectedScene && activeRun 分支渲染。
- frontend/src/app/page.tsx:1472-1623 的章节工作区分支没有章节 active_run 的运行时间线、事件详情、暂停恢复、取消或章节级运行反馈控件。
- frontend/src/app/page.tsx:697-703 只有定时读取 workflow，没有把章节 active run 接入现有 SSE/RunPanel 状态。

影响：

设计文档第 7.1 的底部运行时间线、第 7.3 的 planning/失败/暂停恢复规则，以及第 9.1 第 2、6 项无法由当前 UI 证明。规划运行处于 queued、running、pending_clarification、paused 时，作者没有完整的章节级可读进度和恢复入口。

建议测试：

从章节工作区启动规划，等待真实 workflow active_run，断开或重启 Worker，验证事件序号重放、阶段恢复、澄清问题和 resume/cancel 都能在章节工作区完成。

### P1. Planner 多轮反馈只有 payload/mock 证据，没有真实端到端闭环

已实现的客户端 payload：

- frontend/src/app/page.tsx:608-660 收集问题答案、提案 accept/modify/reject 和反馈正文，提交 target=plan 的决策。

现有测试限制：

- frontend/tests/planner-feedback.spec.ts:68-96 用 page.route mock workflow 和 decisions，只验证请求体结构。
- frontend/tests/chapter-workflow.spec.ts:175-220 通过 fixture 直接播种候选计划，没有从 Planner 问题进入回答、子运行、下一轮候选的 UI 旅程。
- 没有 Playwright 断言 parent_run_id、supersedes_run_id、parent_plan_revision_id、问题/建议服务端 ID 延续、未确认建议阻断 accepted plan。

影响：

设计文档第 9.1 第 3 项、第 9.2 第 748、750 条，以及完成定义第 1、2、9 条仍缺少真实前后端证据。当前只能证明前端会组装 feedback，不能证明回答真正进入下一轮 Planner 或形成可追溯候选 lineage。

建议测试：

使用确定性 Fake provider，通过 UI 完成“意图 -> needs_clarification -> 回答问题 -> 新候选 -> 修改/拒绝建议 -> 接受计划”，并通过 workflow/API 断言同一规划血缘和候选来源。

### P1. 选中场景会替换章节工作区，主流程上下文不保持

证据：

- frontend/src/app/page.tsx:1373-1471 使用 selectedScene ? 渲染完整场景编辑器。
- frontend/src/app/page.tsx:1472-1623 只有 selectedScene 为空且 selectedChapterId 存在时才渲染章节工作区。

影响：

设计文档第 7.1 第 612 行要求“进入某个场景只是定位到章节队列中的一个执行单元，不改变主流程上下文和阶段”。当前选中场景后，阶段、Planner 讨论、场景队列、章节阻塞和章节决策栏从主视图消失，无法同时保持章节上下文。

建议：

将场景编辑改为章节工作区内的执行单元视图或抽屉，保留章节 phase、queue、DecisionBar；增加测试验证点击场景后章节阶段和队列仍可见。

### P2. 7.1/7.2 的阶段进度、上下文栏和决策栏未按设计形成独立 UI

证据：

- frontend/src/app/page.tsx:1122-1135 只有单个 phaseLabel 映射。
- frontend/src/app/page.tsx:1543-1546 仅显示“章节工作台 + 当前阶段”，没有已完成阶段、阻塞阶段和下一步主动作进度条。
- frontend/src/app/page.tsx:1473-1621 将计划、讨论、场景队列、影响范围和章节历史都堆在中间区域；章节右侧在 frontend/src/app/page.tsx:1631-1647 主要只有 Story Bible。
- 未发现 ChapterWorkspace、WorkflowPhaseBar、DecisionBar、ChapterContextRail 等独立边界；页面仍由一个 page.tsx 承载主要工作流。

影响：

功能骨架可用，但与设计文档第 7.1、7.2 的信息架构和边界不一致；主动作层级、上下文折叠和运行时间线无法独立验证。

### P2. 来源/确认状态展示不完整，且仍有原始 JSON 暴露

证据：

- frontend/src/app/page.tsx:1587-1609 的提案只展示 field_path/value 和 accept/modify/reject 按钮，没有显示 source、rationale、当前确认状态或作者决策结果。
- frontend/src/app/page.tsx:1618-1619 的讨论和候选计划只显示文本/brief 字段，没有逐字段展示 provenance 和“AI 建议/作者已确认/尚未确定”。
- frontend/src/app/page.tsx:1391-1395 直接用 JSON.stringify(planScene.brief) 展示场景简报。
- frontend/src/app/page.tsx:1748-1751 用 pre JSON.stringify(selectedSceneBrief, null, 2) 展示场景设定。

影响：

违反设计文档第 7.1 第 614 行、第 7.2 第 620-621 行、第 7.3 第 650-657 行的自然语言优先、来源透明和默认不显示 JSON 约束。当前测试没有覆盖这些视觉/语义要求。

### P2. 响应式布局未达到设计约束

证据：

- frontend/src/app/globals.css:59-80 固定三栏 300px 1fr 340px。
- frontend/src/app/globals.css:275-279 的移动端媒体查询只调整 Canon 指标为单列，没有把资源导航/上下文栏变成可关闭抽屉，也没有保持决策栏固定底部。
- 未发现移动 viewport 的章节工作台 Playwright 测试。

影响：

设计文档第 7.1 第 614 行的中等宽度抽屉、移动端单列和底部决策栏要求没有证据，窄屏可能出现三栏挤压和主操作不可用。

### P2. 场景队列 UI 没有完整展示当前执行项和阻塞原因

证据：

- frontend/src/app/page.tsx:1619-1620 只渲染场景标题和 status。
- workflow 类型虽然包含 accepted_revision_id、current_run_id、blocking_reasons（frontend/src/types/index.ts:190），但这些字段没有在队列 UI 展示。
- chapter-workflow.spec.ts:236-244 通过 API 断言 previous_scene_not_accepted，没有断言章节工作区显示该阻断原因。

影响：

不满足设计文档第 7.2 第 624 行“状态、版本、阻塞原因和当前执行项”及第 7.3 第 658 行“失败/阻塞显示受影响对象和下一步操作”的可见性要求。

## 验证边界

本次实跑：

- npx playwright test tests/chapter-workflow.spec.ts tests/planner-feedback.spec.ts tests/chapter-feedback.spec.ts --reporter=list
- 结果：5 passed。

这 5 条足以证明章节入口、意图请求、workflow 展示、计划接受后的顺序阻断、章节审校/Canon 主动作和结构化反馈 payload 的局部行为；不能证明真实 Planner 多轮、章节 active_run UI、Worker 重启重放、移动端布局或来源确认展示。

## 建议的完成门禁

1. 增加真实 Fake provider 的 Planner 多轮 Playwright：问题回答、建议决策、子运行 lineage、候选版本和 accepted 阻断。
2. 把章节 active_run 接入章节工作区的进度/事件/恢复控件，并增加 Worker 重启与 Last-Event-ID 重放测试。
3. 保持章节工作区作为主上下文，场景只作为执行单元定位，不替换章节 phase/queue/DecisionBar。
4. 展示 plan field provenance、AI/作者确认状态、场景 current run/blocking reasons，并移除默认 JSON 展示。
5. 增加桌面/平板/移动 viewport 的章节工作台验收，验证抽屉、单列和底部决策栏。
