# 2026-07-31 开发日志

## 1. 工作性质与范围

今天完成连续小说创作工作室的产品与架构设计收敛，没有开始应用代码开发。设计从开源写作 Agent 调研出发，首版定位为“作者始终掌控的连续创作工作台”，不做一次性自动写完整本小说，也不做泛化的多 Agent 平台。

## 2. 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 选择连续小说创作工作室方向 | 核心资产是可追溯的作品事实，而不是聊天记录、单次 Prompt 或向量库 | 实施计划的产品范围与领域模型 |
| 章节面向作者、场景面向系统 | 作者以章节发起、审阅和提交；场景负责内部生成、校验和局部重跑。短章可作为单场景，长章由规划流程拆分 | 产品信息架构、章节-场景协调机制 |
| 所有审批点采用循环 | 统一使用 `accept`、`feedback`、`cancel`；`feedback` 回到对应规划、生成或修订节点，只有 `cancel` 是终态 | 章节计划、场景正文和章节最终审批流程 |
| 章节反馈只重跑受影响场景 | 反馈应定位到具体场景，局部重跑后重新聚合，避免无差别重写整章 | `ChapterReviewAgent`、章节聚合与局部重跑规则 |
| Hook 与对应 Agent 协同 | Hook 同时承担一致性检查与日志治理，不作为隐式通用中间件 | Agent Hook 设计与实施任务 |
| 来源 ID 统一为单一来源表 | `evidence_refs` 证明判断，`context_source_refs` 记录实际使用的上下文；二者复用 `ContextManifest.source_id`，不维护两套编号 | Prompt 规范的共享输入信封与输出契约 |
| 首版接入 LangSmith，但不依赖它恢复业务 | LangSmith 用于 Trace、评测和成本分析；PostgreSQL 仍是正文、作者决策和任务状态的权威来源 | 可观测性与隐私边界 |

## 3. 关键规则与取舍

- 作品层级为 `NovelProject -> Volume -> Chapter -> Scene`；`Project` 是一部小说，`GenerationRun` 是一次写作、续写、改写或审校任务，对话线程不是小说本体。
- `ChapterContract` 定义章节目标、开场/结尾状态、场景顺序、必达/禁止事件和待回收剧情线。相邻场景以入口/出口状态衔接，最后一个场景出口必须满足章节预期结尾。
- 自动修订与作者反馈分开：系统对低风险问题每回合最多自动修订一次；作者反馈循环受预算、超时、历史记录和显式取消控制，但不受“一次”限制。
- 工作流采用固定节点和受控路由，不让 LLM 自由选择 Agent。`WritingAgent` 只负责新写和续写，全部改写交给 `RevisionAgent`；审查 Agent 不直接修改正文。
- 检查顺序为：确定性规则 -> `ContinuityAgent` -> `ReviewAgent`。模型负责生成和语义判断，规则引擎负责确定性校验，业务服务负责提交、版本与回滚。
- 正文、已确认设定和候选设定分层。`CanonFact` 仅保存作者确认的事实；模型发现的内容只能以 `FactCandidate` 返回。
- 正文版本使用不可变的 `SceneRevision` 与 `ChapterRevision`。`ChangeSet` 必须带基线版本，执行冲突与幂等校验；业务 ID 由系统生成，Agent 不得编造或改写 ID。
- Hook 生命周期为 `before_agent -> Agent 执行 -> after_agent -> schema/领域校验 -> 路由或等待作者`。业务安全与提交校验 fail-closed；Trace 和日志观测 fail-open；提交后的摘要、索引与 SSE 更新异步重试。
- LangGraph 负责编排、循环和中断恢复；PostgreSQL 是权威事实源，`pgvector` 仅辅助检索。首版不做自由 supervisor、Neo4j、CRDT 实时协作、整书一键生成、第三方发布或多租户。

## 4. 已完成产出

- `docs/superpowers/plans/2026-07-31-novel-writing-studio-plan.md`：实施计划，包含产品信息架构、章节-场景协调、Agent 工作流、Hook、技术边界和实施任务。
- `docs/superpowers/specs/2026-07-31-agent-prompts-v1-draft.md`：待审查的 Prompt 与结构化契约草案，包含 Agent 边界、状态变化、澄清协议、来源引用和工具权限约束。
- `AGENTS.md`：用户明确要求“压缩”时的开发日志规范。

## 5. 验证结果

- 已将讨论确认的产品边界、章节-场景协调、作者反馈循环、Hook 配置和来源 ID 规则写入设计文档。
- 已检索 Prompt 草案，旧字段 `evidence_ids` 与 `context_source_ids` 已移除，替换为 `evidence_refs` 与 `context_source_refs`。
- 已完成计划书与 Prompt 草案的文档级一致性检查；两份文档均标为实施计划或待审查草案，不能视为已完成的应用功能。
- 未新增应用代码、数据库迁移、API、前端或自动化测试；未执行构建、单元测试或端到端测试。

## 6. 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| 题材化 `WritingAgent`、审查评分维度、场景级 Canon 确认、结构化反馈与 `needs_clarification` 交互协议尚未定稿 | 会影响 schema、路由和 UI | Prompt 草案已列为待审查项 |
| `ContextManifest`、稳定 `anchor_id`、`expected_text_hash`、`ChangeSet` 冲突处理与场景状态衔接仅有契约设计 | 无法确认真实正文、并发修改和局部重跑时的正确性 | 尚未实现验证 |
| Hook 注册表、执行顺序、重试/暂停状态及其与 LangGraph checkpoint 的接口未实现 | 无法确认 fail-closed 与 fail-open 策略在运行时生效 | 尚未实现验证 |
| 工作台布局、问题定位和局部重跑尚无可操作原型或端到端样例 | 无法确认作者使用时的可理解性和效率 | 尚未进行体验验证 |

## 7. 当前未完成事项与下一步

1. 审查并定稿 `ContextManifest`、各 Agent 的 Pydantic 输入输出 schema、ID 生成策略和 Hook 注册表。
2. 建立工程骨架、健康检查和本地运行契约。
3. 优先实现正文版本、`ChangeSet`、场景状态转换、作者反馈循环及候选事实的事务性流程。
4. 为来源引用、schema 校验、版本冲突、章节-场景状态衔接和 Hook 失败策略补充测试。
5. 完成单场景闭环后，再实现章节聚合、局部重跑、SSE 进度和 LangSmith 观测接入。
