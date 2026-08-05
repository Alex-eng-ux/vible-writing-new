# 2026-08-01 开发日志

## 工作性质与范围

今天继续收敛连续小说创作工作室的 Agent Prompt、Hook 契约和计划书领域模型，范围覆盖共享输入信封、来源引用、ID 生成责任、7 个 Agent 的输出 schema、计划书 Agent/Workflow 规则、版本/运行时与隐私边界、候选类型、场景接受态、派生快照和章节-场景协调机制。没有开始应用代码、数据库迁移或自动化测试开发。本次日志在用户明确准备压缩前更新。

## 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 示例中的 ID 必须是随上下文变化的占位符，不能使用看似固定的编号 | `src-004`、`fact-102` 等容易被误解为真实或固定格式；示例只能说明字段位置 | Prompt 草案中的共享输入和输出示例改用运行时占位符，并明确不能照抄 |
| 所有正式 ID 通过统一的 `IdService` 分配，但由不同系统组件决定创建时机和对象类型 | 避免 Agent、Hook、领域服务各自维护一套编号规则；同时保留业务边界 | 草案增加 ID 分类、责任映射和幂等分配规则 |
| 正式 ID 不由 Agent 或 Hook 创建 | Hook 的职责是生命周期控制和校验，不能与业务服务重复；Agent 只能返回引用或临时键 | 将 ID 创建拆为 `RunIdentityStep`、`ContextManifestStep`、`IdentityResolutionStep`，Hook 只注入、校验和阻断 |
| ID 按生命周期分层清理，而不是章节结束后全部删除 | 版本血缘、作者反馈和审计仍可能依赖运行 ID、问题 ID、定位 ID 或来源映射 | 草案增加长期业务、条件审计、运行临时和外部观测四级保留策略 |
| 共享输入信封需要补充字段说明 | 仅有 JSON 结构不足以说明 `context_manifest` 与 `context_pack`、作者反馈和规则报告的区别 | Prompt 草案增加字段分组、空值规则和数据流说明 |
| 每个 Agent 的输出约束需要逐字段解释 | 仅有 JSON 示例不足以直接实现类型、必填条件、枚举、空值和下游消费规则 | 草案为 `ChapterPlannerAgent`、`WritingAgent`、`ContinuityAgent`、`ReviewAgent`、`RevisionAgent`、`ChapterReviewAgent`、`CanonAgent` 各增加“字段实现说明” |
| 计划书中的 Hook 契约需要与 Prompt 草案同步 | 计划书原先把事实抽取并入 `DraftHook`，且未完整说明 `SchemaHook`、`FactExtractionHook`、ID 归一化和脱敏失败边界 | 计划书同步调整生命周期、通用 Hook、Agent 专属 Hook、`CommitGuardHook`、失败策略和 Tool Allowlist |
| 初版不拆分题材专业 WritingAgent，ReviewAgent 暂不增加额外评分维度 | 控制初版 Agent 数量和 schema 复杂度；题材差异先由 `style_profile` 表达，额外审查视角归入现有维度 | 草案与计划书固定为一个通用 WritingAgent 和六个 ReviewAgent 评分维度 |
| CanonAgent 支持章节级和作者明确触发的单场景局部设定确认 | 保留局部工作流的灵活性，同时避免局部确认绕过章节接受流程更新全局 Canon | 草案新增 `scope=chapter|scene`，计划书新增局部 Canon 路由和作用域限制 |
| 作者反馈同时支持自然语言和结构化操作，并允许 Agent 请求澄清 | 兼顾自由表达与可执行操作；冲突或缺少定位时不能猜测 | `AuthorFeedback` 增加 `operations`，Hook 和审批恢复规则明确 `needs_clarification` |
| 用户确认 Prompt 草案可纳入任务计划书 | 让实现任务有明确、稳定的输入契约，避免代码只依据散落的 Agent 摘要实现 | 计划书新增“Agent Prompt 契约（已纳入本计划）”章节，并将草案标为已定稿权威源文件 |
| 计划书自审要求优先修正流程和契约矛盾 | 在进入代码实现前消除取消/回滚、Canon 时序、候选事实重复、层级 API 和未声明类型等低级错误 | 计划书已补充显式回滚边界、staged/accepted 版本状态、CanonDecision、身份步骤任务和接口类型归属 |
| 二次自审要求所有原则都有可执行入口 | 需要把澄清暂停、review 分发、多场景反馈、Canon 逐条决策、提交守卫和读写 API 落到状态机与接口 | 计划书已补充 `AgentResultRouter`、运行分发矩阵、场景队列、`CommitGuardHook` 调用点、Canon 决策载荷、层级/读取/保存/回滚 API |
| 用户要求先自审计划书再继续压缩 | 在进入实现前先发现流程、接口和字段之间的矛盾，避免把设计缺陷带入代码 | 完成两轮计划书自审并修正 P1/P2 级文档问题，保留未实现风险清单 |
| 计划书的 Agent and Workflow Rules 必须完整列出每个 Agent | 仅在后文列出 Agent 边界会让实现者阅读全局规则时遗漏规划、章节审校和 Canon 路由 | 计划书第 24 节补齐 7 个 Agent、场景/章节调用顺序和 `review` 独立分支 |
| 计划书的 Agent and Workflow Rules 还必须承载关键路由约束 | 仅列出 Agent 名称仍可能遗漏澄清暂停、运行范围分发、Canon 作用域和反馈目标的实现边界 | 第 24 节补充 `AgentResultRouter`、四类运行分发、候选决策作用域和计划/场景/章节/Canon 反馈路由 |
| 版本、状态和隐私规则必须与实现状态及 Prompt 契约一致 | 原文对基线版本、LangGraph 状态范围和生产环境内容采集的表述过于绝对或宽松，容易造成首次生成、状态恢复和脱敏边界误解 | 计划书修正基线 `null` 语义、轻量路由状态范围，以及完整正文/Prompt 仅限开发或评测环境 |
| 领域模型必须完整覆盖 Prompt 的 Canon 候选类型 | Prompt 同时输出事实、时间线事件和剧情线候选，只定义 `FactCandidate` 会让 API、持久化和作者决策遗漏两类候选 | 计划书新增 `TimelineEventCandidate`、`PlotThreadUpdate`，统一使用 `candidate_type + local_key` 定位并保留候选快照/来源引用 |
| 场景接受态与正文版本保持分离 | `SceneRevision` 正文和血缘不可变；接受、回滚和局部 Canon 需要一个明确的当前接受版本指针 | 计划书明确由 `Scene` 聚合状态和当前接受版本指针记录，未提交内容不得填充 `accepted_scene_revision_id` |
| 场景和章节快照只能作为派生状态 | 快照用于状态衔接和审查，但不能取代 `CanonFact`、`TimelineEvent` 或 `PlotThread` 等权威实体 | 计划书明确 `SceneSnapshot`/`ChapterSnapshot` 绑定版本、可重建且不直接更新 Canon |
| 主章节创作必须按章节规划、场景逐个创作、章节聚合和章节审校的顺序执行，不能在 `normalize_request` 后把场景创作和审校并列成主流程入口 | 保持作者按章节推进的线性工作节奏，避免流程图把请求规范化误解成业务编排 | 计划书第 4 节改为单向主章节流程 |
| `ReviewAgent` 必须在场景正文或补丁产生后执行，`ChapterReviewAgent` 必须在所有场景聚合后执行 | 审校依赖已生成内容；场景级问题应在进入下一场景前发现，章节级问题在整章完成后统一检查 | 计划书主流程已固定为写作后场景审校、聚合后章节审校 |
| 已有版本的局部续写、改写和独立审校保留为辅助操作，但不复用主章节循环，也不触发主流程章节聚合 | 支持小范围修订，同时避免局部任务误改整章状态 | 计划书新增 `SceneRunGraph` 和 `ChapterReviewGraph` 辅助图占位 |
| `AgentResultRouter` 是运行时编排组件，不是 Agent | 它只读取状态并路由、暂停和恢复，不调用模型、不生成内容、不创建 ID | 计划书流程图和 Hook 说明将其作为路由节点处理 |
| 取消运行采用逻辑丢弃，不直接物理删除数据 | 保留已提交版本、作者决策、审计和正式 Canon；未提交候选不进入权威状态，临时数据按终态保留策略清理 | 计划书补充 `cancelled`、逻辑丢弃和非级联清理语义 |
| 不改变原有业务工作流逻辑，只补齐 ID 管理 | 保留“章节规划 → 场景循环 → 章节聚合 → 章节审校 → 作者接受 → Canon”的业务顺序，避免技术性 ID 步骤被误解为新的业务节点 | 计划书新增可复用的 Agent 执行包装层和独立 ID 流程图，未重排主流程 |
| Agent 返回约束必须与 ID 管理同步 | Agent 不能创建正式 ID；新对象只能返回 `client_key`、`local_key` 或文本定位，由运行时归一化 | Prompt 草案补充统一 ID 运行约束，并明确 `thread_id` 是 `generation_run_id` 的别名 |

## 关键规则与取舍

- `context_manifest` 是来源索引，`context_pack` 是本次真正提供给 Agent 阅读的内容；`evidence_refs` 和 `context_source_refs` 只能引用当前 manifest 中已有的 `source_id`。
- 尖括号 ID 只是 schema 占位符，不是命名格式。`revision_id` 和 `anchor_id` 是否存在由实际来源决定，`excerpt_hash` 由实际摘录计算。
- 业务实体和正文版本由 Domain/Versioning Service 调用 `IdService`；运行 ID 由 Workflow Runtime 创建或恢复；来源 ID 由 Context Assembler 创建或复用；审查与正文定位 ID 由对应领域服务解析；`trace_id` 由 Trace Adapter 或外部观测系统生成并记录。
- Agent 对新问题、新定位或新变更集只返回 `local_key` 和正文定位等临时信息。`IdentityResolutionStep` 在 schema 与领域检查通过后，以运行上下文和局部键组成幂等键，解析为正式 ID。
- Hook 不创建 ID、不实现编号算法、不绕过 Domain Service 写库，只负责输入注入、输出校验、引用验证、记录和路由。
- 只有运行进入不可恢复终态、没有待处理反馈或 checkpoint、且持久化引用已完成来源映射后，才允许清理临时 manifest、原始上下文、局部键和无引用运行记录；清理任务必须幂等且禁止级联删除正文、版本和正式事实。
- 运行时生命周期按 `RunIdentityStep / ContextManifestStep -> ContextHook -> RedactionHook -> TraceHook.start -> Agent -> SchemaHook -> 专属 after Hook / FactExtractionHook -> 规则与领域检查 -> ReferenceValidationHook -> IdentityResolutionStep -> RedactionHook -> TraceHook.end` 组织；正式提交或回滚前再执行 `CommitGuardHook -> Domain Service`。
- `FactExtractionHook` 只产出唯一规范化候选载荷，由 `FactCandidateService` 事务化持久化，不提升正式事实；`FeedbackHook` 只恢复对应节点且不得改写作者反馈；`RedactionHook` 失败时不得把未脱敏内容发送到外部模型或观测系统；日志和 Trace 可 fail-open，安全校验必须 fail-closed。
- 章节版本内容和血缘不可变，聚合版本通过 `staged -> accepted` 状态转换；取消运行不补偿已提交版本，显式回滚必须带目标父版本和作者决策。
- Canon 候选决策使用 `(candidate_type, local_key)` 唯一定位；章节级确认才可进入全局 Canon，场景级确认只保留场景作用域记录。
- Agent 输出约束中，`status`、评分、候选条目、文本定位、`evidence_refs`、`local_key`、基线版本、修改操作和候选状态均已明确类型、枚举、空值语义及运行时归一化责任。
- 领域模型中的三类 Canon 候选为 `FactCandidate`、`TimelineEventCandidate`、`PlotThreadUpdate`；它们是待确认载荷，不是 `CanonFact`、`TimelineEvent` 或 `PlotThread`，候选决策记录必须保留候选类型、`local_key` 和候选快照/来源引用。
- 场景正文版本不可变，场景接受态由 `Scene` 的聚合状态和当前接受版本指针记录；`SceneSnapshot` 与 `ChapterSnapshot` 都绑定版本、可重建且不直接更新权威 Canon。
- 主章节工作流固定为 `ChapterPlannerAgent -> 场景循环（WritingAgent -> FactExtractionHook -> RuleEngine -> ContinuityAgent -> ReviewAgent） -> ChapterAggregator -> ChapterReviewAgent -> 作者接受 -> CanonAgent`；审校不与主创作入口并行。
- `normalize_request` 只负责输入规范化和运行初始化；已有版本的场景操作、场景审校和章节审校由独立辅助图承接，不接入主章节循环。
- `ReviewAgent` 的场景审校在每次写作或补丁应用后执行；`ChapterReviewAgent` 只在所有场景完成并聚合后执行。
- `AgentResultRouter` 是条件路由/暂停恢复组件，不属于 7 个 Agent 清单，也不拥有 Prompt、模型调用、正式 ID 或数据库写入权限。
- 取消运行只标记 `GenerationRun.cancelled` 并逻辑丢弃未提交候选；已提交版本和正式业务数据不删除，未引用临时数据在依赖检查后按保留策略清理。
- `FactExtractionHook` 固定为只处理 `WritingAgent`/`RevisionAgent` 已返回候选事实的确定性规范化步骤：不调用模型、不做语义抽取、不直接提升正式事实；`RedactionHook` 只处理观测副本，`ReferenceValidationHook` 区分正式 ID 与临时键，`BudgetHook`、`ErrorHook`、`FeedbackHook` 分别承担预算、异常和恢复职责。
- `needs_clarification` 在 `SchemaHook` 后立即交给 `AgentResultRouter` 暂停，跳过专属 Hook、领域检查、引用校验、ID 归一化和提交；`FactExtractionHook` 仅在状态为 `ready` 且前置校验完成时触发，并按运行范围和场景版本执行幂等去重。

## 已完成产出

- 更新 `docs/superpowers/specs/2026-07-31-agent-prompts-v1-draft.md`：补充共享输入信封字段说明、ID 分类与分配责任、Agent/Hook 边界、ID 生命周期和清理策略。
- 更新 `docs/superpowers/specs/2026-07-31-agent-prompts-v1-draft.md`：校准 7 个 Agent 的请求类型、输出字段、临时键、文本定位、候选事实、状态快照、评分、ChangeSet 和 Canon 候选结构。
- 更新 `docs/superpowers/specs/2026-07-31-agent-prompts-v1-draft.md`：为 7 个输出 schema 增加字段级实现说明，覆盖必填/可空、枚举、引用来源、下游消费和正式 ID/hash 归一化责任。
- 更新 `docs/superpowers/plans/2026-07-31-novel-writing-studio-plan.md`：将 ID 分配从 Hook 中移到运行时/领域服务步骤，新增并明确 `FactExtractionHook`、`ReferenceValidationHook`、`SchemaHook`、`CommitGuardHook`、脱敏、失败策略和 Tool Allowlist。
- 根据用户确认更新两份文档：初版 WritingAgent 不按题材拆分；ReviewAgent 保持六维评分；CanonAgent 支持章节级与单场景局部确认；作者反馈支持 `text` 与 `operations`；冲突、缺定位或缺上下文时统一暂停到 `needs_clarification`。
- 已将 Prompt 契约纳入实施计划：计划书新增 7 个 Agent 的边界、共享输入约束、Hook 验收规则和 Task 4 的契约基线；草案状态更新为“已定稿并纳入实施计划”。
- 保留旧字段清理结果：`evidence_ids` 和 `context_source_ids` 已统一为 `evidence_refs` 和 `context_source_refs`。
- 完成领域模型自查并修订计划书第 3 节：补齐三类 Canon 候选、场景接受指针、派生快照语义、接口类型归属、候选持久化清单和章节聚合边界。
- 根据用户对流程顺序的修正，重画计划书第 4 节：主流程改为章节规划 → 场景循环 → 章节聚合 → 章节审校 → 提交；移除 `normalize_request` 后的主流程业务分流。
- 将场景级 `ReviewAgent` 固定到写作/补丁之后，将 `ChapterReviewAgent` 固定到全场景聚合之后；新增不复用主循环的 `SceneRunGraph`、`ChapterReviewGraph` 辅助流程占位。
- 明确 `AgentResultRouter` 不是 Agent，并补充取消运行的逻辑丢弃、不删除已提交数据和终态清理语义。

## 验证结果

- 已重新读取两份设计文档，UTF-8 读取正常，未发现替换字符。
- 已验证共享输入说明、`IdService` 责任映射、四级 ID 保留策略、终态清理条件和禁止级联删除规则均已写入。
- 已验证旧的 `RunIdentityHook`、`IdentityResolutionHook` 等 ID 创建 Hook 表述已移除，替换为运行时步骤和只读校验 Hook。
- 已验证草案包含 7 个 Agent、7 个输出 schema 和 7 段字段实现说明，且每段说明位于对应 schema 之后、下一个 Agent 之前；未发现重复 Agent 标题。
- 已验证计划书生命周期中 `SchemaHook` 位于 `ReferenceValidationHook` 之前，事实抽取职责独立，`ReviewIssue` 使用 `local_key`、`text_locator`、`evidence_refs`，正式 ID 由 `IdentityResolutionStep` 归一化。
- 已验证草案和计划书 UTF-8 可读取，未发现 `{{...}}` 模板变量或旧的 `observed_state_delta`、`proposed_exit_state`、`affected_scene_ids` 字段。
- 已验证草案不再保留原“待审查问题”列表，替换为五项已定稿决策；已验证 `AuthorFeedback.operations`、Canon 候选 `scope`、局部 Canon Hook 和 `needs_clarification` 路由在草案与计划书中均有对应说明。
- 已补充 `accepted_scene_revision_id` 与 `canon_scope` 共享输入字段，并将其加入 `ChapterRunState`，避免局部 Canon 规则引用未声明变量。
- 已验证计划书能链接到 Prompt 契约，Task 4 明确以该契约作为 schema、Prompt 和 Hook 实现基线，且两份文档均可用 UTF-8 正常读取。
- 已完成计划书自审修订：规则顺序统一为 `RuleEngine -> ContinuityAgent -> ReviewAgent`；章节 Canon 移到作者接受的 `ChapterRevision` 之后；局部 Canon、候选逐条决策、候选事实唯一归一化入口、层级创建 API、显式回滚和身份基础设施均已写入任务与接口。
- 已完成二次自审修订：`needs_clarification` 有暂停/恢复状态和 SSE 事件；`review` 与场景运行有独立分发；章节反馈有受影响场景队列；FactExtractionHook 只产出规范化载荷，由 FactCandidateService 持久化；正式提交和回滚明确经过 CommitGuardHook。
- 已补齐计划书第 24 节的完整 Agent 清单：`ChapterPlannerAgent`、`WritingAgent`、`ContinuityAgent`、`ReviewAgent`、`RevisionAgent`、`ChapterReviewAgent`、`CanonAgent`，并写明场景检查顺序、章节审校时机、章节接受后的 Canon 路由和 `review` 不调用 `WritingAgent`。
- 已进一步补齐 Canon 候选类型命名空间、章节/场景回滚接口、章节版本 `staged -> accepted` 状态、空库层级创建与正文读取/手动 ChangeSet API、低风险自动修订计数和全 Agent Trace 覆盖。
- 已重新读取计划书，确认第 24 节不再遗漏 Agent，且新增顺序与后文 Prompt 边界、Task 4 文件清单和 `review` 路由一致。
- 已再次核对第 24 节，确认澄清暂停、运行范围分发、FactExtractionHook 权责、Canon 逐条决策和不同反馈目标均已在全局规则中显式落点。
- 已修正计划书 Version and Runtime Rules 与 Observability and Privacy 中发现的三处表述：首次基线允许 `null`、LangGraph 仅保存轻量路由状态、生产环境不采集完整正文和 Prompt；并确认与 Prompt 草案和 Task 8 规则一致。
- 已交叉验证领域模型与 Prompt 草案：三类候选名称、`candidate_type`、`local_key`、`scope`、`evidence_refs` 和 `accepted_scene_revision_id` 均有对应定义；计划书无模板变量，Markdown 代码围栏保持成对。
- 已记录两轮自审后的最终计划约束：澄清恢复节点映射、场景级 review 不调用 WritingAgent、章节反馈队列、Canon 取消/反馈语义、候选类型命名空间、接口类型归属和所有正式写入的 CommandContext。
- 已做最终文档级检查：计划书与 Prompt 契约 UTF-8 正常、无 `{{...}}` 模板变量、Markdown 代码围栏成对、Mermaid 节点 ID 无重复、计划书链接可解析；未执行应用代码测试。
- 已重新读取最新计划书：UTF-8 正常，Markdown 代码围栏保持成对；确认主流程不再存在 `normalize_request` 后的旧 `run_scope` 分流，也无残留 `L0`、`R0`、`YR`、`D0` 节点引用；确认辅助场景图不直接复用主流程的 `F` 节点。
- 已核对最新主流程顺序：`WritingAgent` 后才进入 `ReviewAgent`，全场景聚合后才进入 `ChapterReviewAgent`，场景局部 Canon 位于场景提交后，章节 Canon 位于章节提交后。
- 已根据用户确认更新计划书：新增 ID 管理与 Agent 执行包装层，明确 `RunIdentityStep -> ContextManifestStep/ContextAssembler -> Agent -> SchemaHook -> ReferenceValidationHook -> IdentityResolutionStep -> AgentResultRouter` 仅是技术边界，不改变业务流程。
- 已同步 Agent 返回约束：已有正式 ID 只能引用输入值，新对象返回 `client_key`/`local_key`/文本定位；正式 ID 由 `IdentityResolutionStep` 按幂等键归一化；`thread_id` 不再单独分配，而是 `generation_run_id` 的别名。
- 已完成 Hook 契约复核并完成文档修订：`FactExtractionHook` 固定为不调用模型的确定性规范化步骤；`RedactionHook` 只处理观测/外部副本；`ReferenceValidationHook` 区分已有正式 ID 与新临时键；`BudgetHook`、`ErrorHook`、`FeedbackHook` 已放入统一生命周期并明确失败、重试和恢复边界。
- 已统一 `FactExtractionHook` 的触发范围：只处理 `WritingAgent`/`RevisionAgent` 已返回的 `candidate_facts`，仅在 schema、专属 Hook 和状态短路通过且状态为 `ready` 时执行；`needs_clarification`、失败或未完成重试均跳过下游 Hook、ID 归一化和提交。
- 已完成脱敏和 ID 别名说明：业务结果继续供状态、路由和提交使用，`RedactionHook` 只生成观测副本；`thread_id` 与 API `run_id` 均为 `generation_run_id` 的别名，不单独分配。
- 当前没有应用代码、数据库迁移、API、前端或自动化测试；未执行构建、单元测试或端到端测试。

## 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| `IdService`、三个运行时步骤和 `ReferenceValidationHook` 只有文档契约，没有实现接口 | 尚不能验证 ID 分配、重试幂等和跨运行引用校验的真实行为 | 尚未实现或测试 |
| UUID、ULID 或数据库生成值尚未最终选定 | 会影响数据库约束、索引和外部 API 的 ID 表现 | 草案明确保持实现无关，待工程骨架阶段决定 |
| 具体保留期限、终态失败分类和后台清理任务尚未定稿 | 可能影响审计可追溯性、存储成本和隐私处理 | 仅完成生命周期原则，尚未实现验证 |
| 来源映射从临时 `source_id` 转换为持久化实体/版本/定位的存储结构尚未定义 | 清理前无法自动证明仍有引用的来源可以安全删除 | 尚未实现验证 |
| 输出 schema 的具体 Pydantic 类型、嵌套对象约束和错误码尚未落到代码 | 字段说明已可指导实现，但还不能验证运行时序列化、校验失败和兼容性 | 仅完成文档级验证，未实现测试 |
| `AuthorFeedback.operations` 的具体嵌套对象 schema、局部 Canon 的持久化记录结构尚未落到代码 | 后续实现仍需确定定位字段、操作参数和场景作用域的数据库约束 | 已完成文档契约，尚未实现验证 |
| 本次自审新增的 `CanonDecision`、`CommandContext`、身份步骤和候选事实去重规则尚未落到代码 | 计划书已消除设计歧义，但仍需通过 Pydantic、事务和跨运行测试验证 | 仅完成文档级修订，尚未实现测试 |
| 计划书新增的运行分发、澄清恢复、多场景队列、Canon 决策和层级读写 API 尚未实现 | 文档路径已闭合，但真实状态机和接口兼容性仍需代码测试验证 | 仅完成文档级修订，尚未实现测试 |
| 三类 Canon 候选、场景当前接受版本指针和派生快照尚未落到数据库模型 | 作者确认恢复、场景回滚和章节聚合仍需验证候选快照、版本归属和当前指针的一致性 | 计划书已明确契约，尚未实现或测试 |
| `SceneRunGraph` 和 `ChapterReviewGraph` 目前只有流程边界，没有详细节点、输入输出和提交契约 | 实现独立局部操作时仍需避免复用主章节循环或误触发章节聚合 | 已写入计划书占位，尚未设计和实现 |
| 最新 Mermaid 流程只做了文本级节点引用检查，未使用 Mermaid 渲染器做视觉验证 | 复杂回路和辅助图的实际布局仍可能需要调整 | 已确认无旧节点残留，尚未渲染验证 |
| Hook、ID 步骤和 Agent 契约尚未落到应用代码 | 文档已统一职责和顺序，但真实运行时仍可能出现注册遗漏、错误短路或重复归一化 | 已完成文档修订，尚未实现或测试 |
| `FactExtractionHook` 的候选去重、哈希和 `FactCandidateService` 事务边界尚未实现 | Writing/Revision 重试或补丁循环仍需验证不会产生重复候选或提前持久化 | 已完成契约，尚未实现验证 |
| 观测副本脱敏与外部模型/Trace/SSE 适配尚未实现 | 仍需验证脱敏失败时 fail-closed，且业务结果中的正文、ID、定位和引用关系不被修改 | 已完成契约，尚未实现验证 |

## 当前未完成事项与下一步

1. 定稿 `IdService`、`RunIdentityStep`、`ContextManifestStep`、`IdentityResolutionStep` 和 `ReferenceValidationHook` 的输入输出 schema。
2. 明确模型原始输出与运行时归一化输出的字段契约，尤其是 `local_key`、新 `issue_id` 和新 `anchor_id` 的转换。
3. 实现 ID 分配的幂等键、跨运行引用拒绝、来源映射物化和终态清理任务。
4. 为 ID 重试、版本冲突、作者反馈后恢复、来源清理依赖和禁止级联删除补充自动化测试。
5. 完成单场景闭环后，再进行章节聚合、局部重跑、SSE 进度和 LangSmith 观测接入。
6. 按草案字段说明实现 7 个 Agent 的 Pydantic 输入/输出 schema，并为 Hook 注册表、事实抽取、引用校验、脱敏和提交守卫补充测试。
7. 实现并测试三类 Canon 候选的统一定位、场景接受版本指针、派生快照重建和回滚后的章节重新聚合。
8. 单独设计并实现 `SceneRunGraph`、`ChapterReviewGraph` 的输入输出、作者反馈、版本提交和 Canon 边界。
9. 使用 Mermaid 渲染器复核最新流程图布局，并补充主章节顺序、场景审校后循环和独立辅助图的流程测试。
10. 按已定稿契约实现 Hook 注册表和运行时包装层，验证 `needs_clarification` 短路、错误分类、预算检查、反馈恢复和正式提交前守卫。
11. 实现并测试 `FactExtractionHook` 在 `WritingAgent` 和 `RevisionAgent` 路径中的候选去重幂等键、声明哈希、证据合并和 `needs_clarification` 跳过行为。
12. 实现并测试观测副本脱敏、正式 ID 与临时键双层校验，以及 `run_id`/`thread_id` 到 `generation_run_id` 的别名一致性。
