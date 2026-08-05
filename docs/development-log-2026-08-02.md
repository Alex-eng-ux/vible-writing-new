# 2026-08-02 开发日志

## 工作性质与范围

本轮在复核旧评审对计划书提出的 P0/P1 阻断项后继续做文档级收口。范围覆盖首稿版本路径、人工 ChangeSet 身份、Task 2/4B 所有权、运行恢复 CAS、单用户私有部署边界、非空库迁移/备份恢复和真实模型 smoke；同步更新主计划与 Agent Prompt 契约。用户要求保持原有业务工作流逻辑，不把技术包装层变成新的业务节点。当前工作区仍未发现应用代码、数据库迁移、API 实现或自动化测试。

## 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 不改变原有业务工作流，只补齐 ID 管理和契约 | 保留作者按章节推进的交互节奏，避免 ID、Hook 和恢复机制改变业务语义 | 主流程仍为“章节规划 → 场景循环 → 章节聚合 → 章节审校 → 作者接受 → Canon” |
| Agent 和 Hook 不创建正式业务 ID | 防止模型伪造、重复或越权创建实体 | Agent 只能引用已有正式 ID，或返回 `local_key`、`client_key`、文本定位；正式 ID 由运行时步骤/领域服务归一化 |
| 所有 Agent 输出先经过统一结果路由 | 统一处理 `ready`、`needs_clarification`、作者等待和提交分支 | `AgentResultRouter` 在下游 Hook、候选持久化、ID 归一化和提交之前作为状态闸门 |
| `needs_clarification` 必须暂停并从原节点恢复 | 信息不足时不能猜测或继续提交 | 保存 `pending_node`、`clarification_questions`、checkpoint 和等待事件；恢复时重新经过反馈、上下文、专属 before Hook、预算、脱敏和 Trace 生命周期 |
| 专属 Hook 必须有明确调用点 | 仅列职责会导致实现时遗漏顺序或错误放行 | 计划书增加 Agent 节点与执行前/执行后 Hook 矩阵，Prompt 契约同步恢复入口和 Hook 顺序 |
| 章节规划澄清恢复键改名 | `chapter_plan_review` 容易和作者审阅业务节点混淆 | 统一改为 `chapter_plan_clarification` |
| Markdown 表格中的枚举竖线必须转义 | 未转义的 `|` 被渲染器当成列分隔符，造成表格内容被拆列 | `draft\|continue\|rewrite`、`scope=chapter\|scene` 已在表格中转义 |
| 工作流状态接口补齐版本、Canon 和决策字段 | 多场景聚合、三类 Canon 候选和作者决策不能只靠隐式查询或单一事实列表 | 增加场景接受版本映射、暂存/接受章节版本、时间线/剧情线候选、`decision_target` 和按场景自动修订计数，并写明状态不变量 |
| 用户要求对自查发现的接口缺口进行修补 | 接口契约不能只覆盖字段，还必须覆盖状态路由、暂停恢复和类型归属 | 计划书与 Prompt 契约同步补充 Agent 状态映射、`paused` 状态、Router 结果信封、恢复不变量，并保持原业务流程不变 |
| 用户要求明确 `scene` 与 `scenes` 的术语边界 | 避免单个实体、场景 ID 列表和 API 集合路径被误认为重复对象 | 在计划书、Prompt 契约和日志中统一说明 `Scene`、`scene_id`、`scene_ids`、`affected_scene_ids` 与 `scenes` 的含义 |
| 用户要求先补日志再修改接口说明 | 保留本轮审查发现、修改顺序和未修补状态的可追溯记录 | 先记录后半段契约存在的结构混杂、`paused` 边界、重试计数范围和 `target` 映射歧义，再修改计划书与 Prompt |
| 用户要求解释 ID 管理与 Agent 执行包装层中的多种名称 | 区分真正的别名、独立标识、临时关联键和流程图节点，避免把名称数量误解为重复分配 ID 或新增业务流程 | 明确 `generation_run_id` 是唯一权威运行 ID；`run_id`、`thread_id` 只是兼容别名；`agent_run_id`、`source_id`、`client_key`、`local_key` 各自承担独立职责；`O/O2` 只是同一 Router 的图示节点，不是业务 ID |
| 用户要求先自查实施计划，再修改计划书 | 先确认依赖、里程碑和验收缺口，再把修订集中到计划契约，不提前实现应用代码 | 形成实施计划自查结论，并按阻断项修订计划书和 Prompt 契约 |
| 用户要求先修改再压缩 | 在压缩前保留本轮计划修订和未实现状态的可追溯记录 | 本日志先更新；计划书已补充可执行切片、接口和验收门槛 |
| 实施任务按可独立测试的切片交付 | 原 Task 4/5/7 范围过大，且 M1-M3 依赖边界不成立 | 新增 `Task 4A/4B/4C`、`Task 5A/5B/5C`、`Task 7A/7B/7C` 依赖表和每片出口证据 |
| 运行 API 与资源 API 分离错误语义 | 资源创建尚未有运行 ID，不能套用运行错误格式；重试还必须有版本基线和幂等键 | 资源错误固定 `run_id=null`；运行错误返回实际运行别名；所有变更请求使用 `Idempotency-Key`，章节运行增加逐场景基线映射 |
| 用户要求对旧评审指出的 P0/P1 契约断点进行收口 | 计划书必须在交给 AI 前消除首稿、人工编辑、任务所有权和恢复入口的猜测空间 | 新增 `SceneDraftArtifact`/`commit_scene_draft`、`ManualChangeSetContext`、Task 2/4B 所有权边界、resume CAS 和 RC 专项门槛 |
| 首稿必须有独立的可审阅版本路径 | 首次生成没有已接受基线，不能伪装成无基线 `ChangeSet` | `WritingAgent -> SceneDraftArtifact -> commit_scene_draft -> SceneRevision`；首稿反馈回到 WritingAgent，首稿取消或替换只丢弃 artifact |
| 人工编辑不得伪造 Agent 运行身份 | M1 不依赖 LangGraph，但正文修改仍需要可审计、可幂等的命令身份 | Agent 使用 `generation_run_id`，人工 ChangeSet 使用服务端 `manual_command_id`，两者恰好一个非空 |
| V1 的部署和发布边界必须可验证 | 单用户配置不能被误报成通用认证；Fake model 不能代替真实 provider 或升级恢复证据 | 固定 `DEPLOYMENT_MODE=single_user_private`、`API_BIND_SCOPE`、保留期、非空库 migration、备份恢复和 `SKIPPED_PROVIDER_SMOKE` 规则 |
| 局部 Canon 必须有独立入口，但不能改变审校语义 | 已接受场景需要单独确认三类候选，且场景确认不能更新全局 Canon | 增加 `canon-runs` 和 `Task 5C`；以 `request_type=review + decision_target=canon` 路由到 CanonAgent，不调用 WritingAgent |
| 使用确定性 Fake model 和固定 fixture 作为验收基线 | 没有可重复模型输出时，重启、SSE 重放、版本冲突和 Canon 事务无法形成稳定断言 | Task 1 提供 `FakeModelProvider`；Task 9 增加 fixture、重置脚本、十次反馈记录和冻结产物 |
| 直接建设完整 V1 工程，不以 MVP 或市场试点作为交付目标 | 用户接受较大系统的长期建设成本，但仍需要可控的实施节奏和独立验收出口 | 计划书改为 V1 工程交付计划；保留分阶段切片，将 V1-M0 至 V1-RC 定义为内部工程里程碑，不改变既定作者工作流 |
| 将业务边界规范化为 AI 可直接执行的显式状态和对象 | 计划书将直接交给 AI 实现，隐含语义会导致 AI 自行猜测并引发数据模型、接口和恢复逻辑返工 | 增加 `ChapterHandoff`、`ChapterAggregationEligibility`、`chapter_sync_status`、显式重规划继承字段、跨章节入口字段和稳定阻断错误码，并同步 Prompt 契约 |
| 用户要求针对另一轮评审发现的阻断项进行收口 | 仅有架构描述仍可能让实现模型在首稿提交、人工命令身份、幂等重放和恢复 CAS 上自行分叉 | 增加 `SceneDraftArtifact`/`commit_scene_draft`、`ManualChangeSetContext`/`manual_command_id`、`CommandIdempotencyRecord`/请求指纹、终态候选清理、`expected_run_version` 恢复 CAS、Task 2/4B 所有权边界和 `single_user_private` 部署门槛；同步计划书与 Prompt 契约 |
| 本次收口继续保持原有业务工作流不变 | 剩余问题属于实现时的并发、版本和传输契约，不能借收口之名新增业务节点或改变作者决策顺序 | 仅补充租约 fencing/续租、计划当前/接受指针、章节 Canon 专用入口、Outbox 字段、候选来源迁移、Agent 重试身份和 Compose proxy 边界 |

## 关键规则与取舍

- 每次 Agent 调用的调用前顺序为 `ContextHook -> Agent 专属 before Hook -> BudgetHook -> RedactionHook（输入副本） -> TraceHook.start -> Agent`。
- 结果后先执行 `SchemaHook -> AgentResultRouter`；Agent 原始状态按 Agent 语义映射，非澄清且允许继续/等待作者时执行 Agent 专属 after Hook。`needs_clarification` 跳过所有下游业务 Hook、候选持久化、ID 归一化和提交。
- `ContinuityAgent` 的 `pass|issues` 继续进入 `ReviewAgent`，`needs_author_confirmation` 进入作者等待；`ChapterReviewAgent` 的 `pass|issues|author_review` 进入章节作者审阅；`pause|failed` 由 `ErrorHook` 处理，不是 Agent 原始状态。
- 运行状态增加 `paused`，暂停必须记录 `pause_reason`、`last_error_code` 和可恢复的 `pending_node`；技术重试使用 `retry_count`，不与自动修订计数混用。
- `RevisionAgent` 与 Writing 分支不同：修订结果必须先经过 `ChangeSetHook -> apply_change_set（临时应用）`，再重新进入 `FactExtractionHook`、规则检查、ContinuityAgent 和 ReviewAgent。
- `FactExtractionHook` 只做确定性规范化、声明哈希、去重和证据合并，不调用模型、不创建 ID、不直接写库；通过规则、引用和 ID 检查后才由 `FactCandidateService` 幂等保存。
- `CanonAgent` 的事实、时间线事件和剧情线候选分别保存在 `fact_candidates`、`timeline_event_candidates` 和 `plot_thread_updates`；作者决策使用 `candidate_type + local_key` 定位。
- 正式提交路径固定为 `规范化业务结果 -> CommitGuardHook -> Domain Service`。取消是逻辑丢弃，不删除已提交版本；回滚必须显式指定目标版本并记录作者决策。
- `ChapterRunState` 中 `scene_id` 在章节规划阶段允许为 `None`，但场景级运行、场景 Canon 和场景提交必须有正式场景 ID；章节版本和场景接受版本不得用草稿或未提交版本填充。
- 自动低风险修订以 `scene_auto_revision_counts[scene_id]` 按场景、按检查回合限制一次；`auto_revision_count` 只记录本次运行累计次数。
- 输入脱敏失败不得调用外部模型；输出脱敏失败不得发送未脱敏观测副本。Trace/LangSmith 不可用不得触发业务重试，安全校验使用 fail-closed。
- V1 采用“完整架构 + 分阶段工程交付”：阶段切片用于隔离依赖、测试和回滚，不代表独立产品版本、市场 MVP 或逐阶段重新验证产品方向。
- 场景接受、章节接受、章节聚合、重规划继承和跨章节承接必须通过显式版本、状态、handoff 和阻断码表达；不得读取“当前最新版本”或用标题/位置隐式推断继承。
- ID 命名按层次区分：`generation_run_id` 是一次任务的权威运行身份；`run_id` 和 `thread_id` 不单独分配；`agent_run_id` 用于区分一次运行内的 Agent 调用；`source_id` 标识上下文来源；`client_key`/`local_key` 只用于临时关联，不能直接作为正式业务 ID。
- 运行包装图中的 `R/M/C/A/S/F/V/I/Q` 是 Mermaid 节点标签，`O` 与 `O2` 表示同一 `AgentResultRouter` 在状态闸门和后续路由阶段的两次调用，不代表新增工作流节点或新增身份。
- `WritingAgent` 的完整正文先进入 `SceneDraftArtifact`；首稿反馈回到 `WritingAgent` 替换草稿，作者接受后由 `commit_scene_draft` 创建根或子级 `SceneRevision`。`RevisionAgent` 只处理已有场景版本基线的 `ChangeSet`。
- `ChapterRunState` 只保存 `draft_artifact_id`；Prompt 的 `draft_text` 由 `ContextAssembler` 临时加载，不写入 checkpoint，也不能作为已接受正文来源。
- Agent/Review ChangeSet 使用 `generation_run_id`，作者手工 ChangeSet 使用服务端生成的 `manual_command_id`；二者恰好一个非空，人工请求的 `run_id` 固定为 `null`。
- 所有写请求先按作用域、操作名、幂等键和请求指纹查 `CommandIdempotencyRecord`；同键同指纹重放首次结果，同键不同指纹返回 `IDEMPOTENCY_KEY_REUSE`，只有新键才做版本 CAS。
- `cancelled|failed|superseded` 运行的未决候选统一转为 `discarded`，未接受草稿转为 `discarded|superseded`，终态来源不得继续 Canon 或接受。
- V1 固定为 `DEPLOYMENT_MODE=single_user_private`，API 仅允许 loopback 或 compose 私有网络，`actor_id` 由服务端配置解析；Prompt 中的 `run_version` 和幂等字段仅属于 runtime/API transport，不进入模型输出。
- `RunLease` 使用单调递增 `fencing_token` 防止旧 worker 迟到写入；worker 必须支持 `renew/heartbeat`，checkpoint、事件、候选、版本和决策写入均校验 fencing token，失租统一返回 `RUN_LEASE_LOST`。
- 计划版本使用 `Chapter.current_plan_revision_id` 与 `Chapter.accepted_plan_revision_id` 两个显式指针；计划创建和作者接受均通过 CAS，运行创建必须携带 `plan_revision_id`，场景循环只允许使用 accepted 指针。
- 章节 Canon 与场景 Canon 使用两个专用入口；章节接受只写 `chapter_revision.accepted` outbox 事件，由同一 CanonRunService 幂等自动入队或通过专用入口补偿，不在提交事务内直接调用 Agent。
- Canon 候选必须包含来源段落、故事内有效时间、叙事认识状态和重复处理建议；首稿候选先绑定 `source_draft_artifact_id`，草稿物化时在同一事务迁移到 `source_revision_id`，取消/替换则丢弃。
- 同一 checkpoint 的 Agent 技术重试复用 `agent_run_id`/`agent_attempt_key` 并递增 `attempt_no`；作者反馈或新的逻辑节点调用才创建新身份。Outbox 具备投递状态、重试字段、producer command 关联和消费者游标。
- Compose 模式使用 `API_BIND_SCOPE=compose_private` 和 Next.js server-side proxy；浏览器只访问 frontend，宿主机不发布 API/PostgreSQL 端口，本机进程模式保留 loopback 直连。
- 空场景人工根编辑与 Agent 首稿共用显式根草稿规则：空文档是唯一人工根基线，先生成 `SceneDraftArtifact`，再由提交接口物化为根 `SceneRevision`；Agent/Review 不得使用空基线。
- 人工根编辑的空文档固定为规范化 ProseMirror JSON `{ "type": "doc", "content": [] }`，键按字典序、无额外空白后计算 SHA-256，避免实现模型各自选择基线格式。

## 已完成产出

- 更新 [计划书](../superpowers/plans/2026-07-31-novel-writing-studio-plan.md)：补充 Hook 生命周期、专属 Hook 调用点矩阵、失败策略、恢复映射、修订补丁顺序和主流程图省略 Hook 的说明。
- 更新 [Agent Prompt 契约](../superpowers/specs/2026-07-31-agent-prompts-v1-draft.md)：同步专属 Hook 调用点、`chapter_plan_clarification`、`apply_change_set` 后复检、候选幂等键和作者反馈规则。
- 修复调用点表的 Markdown 结构，转义表格单元格内的枚举竖线。
- 补充 `ChapterRunState`：`accepted_scene_revision_ids`、`staged_chapter_revision_id`、`accepted_chapter_revision_id`、`timeline_event_candidates`、`plot_thread_updates`、`scene_auto_revision_counts`、`decision_target`。
- 补充 `ChapterRunState` 的场景、章节版本、Canon、澄清恢复、作者决策和自动修订状态不变量。
- 补充固定接口类型归属，包括 `ChapterContract`、`SceneBrief`、`ReviewIssue`、`AuthorFeedback` 等，并说明状态中的 `dict` 只是 JSON 序列化边界。
- 补充 `AgentResultEnvelope`、`AgentStatus`、`RouterOutcome` 和 `CandidateDecision` 的类型归属，明确 Agent 原始状态与运行路由状态分离。
- 补充场景运行分发：`scene + continue|rewrite` 仍调用 `WritingAgent`，只有 `review` 分支绕过 WritingAgent；补充 `paused`、版本来源、队列索引、恢复清理和章节 Canon 来源不变量。
- 补充 `scene`/`scenes` 术语说明，明确实体、ID 列表、受影响子集和 API 集合路径不重复。
- 重排计划书中运行身份、`ChapterRunState` 和 Agent 路由的说明，使三段分别只描述上下文加载、状态字段和结果路由。
- 重组后半段接口说明：状态不变量按场景版本、Canon 来源、暂停恢复、预算修订分组；序列化边界、结果路由、恢复入口和类型归属分别独立说明。
- 明确 `paused` 只表示可恢复暂停，不可恢复错误进入 `failed`；明确 API `target` 到 `decision_target` 的规范化，以及累计 `retry_count` 与单节点预算的边界。
- 补充 ID 别名说明：把 `run_id`、`thread_id` 与 `generation_run_id` 的关系，以及 `agent_run_id`、`source_id`、`client_key`、`local_key` 和流程图节点标签的区别写入本日志，确认这些名称不会引入第二套运行 ID 或改变主流程。
- 修订[实施计划](../superpowers/plans/2026-07-31-novel-writing-studio-plan.md)：新增任务依赖与交付切片；补齐 Python/Node 依赖锁定、Alembic 入口、统一 `ErrorEnvelope`、Fake model、资源命令、提交守卫、`RunEvent`、逐场景版本基线、SSE 重放和 `paused` resume。
- 调整[实施计划](../superpowers/plans/2026-07-31-novel-writing-studio-plan.md)的交付定位：标题、范围、验收和完成标准统一改为 V1 工程；M0-M4 改为 V1-M0 至 V1-M3 与 V1-RC，Task 9 fixture 改用 `v1-*` 命名，并明确工程验收不等同于正式 GA 或市场验证。
- 规范[实施计划](../superpowers/plans/2026-07-31-novel-writing-studio-plan.md)和[Agent Prompt 契约](../superpowers/specs/2026-07-31-agent-prompts-v1-draft.md)的业务边界：增加场景/章节接受头分离、`ChapterAggregationEligibility`、`ChapterHandoff`、章节 `in_sync|out_of_sync`、重规划父运行与场景继承映射、非首章入口 handoff 和稳定错误码。
- 修订实施任务边界：Task 4 拆为单场景图、章节编排和 Canon 路由；Task 5 拆为资源/手工编辑、运行/SSE 和 Canon API；Task 7 拆为编辑器、运行反馈和 Canon 决策 UI。
- 补齐三类 Canon 候选的统一持久化/事务语义，移除可绕过作者决策的公开事实提升入口；章节 Canon 必须使用 `accepted_chapter_revision_id`，场景 Canon 必须使用 `accepted_scene_revision_id`。
- 补齐资源创建、作者手工 ChangeSet、局部 Canon、章节 `pov`、逐场景 `scene_base_revision_ids`、幂等键、SSE 事件序号/重放/heartbeat 和技术暂停恢复接口。
- 同步[Agent Prompt 契约](../superpowers/specs/2026-07-31-agent-prompts-v1-draft.md)：增加 `accepted_chapter_revision_id` 及其章节 Canon 来源规则。
- 更新本日志，保留用户主导的决策、具体取舍和当前未实现事项。
- 收口另一轮计划评审发现的首稿提交、人工编辑上下文、Task 2/4B 所有权、resume CAS、Task 5B 入队冲突、认证/部署边界和 RC 证据缺口。
- 计划书新增 `SceneDraftArtifact`、`persist_scene_draft`、`commit_scene_draft`、`ManualChangeSetContext`、`manual_command_id`、`CommandIdempotencyRecord`、请求指纹和 `IDEMPOTENCY_KEY_REUSE`；Prompt 契约同步首稿反馈路由、终态来源清理和 runtime-only CAS 说明。
- Task 2 收窄为持久化 schema/领域原语，Task 4B 明确拥有首稿/补丁接受路由、聚合资格、章节聚合和 `ChapterHandoff` 创建；Task 5B 删除“启动图”冲突，只保留“校验后入队”。
- Task 9 新增非空库迁移、备份恢复、真实模型 smoke、部署绑定和保留策略验收门槛。
- 进一步补齐 M1 空场景人工根 ChangeSet：`base_scene_revision_id=null` 仅对人工 `prosemirror_step` 根编辑开放，提交前先进入 `SceneDraftArtifact`。
- 继续修订计划书：补齐 `RunExecutor` 的 `RunLease`/fencing/续租接口、`RunEventConsumerCursor`、章节计划接受 API、章节级 Canon API、运行创建请求的 `plan_revision_id`/`decision_target`、Compose `INTERNAL_API_BASE_URL` 和代理连通性验收。
- 同步修订 Agent Prompt：补齐 `ChapterContract.pov`、`SceneBrief.forbidden_beats`、Canon 候选的 `source/effective_story_time/narrative_knowledge/resolution_action` 字段、首稿候选来源迁移和稳定 `agent_attempt_key` 重试规则。

## 验证结果

- 已重新读取计划书、Prompt 契约和本日志，UTF-8 正常，未发现替换字符。
- 计划书代码围栏为 34 个，Prompt 契约为 32 个，本日志无代码围栏不配对问题。
- 调用点表 9 行均保持正确的三列结构，每行有 4 个未转义表格分隔符；`draft|continue|rewrite` 和 `scope=chapter|scene` 不再拆列。
- 已验证旧恢复键 `chapter_plan_review` 无残留，新键 `chapter_plan_clarification` 在计划书和 Prompt 契约中一致。
- 已验证 `RevisionAgent -> ChangeSetHook -> apply_change_set -> FactExtractionHook` 顺序在计划书和 Prompt 契约中一致。
- 已验证 `ChapterRunState` 新字段、状态不变量、固定类型归属和自动修订计数规则均已写入，且旧的全局 `auto_revision_count=0` 条件已清除。
- 已验证 Agent 状态映射不再假设所有结果都是 `ready`，`ContinuityAgent`/`ChapterReviewAgent` 的专属状态与 Router 规则已在计划书和 Prompt 契约中同步。
- 已验证 `run_status=paused`、`pause_reason`、`last_error_code`、`retry_count`、`AgentResultEnvelope`、`RouterOutcome` 和场景级分发规则已写入；旧 `chapter_plan_review` 无残留。
- 已验证 `Scene` 术语说明已同步出现在计划书、Prompt 契约和本日志中，且没有新增 `scenes` 状态字段或 ID 类型。
- 已验证计划书三段说明已拆分为“运行身份与上下文”“`ChapterRunState` 的职责与字段”“Agent 输出与路由”，Prompt 契约同步使用两层状态模型。
- 已验证后半段说明已拆分为状态不变量、Checkpoint 序列化边界、结果路由与恢复契约、类型归属四个独立部分，计划书与 Prompt 契约的 `paused`、`target` 和 `retry_count` 规则一致。
- 已交叉检查澄清暂停/恢复、提交守卫、候选持久化闸门、Canon 作用域、取消/回滚和主流程/AUX 隔离说明。
- 已核对 ID 管理与执行包装层：确认只有 `run_id`、`thread_id` 是 `generation_run_id` 的别名；`agent_run_id`、`source_id` 和临时关联键不是别名；`O/O2` 不是运行时字段。
- 已验证旧的实施计划冲突表述无残留：M1 不再依赖未定义的“Task 7 基础部分”，M3 已包含 `Task 5C`，Canon API 不再提前计入 Task 5B 成功路径，章节初次规划才强制校验 `chapter_intent.text/pov`。
- 已验证计划书与 Prompt 契约均能读取新增的 `accepted_chapter_revision_id`，计划书 UTF-8 无替换字符；当前验证仍为文档级，未执行应用构建或业务测试。
- 已验证计划书和 Prompt 契约不再残留 `MVP`、`mvp`、`首版` 或 `初版` 定位；V1 范围外能力、V1 工程里程碑、V1-RC 工程验收和非市场验证边界已写入计划书。
- 已验证计划书与 Prompt 契约同步包含 `entry_handoff_id`、`entry_source_chapter_revision_id`、`chapter_sync_status`、父运行字段和 handoff/聚合阻断规则；当前仍未执行应用构建、迁移或业务测试。
- 已清理 Task 4 中重复的 Postgres checkpointer 步骤，并将领域事务、outbox、`last_durable_node` 与 checkpoint 重放边界合并为一条规范。
- 本次收口前已重新读取评审对话、实施计划和 Agent Prompt 契约；旧评审提出的首稿、人工命令、任务边界、恢复 CAS、部署和验收问题已逐条映射到新的文档契约。
- 已验证计划书和 Prompt 契约的代码围栏仍分别为 34、32 个且成对；Prompt 中 8 个 JSON 块全部解析成功。
- 已验证计划书中的 3 个 JSON 示例和 Prompt 契约中的 8 个 JSON 示例全部解析成功；资源 API 的多请求示例已改为 `text` 围栏，不再误标成单个 JSON 文档。
- 已验证计划书与 Prompt 契约均包含 `SceneDraftArtifact`/`commit_scene_draft`、首稿反馈回 WritingAgent、终态候选清理和 `expected_run_version` runtime-only 规则；计划书还包含 `CommandIdempotencyRecord`、`manual_command_id`、请求指纹、`IDEMPOTENCY_KEY_REUSE` 和 `API_BIND_SCOPE`。
- 已验证 Task 5B 不再同时保留“启动图”和“只入队”两条互相冲突的步骤；当前只保留校验后入队，并新增重复启动和同键不同请求体测试要求。
- 已验证空场景人工根编辑的 `base_scene_revision_id=null` 例外只对 `source=author` 的 `prosemirror_step` 开放，并先进入 `SceneDraftArtifact`；Agent/Review ChangeSet 仍要求非空基线。
- 已验证 `ChapterRunState` 只保存 `draft_artifact_id`，Prompt 的 `draft_text` 由运行时临时加载，计划书、Prompt 和日志对该序列化边界一致。
- 已验证计划书的 `RunExecutor` 端口包含 `claim`、`renew`、`heartbeat`、带 fencing token 的 `execute` 和过期回收；`RunLease`、Outbox 投递字段、事件消费者游标和 `RUN_LEASE_LOST` 已写入计划。
- 已验证计划书与 Prompt 的章节计划字段一致：`ChapterContract.pov`、`SceneBrief.forbidden_beats`、`plan_revision_id` 和 accepted plan 指针规则均已出现；运行创建 JSON 显式包含 `decision_target` 与 `plan_revision_id`。
- 已验证章节/场景 Canon 两个专用 API 入口、章节接受事件的幂等自动入队语义和 `chapter_sync_status=in_sync` 门禁已写入计划与 Prompt。
- 已验证 Canon 三类候选输出示例均包含 `source`、`effective_story_time`、`narrative_knowledge` 和 `resolution_action`，且 Prompt 中的来源段落与叙事认识规则和计划领域字段一致。
- 已验证 Agent 技术重试复用 `agent_run_id`/`agent_attempt_key`、递增 `attempt_no`，IdentityResolution 幂等键使用稳定 attempt key；作者反馈/新逻辑调用创建新身份。
- 已验证 Compose 两种拓扑、`INTERNAL_API_BASE_URL`、Next.js server-side proxy 和宿主机端口门禁已写入计划；当前仍未生成实际 compose 文件。
- 最终文档级校验结果：计划书 34 个代码围栏、3 个 JSON 示例全部成对且可解析；Prompt 契约 32 个代码围栏、8 个 JSON 示例全部成对且可解析；计划书旧冲突关键词计数为 0，日志七个固定章节齐全且无 UTF-8 替换字符。
- 未执行应用构建、单元测试、数据库测试、端到端测试或 Mermaid 渲染器视觉验证；当前验证仍是文档级验证。

## 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| `IdService`、运行时步骤、Hook 注册表、`AgentResultRouter` 和 `ChapterRunState` 尚未实现 | 文档已定义顺序和边界，但不能证明真实运行时会按契约执行 | 文档已验证，代码未实现 |
| `staged_chapter_revision_id`、接受版本映射和三类 Canon 候选尚未接入持久化/查询逻辑 | 多场景聚合、章节提交和 Canon 决策仍需验证不会丢失版本或候选 | 状态契约已补，工程实现未验证 |
| 候选指纹、来源版本为空时的唯一约束和候选记录 ID 生成尚未落地 | 重试、首次生成和跨运行重复候选仍存在实现风险 | 文档规则已定，数据库约束未实现 |
| `ChapterHandoff`、`ChapterAggregationEligibility`、`chapter_sync_status` 和重规划继承映射尚未接入运行时代码 | 场景接受与章节接受、跨章节承接、聚合阻断和旧运行失效仍需验证不会读取错误版本 | 规范已同步到计划书/Prompt，代码未实现 |
| `needs_clarification` checkpoint、SSE 等待事件、反馈恢复和进程重启恢复尚未接入 | 恢复节点、预算扣减和错误分类可能与文档不一致 | 仅文本级验证 |
| 新增的 `paused` 状态、Router 结果信封和技术重试计数尚未接入运行时代码 | 预算耗尽、依赖不可用和恢复入口仍需验证不会错误地落入 `failed` 或继续调用 Agent | 契约已补，代码未实现 |
| 应用代码尚未实现状态不变量、序列化边界、路由契约、恢复映射和类型归属 | 运行时仍需验证不会把运行状态、Agent schema、API 请求和恢复入口混为一谈 | 文档已分组修补，代码未实现 |
| Markdown 主流程图尚未用渲染器验证 | 复杂回路、错误分支和 AUX 布局仍可能存在视觉问题 | 文本节点检查完成，视觉验证未完成 |
| 运行包装图同时展示身份、来源、临时键和节点简称，名称层次较多 | 读者可能把不同类别名称误认为重复的 ID 或新增业务节点 | 语义已通过文字澄清，图示视觉简化尚未执行 |
| 新增的任务切片、API/SSE 契约和验收 fixture 尚未落成文件或代码 | 计划已可按依赖实施，但不能证明实际接口、事件重放和错误门禁符合契约 | 计划文本已修订，工程实现未开始 |
| `Task 5B` 对 Canon 字段保留兼容信封，但正式成功路径延后到 `Task 5C` | 实现时必须保留 `CANON_NOT_ENABLED` 门禁，否则会提前越过 M3 的 Canon 边界 | 计划已明确，代码未验证 |
| 质量阈值、Fake model 和十次反馈记录尚未产生实际报告 | M3/M4 的门槛目前只能作为计划约束，不能作为发布证据 | 计划已定义，报告未生成 |
| 收口后的 `SceneDraftArtifact`、人工命令身份、resume CAS 和私有部署门禁尚未进入应用代码 | 首稿接受、人工编辑幂等、暂停恢复和部署边界仍需运行时证据 | 计划与 Prompt 已同步，代码未实现 |
| 首稿物化、人工命令幂等重放、终态候选清理和 `paused` resume CAS 尚未有应用实现 | 新契约已消除主要猜测点，但仍不能证明事务锁、重放顺序和状态迁移在运行时正确 | 文档已验证，代码未实现 |
| `single_user_private` 绑定、非空库迁移、备份恢复和真实模型 smoke 尚未执行 | V1 的私有部署和发布证据仍依赖 Task 9 的实际演练 | 计划已补门槛，运行验证未完成 |
| 租约 fencing、续租/heartbeat、旧 worker 迟到写入拒绝尚未有运行时代码 | 长模型调用和租约接管仍需证明不会重复提交或污染事件/版本 | 计划与端口已写入，代码和故障注入测试未实现 |
| 计划当前/accepted 指针、章节 Canon 自动入队、Outbox 投递状态/消费者游标尚未落库 | 重规划、章节接受后的 Canon 启动和事件重放仍依赖实现阶段按契约落地 | 文档契约已补，迁移和 API 未实现 |
| Canon 候选 schema 与 Agent 重试 attempt key 尚未进入实际 Pydantic/数据库模型 | 候选去重、段落证据和技术重试幂等仍不能由运行时证据证明 | Prompt/计划已同步，应用 schema 未实现 |
| Compose proxy 和 `INTERNAL_API_BASE_URL` 尚未生成或运行验证 | 容器内 API 连通性、CORS 和宿主机端口门禁仍需故障演练 | 拓扑规则已写入，compose/Next 实现未完成 |

## 当前未完成事项与下一步

1. 按 M0 实现工程依赖、迁移入口、`ErrorEnvelope`、健康检查、私有部署门禁和 `FakeModelProvider`。
2. 按 M1 实现资源 CRUD、不可变版本、`SceneDraftArtifact`、人工/Agent ChangeSet、命令幂等、提交守卫、版本比较/回滚及其测试。
3. 实现 Context Pack 的运行作用域、`RunEvent`/checkpoint、Task 4A-4B 单场景和章节闭环。
4. 实现 Task 5B 的运行 API/SSE 重放，再实现 Task 4C/5C/7C 的三类 Canon 路由和 UI。
5. 实现一致性规则、观测降级、脱敏、评测 fixture 和 M3 质量门槛。
6. 执行 Task 9 的空库与非空库重放、暂停/重启恢复、过期版本冲突、备份恢复、真实模型 smoke/显式跳过、回滚和十次反馈记录。
7. 工程实现和测试完成后，再进行一次代码、Prompt、状态接口、API/SSE 契约和流程图的交叉一致性复核。
8. 使用 Mermaid 渲染器复核主流程、场景循环、错误分支和 AUX 辅助图布局。
9. 实现并测试 `RunLease` fencing/续租、计划指针 CAS、章节 Canon outbox 自动入队、Outbox 游标和 Compose server-side proxy；这些是当前进入 Task 4A/5B 前的工程冻结项。
