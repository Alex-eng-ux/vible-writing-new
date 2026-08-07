# 连续小说创作工作室二阶设计计划书

> 本文是基于《连续小说创作工作室 V1 工程交付计划》与当前成品现状形成的产品与工程设计。目标不是重新建设底层 Agent，而是把已有能力收敛为作者可以从章节意图走到 Story Bible 的完整前后端工作流。

## 1. 设计结论

当前系统已经具备资源树、场景编辑、场景级运行、版本治理、Canon 数据模型、Worker、章节 Agent 和大量后端测试。当前缺口集中在“作者可见的章节主流程”：章节意图没有被完整采集，计划生成被初始化接口替代，章节级运行、聚合、审校和接受没有形成连续入口。

二阶建设采用“流程契约优先”路线：先定义章节工作流的状态、命令和读取视图，再补齐后端编排接线，最后完成前端章节工作台，并用一条真实 Playwright 流程验收。最终交付必须是可用的前后端功能，不是只完成设计文档、局部接口或局部页面。原有场景编辑器、版本治理、幂等、租约、事件和 Canon 领域约束继续复用，不做无关重写。

本文同时作为实现基线和交付清单使用。第 3 节明确当前代码状态；“当前已经存在”表示已有底层能力或独立入口，不表示二阶章节主流程已经完成；只有第 8 至第 11 节的阶段退出标准、主流程验收和完成定义全部满足，才能称为二阶建设完成。

## 2. 建设目标

### 2.1 用户目标

作者在一个章节工作区内能够完成以下闭环：

1. 创建作品、卷、章节，用自然语言描述想写的内容；题材、目标读者、文风、章节目标、开场状态、结尾状态、主视角、必达剧情点和禁止事项均可逐步补充，不要求首次一次性填写完整。
2. 启动章节规划，与同一个 ChapterPlannerAgent 多轮讨论，查看 ChapterContract 和有序 SceneBrief，提出反馈并反复重新规划，直到接受计划。
3. 接受计划后，系统自动创建场景映射并按计划顺序处理场景。作者以章节为单位查看当前场景、正文、检查结果和反馈入口，不需要手动维护场景顺序。
4. 每个场景经过生成、确定性检查、连续性检查和质量审校后，作者可以接受、反馈或取消。
5. 所有场景完成后，系统生成 staged ChapterRevision，运行章节审校，展示章节级问题和影响范围。
6. 作者接受章节版本后，系统创建章节级 Canon 运行，作者逐条确认、拒绝或暂缓候选，确认项进入 Story Bible。
7. 作者可以在流程中的任何等待点看到当前阶段、阻塞原因、下一步动作、版本来源和受影响场景。
8. 最终形成可用的前后端章节工作台：前端能够完整呈现并操作上述流程，后端完整提供规划、讨论、决策、场景队列、版本、审校、Canon、状态读取和暂停恢复能力；前后端通过真实端到端流程打通。

章节规划的最小输入是一段非空的作者自然语言意图，不是完整结构化表单。AI 可以根据已有信息提出澄清问题、给出可编辑的契约建议或标记不确定项；作者回答、采纳或修改后，系统才把内容冻结为 ChapterContract。AI 推断的关键目标、状态、人物关系和必达剧情点必须明确标记为建议，不能静默写入正式计划。

章节主流程应明确呈现为：

作者输入章节意图
→ ChapterPlannerAgent 与作者多轮讨论
→ 生成并展示章节计划和有序 SceneBrief
→ 作者确认章节计划
→ 系统自动创建场景映射并按顺序生成正文
→ 作者逐场查看、反馈和接受
→ 所有场景完成后聚合为章节版本
→ 作者确认章节版本后进入 Canon

### 2.2 工程目标

- 补全前后端章节工作流功能：将上述主流程实现为一条可恢复的状态机、命令链和前端工作区，并打通真实端到端旅程，而不是一组孤立接口或局部页面。
- 计划书流程图中的每个作者决策节点都对应明确的 UI 动作、后端命令、持久化状态和可恢复 checkpoint。
- ChapterPlannerAgent 必须支持同一章节上下文中的初次规划、澄清恢复和计划反馈重规划；讨论记录、待回答问题、待确认建议和作者决策必须可读取、可恢复，未确认内容不得进入 accepted plan。
- Agent 职责必须收紧：ChapterPlannerAgent 是唯一负责章节规划讨论的 Agent；其他 Agent 只复用现有能力并处理已接受计划下的场景生成、场景审校、修订、章节审校或 Canon，不新增通用聊天 Agent，不把章节规划逻辑扩散到其他 Agent。
- “计划生成”的定义与接口约束：计划生成是一次真实的章节规划运行，不是把已有场景重新包装成计划。新 UI 必须调用 POST /api/chapters/{chapter_id}/runs，提交 run_scope=chapter、request_type=new_chapter、decision_target=plan 和非空 chapter_intent.text；ChapterPlannerAgent 根据意图生成 ChapterContract 与有序 SceneBrief，允许澄清和计划反馈，作者接受后才形成 accepted plan。现有 POST /api/chapters/{chapter_id}/plan 只在迁移期间作为兼容/测试接口，用于初始化已有场景并直接接受；它不能作为新 UI 的主入口，不能被称为 AI 章节规划，完成迁移后必须删除。
- 场景自动生成约束：章节主流程不以作者手动创建场景为前置。ChapterPlannerAgent 必须从章节意图生成有序 SceneBrief；作者接受计划后，领域服务才物化场景映射，Worker 再按 accepted plan 的顺序自动生成场景正文。
- 每个场景的正文生成、确定性检查、连续性检查和质量审校必须绑定对应的 SceneBrief、accepted plan 版本和场景顺序；当前场景未完成作者决策时，Worker 不得跳过、插入或直接推进后续主流程场景。
- 章节聚合只能在计划清单中的所有场景具备有效 accepted revision 后执行；章节反馈或重新规划必须产生新版本并计算受影响场景，不能覆盖旧计划或旧章节版本。
- 章节和场景的 accepted、staged、stale、out_of_sync 等状态必须来自服务端权威视图，前端不得根据“最新行”自行推断。
- 正文、计划、章节版本、Canon 和审计记录继续保持不可变血缘、事务边界、幂等和 fencing 约束。
- ChapterWorkflowRead 必须一次性提供当前阶段、Planner 讨论、待决问题/建议、当前计划、场景队列、运行事件和阻塞原因，使前端能按同一状态恢复工作区，而不是拼接多个“最新记录”接口。
- 观测边界必须明确：PostgreSQL、RunEvent、checkpoint 和本地结构化日志是运行与审计底座；LangSmith 只作为可选的 Agent Trace、评测、耗时/Token/成本分析出口。LangSmith 未配置或不可用时，业务仍必须能够运行、暂停、恢复和提交；生产环境只发送脱敏元数据，完整正文和 Prompt 仅允许在开发/评测环境显式开启。
- 旧的场景编辑、场景审校、版本比较、回滚和 Canon 测试不回归。

### 2.3 不在本轮范围

- 多人协作、CRDT、评论系统和权限体系。
- 场景并行生成、整书批量生成和自动发布。
- 重写富文本编辑器核心能力。
- 新增第二套模型调用协议或替换 LangGraph/Worker 架构。
- 用装饰性流程图替代真实状态和命令。

## 3. 当前基线与差距

### 3.1 当前已经存在

- NovelProject -> Volume -> Chapter -> Scene 资源树、创建、删除和读取。
- Tiptap/ProseMirror 正文编辑、ChangeSet 提交、冲突处理、版本比较、回滚。
- 场景级 continue、rewrite、review 运行，SSE 事件、反馈、接受、取消、澄清和暂停恢复。
- ChapterPlanRevision、SceneBrief、ChapterRevision、ChapterAggregator、ChapterReviewAgent 和 CanonAgent 的后端领域或运行能力。
- 场景级和章节级 Canon 候选、逐条 confirm|reject|defer 决策及 Story Bible 读取。
- PostgreSQL 版本、事件、幂等、租约、checkpoint、outbox 和错误契约测试。

### 3.2 当前必须修正

- 前端把章节意图设计成一次性结构化表单，未提供自然语言起步、AI 澄清和建议确认；项目和章节部分字段仍使用默认或硬编码值。
- 当前前端“生成章节计划”按钮调用的是 `POST /api/chapters/{chapter_id}/plan` 兼容接口，而不是章节规划运行。该接口的实际行为是：已有 `accepted plan` 时直接返回；没有计划时读取章节已有场景，将每个场景的 `scene_brief` 映射为计划项；章节没有场景时创建空计划；随后自动接受计划并建立场景映射。因此它不会读取非空的 `chapter_intent.text`，不会调用 `ChapterPlannerAgent`，不会产生澄清问题或可确认建议，也不会把计划交给作者审阅后再接受。这个接口只能作为迁移期间的初始化/测试路径，不能把它的结果称为 AI 生成的章节计划；新主流程必须改为章节规划运行并经过作者确认。
- 前端只调用场景运行入口，没有把章节入口接到主界面。
- 前端没有章节计划反馈/接受、章节队列、章节聚合、章节审校、章节版本接受和章节版本历史视图。
- 前端没有展示 affected_scene_ids、stale_scene_ids、chapter_sync_status、entry_handoff_status 等阻塞信息。
- 场景当前被当作作者必须手动创建和管理的主要层级，和“章节为作者工作单位、场景为 Agent 内部执行单位”的产品规则不一致。
- 当前 Playwright 测试覆盖资源、编辑器、场景运行和 Story Bible，缺少从章节意图到 Story Bible 的完整旅程。

### 3.3 当前实现状态

以下状态以当前仓库代码和最近一次验证结果为准；已有底层测试通过，不等于章节工作台主流程已经通过。

| 状态 | 当前内容 | 结论 |
| --- | --- | --- |
| 已实现并验证（独立能力） | 资源树、正文编辑、场景级运行、版本治理、Worker、事件、checkpoint、Canon/Story Bible，以及 Provider 接线、结构化输出 schema 校验、错误映射/重试和幂等恢复测试 | 作为二阶建设的复用底座；这里的“验证”是模块、API 或独立流程测试通过，不代表章节主流程已完成 |
| 已有代码和独立测试，但未形成主流程 | `ChapterPlannerAgent`、`ChapterReviewAgent`、`ChapterAggregator`、`ChapterGraph`、章节计划版本和场景物化领域服务。章节图已有 Planner→Review→Aggregator 的编排和暂停恢复测试，但 Worker 默认章节图尚未接入 `ChapterAggregator` | 可以复用领域和运行骨架；必须补齐计划候选持久化、作者决策、场景队列和真实状态衔接，不能把现有 Agent 单测当作主流程完成 |
| 章节规划运行入口部分存在，但字段链未接通 | `POST /api/chapters/{chapter_id}/runs` 已支持创建 `run_scope=chapter`、`request_type=new_chapter`、`decision_target=plan` 的排队运行，也会保存 `chapter_intent`；但 Worker 重建 `AgentInputEnvelope` 时仍主要从已接受计划读取 `chapter_contract`，`ChapterPlannerAgent` 仍只读取 `chapter_contract`，未接入 `chapter_intent`、讨论历史和待确认建议 | 入口不能独立证明真实章节规划可用；必须完成字段适配、Planner prompt/Hook、多轮恢复和计划接受链 |
| 迁移中的冲突 | 当前前端仍调用 `createChapterPlan()` 对应的 `POST /api/chapters/{chapter_id}/plan`。该接口读取已有场景，将其映射进计划并自动接受；前端已有“生成章节计划”按钮和初始化结果展示，但没有调用章节规划运行 | 新 UI 和新测试必须迁移到真实章节规划运行；完成完整验收后删除该 POST 接口及其初始化测试 |
| 尚未形成二阶主流程 | 自然语言意图到 Planner 的可用接入、同一 Planner 多轮讨论、建议确认、`ChapterWorkflowRead`、章节工作台、accepted plan 后的自动场景队列、逐场决策、章节聚合/审校/接受串联，以及从意图到 Story Bible 的完整 Playwright 旅程 | 这些属于二阶本次必须交付的端到端范围；已有局部组件或兼容接口不能替代它们 |

上表中的“已实现并验证”仅表示对应底层能力或独立测试已有证据，不应被前端现状、章节图单测或兼容接口的可用性替代。当前章节运行 API 能够创建并排队一次运行，章节图也能够测试暂停/恢复，但这不能证明作者输入的意图已经进入 Planner、计划已经持久化并等待作者确认。尤其是“生成章节计划”按钮当前能够展示初始化结果，只能证明兼容链路可用，不能证明作者已经完成 AI 章节规划、计划审阅和接受闭环。

### 3.4 现有契约与目标契约对照

下表用于指导实现，不把目标字段误认为现有字段。实现时必须先完成目标契约，再接入前端和 Worker；只修改提示词或只增加页面字段都不算完成。

| 契约 | 现有实现 | 目标实现 | 必须补齐的差异 |
| --- | --- | --- | --- |
| 章节意图 | `backend/app/api/schemas.py` 的 `ChapterIntent` 已有 `text`、`pov`、开场/结尾状态、必达和禁止剧情点，但字段允许为空；章节创建只把它保存到 `Chapter.chapter_intent` | `new_chapter` 运行至少要求非空 `chapter_intent.text`；其余字段可选，并区分作者已确认、AI 建议和未确定 | 收紧新建规划入口的输入校验，保留旧数据兼容读取，禁止用默认值静默补全 |
| Planner 输入 | `AgentInputEnvelope` 有 `chapter_contract` 和 `author_feedback`，没有独立 `chapter_intent`、`plan_discussion`、待确认建议和作者决策；`/runs` 的 `normalized_input` 虽然会保存 `chapter_intent`，但 Worker 重建信封时没有把它投影进去；`ChapterPlannerAgent._build_prompt()` 当前只注入项目、章节契约和来源引用 | 同一章节上下文携带意图、讨论消息、待回答问题、待确认建议、作者回答/采纳/修改/拒绝记录 | 扩展输入 schema、持久化讨论记录和恢复快照，补齐服务端字段投影，并把这些内容完整注入 Planner prompt |
| Planner 输出 | `ChapterPlanOutput` 当前字段为 `status`、`chapter_contract`、`scene_contracts`、`reason`、`clarification_questions`；没有建议来源、确认状态和未解决假设 | 输出 `ChapterContract`、有序 `SceneBrief[]`、澄清问题、可编辑建议、未解决假设和来源/确认标记；未确认建议不得进入 accepted plan | 明确 `scene_contracts` 到规范化 `SceneBrief[]` 的映射，增加建议确认字段和 schema/Hook 校验 |
| 计划持久化与决策 | 领域层已有计划修订、接受指针和场景物化事务；通用决策接口已有 `target=plan`、`plan_revision_id` 和 CAS 接受逻辑，但尚未接收并持久化 Planner 产生的候选计划；`POST /api/chapters/{chapter_id}/plan` 仍直接把已有场景初始化为计划并接受 | `/runs` 负责真实规划运行，候选计划先等待作者决策；决策命令接受具体 `plan_revision_id` 后才冻结 accepted plan 并物化场景 | 将 Planner 候选输出接入计划修订持久化，再将前端和新测试迁移到 `new_chapter + decision_target=plan`，保留不可变版本和幂等边界 |
| 章节读取视图 | `GET /api/chapters/{chapter_id}/plan` 只返回当前 accepted plan 指针、版本、契约和理由 | `GET /api/chapters/{chapter_id}/workflow` 一次返回阶段、讨论、计划、场景队列、运行、阻塞、章节版本和 Canon 来源 | 新增 `ChapterWorkflowRead` 服务端组合视图，前端不得继续拼接多个“最新记录”接口 |
| 前端入口 | `frontend/src/services/api.ts` 的 `createChapterPlan()` 调用旧 POST；页面随后使用场景级运行入口 | `ChapterWorkspace` 以章节为工作单位，提供意图、讨论、计划接受、场景队列、章节审校、版本和 Canon 决策 | 删除主界面对 `createChapterPlan()` 的依赖，接入 workflow 读取和章节决策状态机 |
| 验收证据 | 已有 Agent Provider/schema、章节图暂停恢复、初始化 API、`/runs` 输入持久化/决策状态和场景运行/Story Bible 测试；这些测试验证的是局部契约，没有多轮 Planner 和意图到 Story Bible 的完整旅程 | API、状态转移、建议确认、旧接口删除、前后端主流程和 Playwright 全旅程均有可重复验证 | 新增针对目标契约的测试，且不能用旧初始化接口或局部 `/runs` 测试替代主流程验收 |

本表是“设计实现”约束：目标契约必须落到后端 schema/服务、前端状态和 Worker 行为，并由测试证明；只更新本计划书不计入任何一项功能完成。

### 3.5 修订后的最小实施闭环与前置门槛

本计划不再把“阶段 1 的完整章节规划”作为第一条可验证路径，而是先完成一条可恢复的最小闭环：

```text
自然语言意图
  -> new_chapter 运行
  -> Planner 候选计划持久化（pending）
  -> ChapterWorkflowRead 返回讨论/问题/候选
  -> 作者接受指定 plan_revision_id
  -> 同一事务写入 accepted 指针、场景映射和 outbox
  -> 队列恢复并启动一个确定性场景运行
```

阶段 0 在进入阶段 1 前必须完成以下前置门槛：

持久化对象固定为：`chapter_plan_revisions`（候选契约、SceneBrief、来源和候选版本）、`chapter_plan_discussion_messages`（消息正文和顺序）、`chapter_plan_questions`（问题状态）、`chapter_plan_proposals`（建议状态和字段路径）以及 `chapter_plan_scene_links`（`plan_revision_id`、`client_key`、`scene_id`、`sort_order`）。这些对象必须通过迁移创建；不能把前端内存、checkpoint 或观测记录当作唯一业务存储。

1. **候选计划持久化**：`ChapterPlanRevision` 必须能保存规范化 `ChapterContract`、有序 `SceneBrief[]`、字段来源、未解决假设、来源运行 ID 和候选版本；Planner 输出、运行状态、checkpoint 和候选 revision 必须在可重试边界内关联。候选状态只能是 `pending`，不得直接写 accepted。
2. **讨论持久化**：新增章节规划血缘下的消息、问题和建议持久化记录。消息由服务端生成不可变 `message_id` 和单调 `message_sequence`；`question_id`、`proposal_id` 在同一规划血缘内稳定，反馈重放不得生成新语义 ID。
3. **场景映射持久化**：计划接受后必须保存 `plan_revision_id + client_key + scene_id + sort_order` 的固定映射。队列和章节聚合只读取该映射，不按场景创建时间推断顺序。
4. **子运行事务**：计划反馈必须在锁定父运行和幂等命令的同一事务中写入作者消息、问题/建议决策并创建带 `parent_generation_run_id`、`supersedes_run_id`、`parent_plan_revision_id` 的 Planner 子运行；同一命令重放返回同一子运行。
5. **权威读取规则**：workflow 读取只使用显式 accepted 指针、场景映射和非终态运行集合；同一章节出现两个互相冲突的活动运行时返回 `blocked`，不得按最新 `created_at` 静默选择。
6. **基础回归**：在主流程接线前修复 accepted 指针读取、已接受旧计划的 CAS 幂等边界，以及章节版本的真实 `scene_id` 映射，并为每项增加至少一个回归测试。

阶段 0 的唯一退出标准是：在不依赖真实外部模型的确定性 fixture 中，能够创建候选、刷新 workflow、接受计划、重放 `chapter_plan.accepted`，并在 Worker 重启后恢复第一个场景运行。只通过 schema 单测或旧初始化接口，不得退出阶段 0。

## 4. 目标用户流程

~~~mermaid
flowchart TD
    A[作者打开章节工作区] --> B[输入自然语言意图<br/>结构化字段可选]
    B --> C[启动章节规划运行]
    C --> D[ChapterPlannerAgent 生成并展示 ChapterContract 和有序 SceneBrief]
    D --> E{作者审阅章节计划}
    E -->|反馈| C1[计划反馈命令：同一规划血缘创建 Planner 子运行]
    C1 --> D
    E -->|需要澄清| E1[ChapterPlannerAgent 提出问题或契约建议]
    E1 --> E2[作者回答、采纳或修改]
    E2 --> E3[计划决策命令恢复同血缘 Planner 子运行]
    E3 --> D
    E -->|取消| X[运行取消并丢弃未决候选]
    E -->|接受| F[作者确认，冻结 accepted plan 并建立场景映射]

    F --> G{还有未完成场景?}
    G -->|有| H[系统按计划顺序自动生成当前场景正文]
    H --> I[WritingAgent 生成当前场景正文候选]
    I --> I1[确定性检查与规则校验]
    I1 --> I2[ContinuityAgent 连续性检查]
    I2 --> I3[ReviewAgent 场景质量审校]
    I3 --> J{作者审阅场景结果}
    J -->|反馈| K[RevisionAgent 生成补丁]
    K --> I
    J -->|澄清| H
    J -->|取消| X
    J -->|接受| G

    G -->|无| L[ChapterAggregator（确定性领域服务）生成 staged ChapterRevision]
    L --> M[ChapterReviewAgent 章节审校]
    M --> N{作者审阅章节结果}
    N -->|修改| O[计算影响闭包并重跑场景]
    O --> H
    N -->|重新规划| C1
    N -->|取消| X
    N -->|接受| P[提交 accepted ChapterRevision]

    P --> Q[幂等创建章节 Canon 运行]
    Q --> R[CanonAgent 生成候选]
    R --> S{作者逐条决策}
    S -->|反馈| R
    S -->|确认| T1[候选 accepted，并物化正式 Story Bible]
    S -->|拒绝| T2[候选 rejected，不写正式 Story Bible]
    S -->|暂缓| T3[候选 deferred，不写正式 Story Bible]
    S -->|取消| U[保留未决候选]
    T1 --> V[完成章节任务]
    T2 --> V
    T3 --> V
    U --> V
~~~

本流程包含 7 个业务 Agent：`ChapterPlannerAgent`、`WritingAgent`、`ContinuityAgent`、`ReviewAgent`、`RevisionAgent`、`ChapterReviewAgent` 和 `CanonAgent`。`ChapterAggregator` 是不调用模型的确定性领域服务，不计入 Agent 数量；确定性检查、规则引擎、Hook、Worker 和 checkpoint 也不是独立 Agent。规划澄清和计划反馈始终恢复同一个 `ChapterPlannerAgent`，不新增通用聊天 Agent。

图中 `C` 只表示首次规划入口。计划反馈和章节重新规划必须走当前运行的决策命令，在同一章节、同一计划血缘下创建带 `parent_run_id`、`supersedes_run_id` 和 `parent_plan_revision_id` 的 Planner 子运行；不得重新提交一个无父级的首次规划运行。Canon 只有 `confirm` 会物化正式 Story Bible，`reject` 和 `defer` 只更新候选状态。

### 4.1 主流程与独立操作的边界

主流程以章节为入口，场景由计划和 Worker 队列推进。作者可以在章节工作区内定位某个场景，但不需要通过“新建场景”手动拼接章节主结构。

在主流程中，SceneBrief[] 是场景清单的唯一来源：规划阶段只展示候选场景计划，计划接受后才创建/映射主流程场景，Worker 按清单顺序推进正文生成。作者可以通过反馈修改计划或审阅单个场景，但不能通过手动新建、插入或跳过场景来绕过 accepted plan。现有手动场景接口仅作为兼容和独立场景操作保留，不得把未纳入 accepted plan 的场景加入章节主队列。

场景续写、改写、审校和场景级 Canon 仍保留为独立操作。独立操作必须明确显示其不属于当前章节主流程，并要求携带正确的计划版本和场景基线。

### 4.2 反馈语义

- 计划反馈：作者回答 Planner 的问题，或采纳、修改、拒绝建议；系统通过当前运行的计划决策命令，在同一章节规划血缘中创建 Planner 子运行，生成新的不可变候选计划版本，只有作者接受指定版本后才形成 accepted plan。
- 场景反馈：调用 RevisionAgent，只生成当前场景补丁并重新检查。
- 章节反馈：先计算入口/出口状态影响闭包，再决定场景重跑或 stale 阻断。
- Canon 反馈：只写入 Canon 运行反馈，不修改正文和章节版本。
- feedback 永远不是运行终态；它不能跨章节或跨计划血缘拼接讨论。只有作者接受、取消或系统失败才结束当前决策阶段。

## 5. 工作流状态与读取模型

### 5.1 章节工作流状态

章节工作流视图使用服务端计算结果，不新增与领域状态冲突的前端状态。对作者展示以下阶段：

| 阶段 | 含义 | 主动作 |
| --- | --- | --- |
| intent_required | 没有可供规划的自然语言意图 | 输入一句想法并启动规划 |
| planning | 规划运行中 | 查看进度 |
| plan_feedback | 候选计划等待作者决策，真实场景尚未物化；若 `pending_decision.kind=answer_planner`，表示当前仍需回答 Planner | 由 `pending_decision.kind` 决定接受计划或回答 Planner，反馈、取消为次级动作 |
| scene_generation | 按 accepted plan 的顺序处理已物化场景 | 查看当前场景 |
| scene_feedback | 场景等待作者决策；若场景状态为 `pending_clarification`，表示需要回答当前场景问题 | 由 `pending_decision.kind` 决定接受场景或回答场景问题，反馈、取消为次级动作 |
| chapter_review | 所有计划场景已接受，章节已聚合并完成审校 | 查看问题、反馈、接受 |
| chapter_feedback | 章节存在影响闭包或重跑任务 | 处理受影响场景 |
| canon_feedback | Canon 候选等待决策 | 逐条确认、拒绝、暂缓 |
| completed | 章节版本已接受，Canon 候选均已确认、拒绝或暂缓 | 查看历史 |
| blocked | 存在 stale、out_of_sync、handoff 冲突或失败 | 查看阻塞原因 |

### 5.2 ChapterWorkflowRead

新增章节工作台读取视图，以下为前后端必须共同遵守的规范结构：

~~~typescript
type ChapterWorkflowRead = {
  chapter_id: string;
  phase:
    | \"intent_required\" | \"planning\" | \"plan_feedback\" | \"scene_generation\"
    | \"scene_feedback\" | \"chapter_review\" | \"chapter_feedback\" | \"canon_feedback\"
    | \"completed\" | \"blocked\";
  chapter_status: string;
  pending_decision: {
    target: \"plan\" | \"scene\" | \"chapter\" | \"canon\" | null;
    kind:
      | \"start_planning\" | \"answer_planner\" | \"accept_plan\"
      | \"answer_scene\" | \"accept_scene\" | \"chapter_feedback\"
      | \"accept_chapter\" | \"canon_decisions\" | null;
    run_id: string | null;
    expected_run_version: number | null;
  };
  intent: {
    text: string;
    optional_fields: Record<string, unknown>;
    unresolved_questions: string[];
  };
  plan_discussion: {
    messages: Array<{
      message_id: string;
      message_sequence: number;
      role: \"author\" | \"planner\";
      agent: \"ChapterPlannerAgent\" | null;
      kind: \"intent\" | \"question\" | \"answer\" | \"feedback\" | \"proposal\" | \"decision\";
      text: string;
      created_at: string;
      source_run_id: string | null;
      parent_run_id: string | null;
      supersedes_run_id: string | null;
      checkpoint_id: string | null;
    }>;
    pending_questions: Array<{
      question_id: string;
      text: string;
      impact: string;
    }>;
    pending_proposals: Array<{
      proposal_id: string;
      field_path: string;
      value: unknown;
      source: \"ai\";
      status: \"pending\" | \"accepted\" | \"modified\" | \"rejected\";
      rationale: string;
    }>;
  };
  plan: {
    candidate_revision_id: string | null;
    accepted_revision_id: string | null;
    candidate_version: number | null;
    accepted_version: number | null;
    status: \"none\" | \"candidate\" | \"accepted\" | \"stale\";
    contract: Record<string, unknown> | null;
    contract_field_provenance: Record<string, PlanFieldProvenance>;
    scene_briefs: Array<{
      client_key: string;
      order: number;
      title: string;
      brief: Record<string, unknown>;
      field_provenance: Record<string, PlanFieldProvenance>;
      status: \"proposed\" | \"accepted\" | \"stale\";
    }>;
  };
  scenes: Array<{
    scene_id: string;
    order: number;
    title: string;
    status:
      | \"planned\" | \"generating\" | \"waiting_feedback\" | \"pending_clarification\"
      | \"accepted\" | \"stale\" | \"blocked\";
    accepted_revision_id: string | null;
    current_run_id: string | null;
    blocking_reasons: string[];
  }>;
  chapter_revision: {
    staged_revision_id: string | null;
    accepted_revision_id: string | null;
    review_run_id: string | null;
  };
  active_run: (RunSnapshot & {
    decision_target: \"plan\" | \"scene\" | \"chapter\" | \"canon\";
  }) | null;
  affected_scene_ids: string[];
  stale_scene_ids: string[];
  blocking_reasons: string[];
  canon_run_id: string | null;
};

type PlanFieldProvenance = {
  status: \"author_confirmed\" | \"ai_suggested\" | \"unresolved\" | \"explicitly_omitted\";
  source: \"author\" | \"ai\" | \"merged\";
  source_message_id: string | null;
  proposal_id: string | null;
};
~~~

`plan.scene_briefs` 在计划尚未接受时用于展示 Planner 生成的候选清单；只有作者接受计划后，领域服务才物化 `scenes` 中的真实场景。服务端对 accepted plan、accepted scene revision、accepted chapter revision 和有效 handoff 使用权威指针，不能按“最新记录”推断。候选计划、讨论消息和待确认建议必须绑定同一章节规划血缘，并通过来源运行、父运行、被取代运行和 checkpoint 追溯恢复，不能伪装成 accepted 数据。

`message_id`、`question_id` 和 `proposal_id` 均由服务端生成，创建后不可修改；同一规划血缘下的反馈运行只能复用已有问题/建议 ID，不能由客户端重新命名。`message_sequence` 在章节规划血缘内由数据库事务分配，workflow 读取、SSE 刷新和跨子运行恢复均按该序号排列。`candidate_version` 表示候选计划在同一规划血缘内的单调版本，`accepted_version` 表示 accepted pointer 的计划版本；两者不能复用同一个“最新行”推断规则。

`pending_decision` 是工作台主动作的唯一依据：`answer_planner` 和 `answer_scene` 只能提交回答并恢复对应运行；`accept_plan` 和 `accept_scene` 才允许提交接受命令。`contract_field_provenance` 和 `field_provenance` 必须覆盖进入候选契约或 SceneBrief 的字段；接受计划时，字段必须为 `author_confirmed` 或 `explicitly_omitted`，AI 建议不能仅因出现在候选 JSON 中而视为已确认。

## 6. 后端设计

### 6.1 章节规划命令

将当前初始化接口降级为兼容/测试用途，主界面改用章节运行入口：

所有运行和决策命令必须沿用现有命令基础设施：请求携带 `Idempotency-Key`，服务端按请求指纹保证重复提交返回同一结果；决策必须携带 `expected_run_version`，计划接受还必须校验 `expected_current_plan_revision_id` 和 `expected_plan_version`。计划、场景和章节版本写入必须携带对应的版本基线、运行身份和 fencing 信息；发生版本冲突、旧运行写入或 fencing 失败时拒绝提交，不得覆盖新结果。

- POST /api/chapters/{chapter_id}/runs
  - 首次规划：run_scope=chapter、request_type=new_chapter、decision_target=plan，携带至少一个非空的 chapter_intent.text；结构化字段可选。
  - 已接受计划后的继续生成：显式携带 plan_revision_id，服务端校验等于当前 accepted plan。
  - 章节审校：request_type=review、decision_target=chapter，不能调用 WritingAgent。
- POST /api/runs/{run_id}/decisions
  - 扩展 target 为 plan|scene|chapter，保持 accept|feedback|cancel 状态机。
  - feedback 可以是作者自然语言回答，也可以是对 AI 契约建议的采纳、修改或拒绝；服务端必须保存完整的、可恢复的讨论记录，不得只保存反馈哈希。
  - 计划接受必须提交服务端产生的 plan_revision_id。
  - 章节接受必须提交服务端产生的 chapter_revision_id。

计划决策请求必须使用以下规范字段；不得用场景 `operations` 或无结构的 `selection` 代替：

~~~typescript
type PlanDecisionRequest = {
  target: \"plan\";
  decision: \"feedback\" | \"accept\" | \"cancel\";
  expected_run_version: number;
  expected_current_plan_revision_id: string | null;
  expected_plan_version: number;
  plan_revision_id: string | null;
  feedback: {
    kind: \"answer\" | \"proposal_review\" | \"replan\";
    text: string;
    answers: Array<{ question_id: string; text: string }>;
    proposals: Array<{
      proposal_id: string;
      action: \"accept\" | \"modify\" | \"reject\";
      field_path: string;
      value: unknown;
      expected_status: \"pending\";
    }>;
  } | null;
};
~~~

`feedback` 时必须创建同一规划血缘的 Planner 子运行，并保存问题/建议决策；此时 `plan_revision_id` 可以为空（例如首次澄清尚未形成候选计划）。`accept` 时必须提交非空候选 `plan_revision_id`，并校验 CAS 版本和全部字段来源。可选字段如果作者不采纳，必须标记为 `explicitly_omitted` 或明确拒绝，不能静默使用 AI 值。章节 `feedback` 也必须携带 `chapter_revision_id`、反馈正文和版本基线，服务端返回并持久化 `affected_scene_ids`、`stale_scene_ids` 与下一步阶段。
- GET /api/chapters/{chapter_id}/plan
  - 过渡期只读当前 accepted plan 指针，不创建计划、不触发 Agent；待 workflow 完全接管前端读取后，再决定是否一并移除。
- GET /api/chapters/{chapter_id}/workflow
  - 返回 ChapterWorkflowRead，供章节工作区首屏和 SSE 后刷新使用。
- GET /api/chapters/{chapter_id}/revisions
  - 继续复用现有章节版本读取接口，并补齐版本中场景版本映射和审校摘要。
- POST /api/chapters/{chapter_id}/rollback
  - 继续使用显式目标版本和作者决策，回滚后重新计算同步状态和 handoff 影响。

### 6.1.1 ChapterPlannerAgent 输入、提示词与专属 Hook

阶段 1 必须把 Planner 的“讨论式规划”落成可执行代码，不得只修改页面文案或只增加一个聊天区域。

1. **输入契约**
   - 扩展 `AgentInputEnvelope`，增加规范化的 `chapter_intent`、`plan_discussion`、待回答问题、待确认建议和作者决策；保留 `chapter_contract` 作为当前候选契约，而不是把它当作作者首次必填表单。
   - `plan_discussion` 至少保存消息角色、消息类型、正文、来源运行/版本、创建时间和作者决策；恢复运行时必须从持久化记录重建，而不能依赖前端内存状态。
   - `author_feedback` 必须真正进入 Planner 的模型输入；回答、采纳、修改和拒绝都要能关联到对应问题或建议。

2. **提示词修改**
   - 重写 `backend/app/agents/prompts.py` 中的 `CHAPTER_PLAN_SYSTEM_PROMPT`，明确 Planner 的职责是与作者共同形成章节计划，不是静默补全表单。
   - 提示词必须说明：自然语言意图是最小输入；结构化字段可选；信息不足时先提问；AI 推断的目标、状态、人物关系、必达剧情点和场景拆解只能标记为建议；未经作者确认不得视为正式契约或 accepted plan。
    - 明确 JSON 输出中的 `ChapterContract`、有序 `SceneBrief[]`、澄清问题、建议、未解决假设、建议来源和确认状态。每个建议必须有稳定的 `proposal_id`、`field_path`、`source` 和 `pending` 状态；契约字段和 SceneBrief 字段必须带字段来源。`needs_clarification` 必须携带可回答的问题，允许暂时没有或只有部分 `SceneBrief[]`；`ready` 才要求返回完整、唯一且有序的 `SceneBrief[]`，并且不能隐藏未确认的关键假设。
   - 修改 `ChapterPlannerAgent._build_prompt()`，按稳定顺序注入章节意图、讨论历史、待回答问题、待确认建议、作者反馈、当前候选契约和上下文来源，并限制长度；不得只注入当前 `chapter_contract`。

3. **PlannerDiscussionHook**
   - 新增并注册 `PlannerDiscussionHook`，接入 `ChapterPlannerAgent` 的结果校验和章节图路由；它不直接写正式计划，只负责讨论上下文归一化和边界校验。
    - 输入侧：整理本轮意图、历史消息、作者反馈和待决建议；拒绝跨章节或跨计划血缘的讨论记录，但允许同一章节、同一规划血缘下由初次运行、反馈运行或重规划运行共同恢复讨论。每条消息必须保留来源运行、父运行/被取代运行和 checkpoint 关联，恢复时按持久化顺序重建输入。
    - 输出侧：校验澄清问题非空、建议带稳定 `proposal_id`、字段路径、来源和 `pending` 确认状态、建议引用可追溯上下文，并把未解决假设显式返回。`ready` 状态下必须校验 `SceneBrief[]` 完整、唯一且有序；`needs_clarification` 状态只要求问题可回答，不得因暂时没有完整场景清单而误报失败。
    - 决策侧：进入 `accepted plan` 的每个契约字段和每个 `SceneBrief` 必须是作者已提供或明确确认的内容。未确认的关键建议、阻断假设或待回答问题阻止接受；未确认的非关键建议必须留在候选/待确认区，或被明确排除，不能静默写入 accepted plan。
    - 执行侧：现有 `AgentCallable` 的生命周期只有通用 `before/after`，不足以在路由前阻断 Planner 结果；必须扩展 `HookRegistry`/`AgentCallable` 提供结果校验阶段，固定顺序为 `before → Agent → SchemaHook → PlannerDiscussionHook → AgentResultRouter → after`。Planner Hook 不能在 `after` 阶段才执行，否则无法阻断错误路由。
   - 与现有 `SchemaHook`、`AgentResultRouter`、`ChapterGraph` 和 checkpoint 协作，不由 Hook 创建计划修订、物化场景或直接写入 Canon。

4. **测试与接线**
   - 更新 `backend/tests/agents/test_prompt_contracts.py`，覆盖提示词对自然语言起步、可选字段、建议来源、确认状态和禁止静默补全的约束。
   - 新增 Planner 讨论行为测试，覆盖首次规划、缺字段提问、作者反馈注入、建议采纳/修改/拒绝、暂停恢复、未确认建议阻断和有序 `SceneBrief[]` 校验；可放在 `backend/tests/agents/test_chapter_planner_discussion.py`。
   - 更新 `backend/tests/agents/test_chapter_agents_provider.py`、章节运行时测试和 `frontend/tests/chapter-workflow.spec.ts`，证明作者的回答确实进入下一轮 Planner，并最终只能由作者决策形成 accepted plan。

### 6.1.2 影响边界与兼容迁移规则

二阶实现允许改变章节规划主流程，但不允许把 Planner 的改造扩散成全系统 Agent 或编辑器重写。所有跨模块变更按以下边界执行：

| 部件 | 允许影响 | 禁止影响 | 迁移与验证要求 |
| --- | --- | --- | --- |
| `AgentInputEnvelope` | 以新增可选字段方式增加章节意图、讨论和建议上下文 | 不得删除或改变场景 Agent 现有字段语义；旧 envelope 在非 Planner 场景中必须继续可用 | 先补默认值和兼容构造测试，再迁移 Planner 调用方；全量 Agent 测试必须通过 |
| `ChapterPlanOutput` | 扩展 Planner 的建议、来源、确认状态和规范化 `SceneBrief[]` | 不得把 Planner 输出契约改成其他 Agent 共用的输出格式；不得让未确认建议伪装成正式契约 | 只在 Planner schema/适配层完成映射；补合法/非法响应和版本兼容测试 |
| `CHAPTER_PLAN_SYSTEM_PROMPT` 与 `_build_prompt()` | 改变章节规划模型的输入语义和讨论行为 | 不得改变 Writing、Revision、Review、Canon 的 system prompt 或模型调用协议 | Provider 协议、埋点、错误映射和重试策略保持不变；提示词契约测试必须单独覆盖 |
| `PlannerDiscussionHook` | 只在章节 Planner 结果链路执行问题、建议、来源和确认校验 | 不得替代通用 `SchemaHook`，不得改变其他场景图 Hook 顺序或直接写数据库 | 扩展 `HookRegistry`/`AgentCallable` 提供路由前结果校验；执行顺序为 SchemaHook → PlannerDiscussionHook → AgentResultRouter，Hook 行为测试覆盖 pending、feedback、ready 和阻断 |
| `ChapterGraph`、`AgentResultRouter`、checkpoint | 增加 Planner 多轮暂停、恢复和计划反馈路由 | 不得改变场景图已有暂停、恢复、取消和 fencing 语义 | 章节图测试验证同一运行/章节上下文恢复；场景图回归测试保持通过 |
| 章节 API、计划事务和 Worker | 增加真实 `new_chapter` 规划、计划决策、accepted plan 队列和顺序推进 | 不得让未接受计划启动主流程场景；不得破坏独立场景运行、版本血缘、幂等和租约 | 新入口先与旧初始化接口并存；通过完整 Playwright 和 API 回归后才删除旧 POST |
| 前端章节工作台 | 新增章节意图、Planner 讨论、计划审阅、场景队列、章节审校和 Canon 入口 | 不得把现有单场景编辑器、版本比较、回滚和删除操作改成章节主流程依赖 | workflow 视图接管章节状态；现有编辑器、运行、Story Bible 和删除测试必须通过 |
| 共享 Provider、数据库和领域模型 | 复用现有 Provider、事务、事件、版本和 fencing 机制 | 不得新增第二套模型调用协议、覆盖旧版本或绕过领域事务直接写正式结果 | 只做增量 schema/迁移；验证服务重启后状态、事件、版本和来源仍一致 |

**兼容迁移规则：**

1. 先新增目标字段、workflow 读取接口和真实规划运行入口，旧字段和旧只读读取保持可用；禁止先删除旧入口再迁移前端。
2. Planner 以外的 Agent 继续使用原有 `AgentInputEnvelope` 默认值和输出契约；只有 Planner 调用显式启用讨论上下文和专属 Hook。
3. `PlannerDiscussionHook` 必须在结果路由前执行，但不直接接受计划；只有作者决策命令在事务中更新 accepted 指针，Worker 才能物化和推进场景。跨运行恢复只允许发生在同一章节和同一计划血缘内，不能用新的无关运行拼接旧讨论。
4. 迁移期间同时验证旧数据读取、新数据写入、暂停恢复和幂等重放；任一边界验证失败，保留兼容入口并停止删除步骤。
5. 删除 `POST /api/chapters/{chapter_id}/plan` 前，必须确认生产代码、前端、测试和 OpenAPI 均无调用方，并完成第 6.4 节的搜索、全量测试、类型检查和完整 Playwright 验收。

### 6.1.3 Agent 职责收紧与复用边界

章节工作台不通过扩展所有 Agent 来实现。只有 `ChapterPlannerAgent` 获得章节讨论能力；其他 Agent 的职责、输入范围和输出边界如下：

| Agent/组件 | 只负责 | 不负责 | 本次允许的变化 |
| --- | --- | --- | --- |
| `ChapterPlannerAgent` | 接收章节意图，与作者多轮讨论，生成 `ChapterContract` 和有序 `SceneBrief[]` 候选 | 不直接写计划、创建场景或写 Canon | 扩展输入上下文、提示词、输出 schema 和 `PlannerDiscussionHook` |
| `WritingAgent` | 根据 accepted `SceneBrief` 和场景上下文生成正文候选 | 不解释章节总目标、不调整计划顺序、不与作者进行章节规划讨论 | 仅适配 accepted plan/SceneBrief 版本指针，保持现有写作提示词和输出契约 |
| `ContinuityAgent` | 检查场景与前后文、Canon 和规则的一致性 | 不生成章节计划、不替作者确认建议 | 保持现有检查边界，补齐计划版本和来源引用即可 |
| `ReviewAgent` | 对当前场景进行质量审校并返回问题 | 不把场景问题扩展成章节重规划 | 保持现有提示词、schema 和运行路由 |
| `RevisionAgent` | 根据当前场景反馈生成正文补丁并重新检查 | 不修改其他场景、不重写章节计划 | 继续绑定当前场景 revision 和 accepted `SceneBrief` |
| `ChapterReviewAgent` | 对聚合后的 `ChapterRevision` 进行章节级审校 | 不生成 `SceneBrief`，不替代场景审校，不进行 Planner 对话 | 保持章节审校职责，只接收章节版本和影响范围 |
| `ChapterAggregator` | 按确定性规则聚合已接受场景版本，生成 staged 章节版本 | 不调用模型、不提出规划建议 | 复用现有领域服务，只增加 accepted plan/场景资格校验 |
| `CanonAgent` | 从已接受场景或章节版本提取 Canon 候选，等待作者逐条决策 | 不参与章节计划讨论，不直接把候选写成正式 Canon | 保持现有 scope、来源版本和确认事务边界 |

**收紧规则：**

1. 其他 Agent 不新增 `chapter_intent` 多轮对话字段，不新增 Planner 专属建议确认逻辑；需要的章节上下文由服务端按其职责投影为只读输入。
2. 其他 Agent 的提示词只做必要的 accepted `SceneBrief`、revision、来源和作用域适配，不因二阶规划需求整体重写。
3. `ChapterPlannerAgent` 输出 accepted plan 后，后续 Agent 只能消费 accepted plan 及其有序 `SceneBrief[]`；任何 Agent 都不能自行新增、插入、跳过或重排章节主流程场景。
4. `SchemaHook`、`AgentResultRouter`、`AgentCallable`、Provider、checkpoint 和 fencing 等通用基础设施继续复用；Planner 专属行为只通过 `PlannerDiscussionHook` 和章节图接线实现。
5. 阶段验收必须同时证明 Planner 新逻辑生效、其他 Agent 旧职责未扩大、场景图和 Canon 图回归通过；“所有 Agent 都改成支持聊天”视为范围扩张，不属于本计划。

### 6.1.4 共享信封与 Agent 字段投影

`AgentInputEnvelope` 是运行层的共享传输信封，不等于每个 Agent 的 Prompt 输入。运行层可以保留完整信封用于身份、版本、租约和恢复；调用具体 Agent 前，服务端必须按职责投影字段，禁止把 Planner 的讨论上下文无差别传给场景或 Canon Agent。

字段边界按以下三层执行：

```text
共享运行信封
    ↓
服务端按 Agent 投影字段
    ↓
Agent 专属 Prompt / schema 输入
```

| 字段 | 当前代码用途 | 目标投影范围 | 约束 |
| --- | --- | --- | --- |
| `chapter_intent` | 当前不在 `AgentInputEnvelope` 中；章节创建接口把意图保存到章节 JSON | 只投影给 `ChapterPlannerAgent` 和 Planner Hook | 新增为可选字段；`new_chapter` 时由入口校验非空 `text`，其他 Agent 不读取 |
| `plan_discussion` | 当前不存在独立字段；章节图只在恢复时更新 `author_feedback` | 只投影给 `ChapterPlannerAgent`、Planner Hook 和章节工作流读取视图 | 必须持久化、可恢复；不得进入 Writing、Continuity、Review、Revision 或 Canon Prompt |
| `pending_proposals` / 未解决假设 | 当前 `ChapterPlanOutput` 没有确认状态字段 | 只投影给 Planner Hook、计划审阅 UI 和 Planner 下一轮输入 | 必须带来源和确认状态；未确认关键建议阻断 accepted plan |
| `author_feedback` | `WritingAgent`、`RevisionAgent` 已读取；章节图会把反馈放回 envelope，但 Planner 当前 Prompt 未读取 | Planner 读取计划反馈；Writing/Revision 保留现有场景反馈用途 | 按 `target` 投影，不能把场景反馈当成章节规划反馈 |
| `chapter_contract` | Worker 会装入运行信封；Planner 和 `ChapterReviewAgent` 当前读取 | Planner 使用候选契约；ChapterReview 使用已接受契约/章节审校上下文 | 其他场景 Agent 不注入 Prompt；不得把候选契约当作 accepted plan |
| `scene_brief` | `WritingAgent` 当前读取；其他场景 Agent 当前主要依赖正文和来源引用 | Writing 使用生成上下文；Continuity/Review/Revision 至少用于服务端绑定和资格校验，按需要投影只读摘要 | 必须绑定当前 `plan_revision_id` 和场景顺序；不能允许 Agent 自行重排或修改 |
| `plan_revision_id` | Worker、运行服务和图状态用于计划版本血缘；不是各 Agent 当前的 Prompt 字段 | 服务端、Worker、场景资格校验和事件审计 | 作为只读版本指针传递；除计划事务外任何 Agent 不得修改 |
| `accepted_scene_revision_id` | 场景运行基线和 Scene Canon 来源校验使用 | 场景运行服务、Scene Canon 和必要的场景 Prompt 来源 | 只作为已接受版本基线；不能被 Planner 当作章节计划输入 |
| `accepted_chapter_revision_id` | Chapter Canon 和章节来源校验使用 | Chapter Canon、章节审校/接受事务和 workflow 读取视图 | 只能指向 accepted ChapterRevision；没有该指针不能启动章节 Canon |
| `canon_scope` | CanonAgent 和 CanonGraph 用于区分 scene/chapter | 只投影给 Canon 链路 | 其他 Agent 不处理 Canon 作用域 |
| `accepted_text` / `draft_text` | Writing、Continuity、Review、Revision、Canon 按各自场景读取 | 对应正文生成、连续性检查、场景审校、修订和 Canon 投影 | 保持现有正文基线语义；Planner 不读取正文作为章节意图替代品 |

**实现要求：**

1. 扩展共享 `AgentInputEnvelope` 时使用可选字段和默认值，保证现有场景 Agent 的旧构造方式继续工作。
2. 新增服务端投影/适配函数，分别为 Planner、场景图、章节审校和 Canon 图构造最小输入；不要在每个 Agent 内自行从完整信封猜测权限边界。
3. Planner 讨论字段只进入 Planner Prompt；场景 Agent 只接收 accepted `SceneBrief`、当前正文基线、对应反馈和必要的版本来源。
4. `scene_brief` 目前只有 Writing Prompt 已使用的事实必须在计划中明确记录；Continuity、Review、Revision 是否加入 Prompt，必须根据其检查/修订职责单独验证，不能因为共享信封有字段就默认注入。
5. 增加字段投影测试和字段泄漏测试：验证 Planner 能收到意图/讨论，Writing/Revision 保留场景反馈，Canon 只收到对应 accepted revision 和 `canon_scope`，其他 Agent 不收到 `plan_discussion`。

### 6.1.5 候选计划、讨论与子运行的持久化事务

本节是阶段 0/1 的后端实现边界，解决“Agent 返回了候选但服务端没有可接受 revision”的问题。表名沿用 3.5 的固定命名；字段语义、唯一约束和事务边界不可省略。

1. **Planner 结果提交**：Worker 收到 `ChapterPlanOutput` 后，先完成 schema 和 `PlannerDiscussionHook` 校验；校验成功后，在同一业务事务中写入候选 `ChapterPlanRevision(status=pending)`、有序 `SceneBrief[]`、字段来源、未解决假设、来源 `generation_run_id` 和当前 checkpoint 关联，并把运行置为 `pending_clarification` 或 `waiting_feedback`。写入幂等键为 `(generation_run_id, agent_attempt_key, node_name)`；同一节点重试只能返回已有候选，不能产生第二个语义候选。
2. **讨论记录**：作者意图、Planner 问题/建议、作者回答和采纳/修改/拒绝都写入同一规划血缘的消息记录。正文保留在业务数据库，观测 sink 只作为附加出口，不能用反馈哈希替代讨论正文。每条记录必须保存 `message_id`、`message_sequence`、`source_run_id`、父/被取代运行、checkpoint 和关联问题/建议 ID。
3. **反馈与子运行**：`target=plan, decision=feedback` 时，锁定父运行、检查 `expected_run_version` 和命令幂等键，写入作者反馈及其问题/建议决策，然后创建唯一 Planner 子运行；子运行成功入队后，父运行标记为 `superseded`，但保留父子关系和原始 checkpoint 供追溯。子运行继承同一章节和计划血缘，但不得继承已被拒绝的建议状态。
4. **计划接受**：`target=plan, decision=accept` 时，锁定候选 revision 和章节计划 link，校验 `plan_revision_id`、`expected_current_plan_revision_id`、`expected_plan_version`、字段来源、运行版本和 fencing。只有校验通过，才在同一事务中更新 accepted pointer、写入 `chapter_plan_scene_links` 固定映射并发布 `chapter_plan.accepted` outbox。旧候选即使状态已经是 `accepted`，只要当前 pointer 不一致也必须返回冲突；只有同一命令的完全重放才返回原结果。
5. **workflow 读取**：`GET /api/chapters/{chapter_id}/workflow` 由一个组合查询服务读取上述持久化记录和显式版本指针，返回单一快照。发现两个活动 Planner/队列运行互相冲突、候选映射缺失或 accepted 指针悬空时返回 `blocked` 和可执行修复动作，不能自行选择“最新记录”。

### 6.2 Worker 与领域边界

- 章节后端必须按以下状态顺序编排，不能继续使用现有 `ChapterGraph` 的 Planner → ChapterReview → ChapterAggregator 直连路径：
  1. `ChapterPlannerAgent` 产出候选计划后，章节运行进入 `plan_feedback`，只保存候选计划和 checkpoint，不创建真实场景。
  2. 作者接受指定 `plan_revision_id` 后，计划领域服务在同一事务中更新 `accepted plan` 指针、物化场景映射，并写入幂等的 `chapter_plan.accepted` outbox 事件；事务成功后，队列消费者才创建或唤醒第一个场景运行。
  3. Worker 通过章节队列按 `SceneBrief.order` 逐个运行 `SceneGraph`；当前场景未完成作者接受、反馈处理或取消前，不得启动下一个场景。
  4. 所有计划场景都具备有效 accepted revision 后，`ChapterAggregator` 先生成 `staged ChapterRevision`，再由 `ChapterReviewAgent` 执行章节审校，最后进入章节接受决策。
  5. 章节版本接受事务发布 `chapter_revision.accepted` outbox 事件，Canon 服务幂等消费后创建章节 Canon 运行。
- `RunWorker._default_graph_builder` 当前默认把章节运行接入 `ChapterGraph`；实现时必须改为接入章节工作流控制器/队列编排，或重构 `ChapterGraph` 的边，使其明确等待计划接受和场景队列完成。仅在 `ChapterGraph` 中增加 Planner Hook，不足以完成主流程。
- `chapter_plan.accepted` outbox 消费必须可重试且幂等；如果事务已提交但 Worker 尚未领取事件，协调器必须能按 accepted plan 指针重建缺失的场景队列，不得留下“计划已接受但没有第一个场景运行”的孤儿状态。
- 章节队列控制器必须复用现有运行命令、租约、checkpoint、幂等和 fencing 机制；队列推进以服务端 accepted 指针为准，不能以 Worker 内存状态或最新运行记录推断。
- ChapterPlannerAgent 只返回结构化计划，不直接写数据库；Worker 在 Hook 校验成功后调用唯一的候选持久化服务写入 `ChapterPlanRevision` 和讨论记录，其他 Agent 不得绕过该服务写正式计划。
- 计划接受由计划领域服务在事务中更新 accepted 指针并物化场景映射。
- 章节主流程的场景清单来自 accepted plan 的 SceneBrief[]；计划接受事务负责物化场景映射，不能由前端逐个手动拼装。
- 场景队列由 Worker 根据 accepted plan 顺序推进；当前场景没有 accepted 结果时不能跳过、插入或直接运行下一个场景。
- 未纳入 accepted plan 的手动场景不得进入章节主队列；手动场景只能走独立场景操作，并携带明确的场景基线和计划版本（如适用）。
- ChapterAggregator 只在场景资格检查通过后生成 staged 章节版本，并且必须先于 `ChapterReviewAgent` 执行。
- ChapterReviewAgent 只负责聚合后章节版本的章节级审校，不能替代场景审校，也不能在计划接受或场景队列完成前运行。
- 章节接受事务只发布 chapter_revision.accepted outbox 事件；Canon 服务幂等消费该事件创建章节 Canon 运行。
- 非 Canon 运行取消、失败或被取代时，当前运行未决计划/正文候选按各自领域规则转为 `discarded`；Canon 运行取消时，未决候选保留 `pending`，不写入正式 Canon；已接受版本不受影响。
- LangSmith 不属于业务 Agent 或章节状态机节点，不得作为运行成功、暂停恢复、作者决策或版本提交的前置依赖。观测发送失败只记录本地降级信息，不触发业务重试；脱敏失败则禁止向外部观测系统发送原文。

### 6.2.1 Worker 启动、确定性 fixture 与 E2E 驱动

Playwright 验收必须能同时启动 API、前端和 Worker；如果 Worker 不在进程图里，主流程只会停在 queued/running，不能构成验收。

1. **E2E 启动约束**
   - `frontend/playwright.config.ts` 的 `webServer` 必须显式包含 Worker 启动方式，或在同一测试前置脚本中以独立进程启动 Worker。
   - Worker 使用与 API 相同的 `DATABASE_URL`、`ACTOR_ID`、`DEPLOYMENT_MODE`、`API_BIND_SCOPE` 和 `INTERNAL_API_BASE_URL`，并在测试结束时可靠退出。
   - 如果真实模型 provider 未配置，E2E 默认使用确定性 Fake provider；若配置了真实 provider，则只能在独立评测套件中启用，主验收仍以稳定输出和状态断言为准。
2. **确定性推进**
   - E2E 不能依赖“等一会儿就好”的人工观察；必须使用明确的运行轮询、SSE 事件等待和最终状态断言，超时后报告当前 phase、run_id、pending_decision 和最后一个事件序号。
   - 每个阶段至少断言一次持久化结果：候选计划、accepted plan 指针、场景映射、staged ChapterRevision、accepted ChapterRevision 和 Story Bible 候选/正式条目。
3. **恢复与重放**
   - E2E 必须包含至少一个 Worker 重启或进程重建步骤，验证 `chapter_plan.accepted`、`chapter_revision.accepted` 和运行事件可重放。
   - SSE 重放和重复决策必须验证幂等：相同命令返回同一结果，不得创建新候选或新场景。

### 6.3 兼容策略

- POST /api/chapters/{chapter_id}/plan 只作为迁移期兼容接口：新 UI、服务端主流程和新测试不得调用它。待章节规划运行入口、章节工作流读取视图和完整 Playwright 主流程验收通过后，删除该 POST 路由、前端 createChapterPlan 调用及其初始化测试；删除后不提供同语义的第二个初始化接口。
- GET /api/chapters/{chapter_id}/plan 只读 accepted plan，不属于计划生成；前端迁移到 GET /api/chapters/{chapter_id}/workflow 后，可以删除该 GET 路由及其客户端读取函数，或保留为明确标注的只读兼容别名。
- 现有场景级接口和独立场景操作保持兼容。
- 新增的章节工作流读取视图通过已有领域服务组合，不复制一套版本或状态存储。
- 旧数据没有章节意图时显示“需要补充章节意图”，不得用默认字符串静默补全。

### 6.4 兼容接口删除清单与验证

删除必须在完整章节主流程验收之后执行，不能先删接口再让前端失去可用入口。删除清单如下：

1. 后端删除 `post_chapter_plan`、`_INIT_PLAN_REASON` 及只服务于该初始化命令的辅助分支；确认 `POST /api/chapters/{chapter_id}/plan` 不再出现在路由和 OpenAPI 中。
2. 前端删除 `createChapterPlan()`、生成初始化计划的按钮处理逻辑及只服务于该调用的类型/适配函数；章节工作区只能通过章节规划运行和 `ChapterWorkflowRead` 工作。
3. 删除或改写 `backend/tests/api/test_chapter_plan_init.py` 及相关初始化 fixture；保留的数据迁移测试只能验证旧数据可被 workflow 读取，不能继续验证初始化命令。
4. 清理生产代码、前端和测试中的 `plan_init`、`createChapterPlan`、`post_chapter_plan` 引用；设计文档中的迁移说明可以保留，但必须标注为历史约束。
5. `GET /api/chapters/{chapter_id}/plan` 在 workflow 读取完全接管前可保留为只读兼容别名；接管后若删除，必须同时删除客户端读取函数并验证无调用方。

删除完成的最小验证命令：

~~~powershell
rg -n "post_chapter_plan|createChapterPlan|plan_init" backend/app backend/tests frontend/src frontend/tests
rg -n "method:\s*[\"']POST[\"'].*chapters/.*/plan|/api/chapters/.*/plan" backend/app frontend/src
Push-Location backend
.venv\Scripts\python.exe -m pytest
Pop-Location
Push-Location frontend
npm run typecheck
npx playwright test tests/chapter-workflow.spec.ts
Pop-Location
~~~

前两条搜索在删除完成后不得出现生产代码调用；若保留历史文档说明，搜索范围应排除 `docs/`，并单独检查 OpenAPI 路由清单。

## 7. 前端设计

### 7.1 页面信息架构

章节工作台保留三栏布局，但主对象从“选中场景”提升为“当前章节”。右侧是上下文栏，不与中间工作区争夺主操作；底部是当前阶段的决策栏，不重复渲染另一套运行状态机：

~~~text
桌面端：
左侧：作品、卷、章节导航
中间：章节工作区
      - 阶段进度条
      - 章节计划/正文/章节审校三个视图（按当前阶段启用）
      - 当前场景内容与检查结果
右侧上下文栏（分组折叠，默认展开当前阶段相关内容）：
      - 章节契约
      - 当前场景简报
      - 阻塞与影响范围
      - Story Bible / Canon
      - 版本历史
底部决策栏：当前阶段主动作、反馈输入、取消/详情等次级动作
底部运行时间线：默认折叠；展开后查看可读事件和原始 payload
~~~

章节页面默认展示章节正文和场景顺序。进入某个场景只是定位到章节队列中的一个执行单元，不改变主流程上下文，也不改变章节工作流阶段。

响应式约束：宽屏使用三栏；中等宽度将右侧上下文栏变为抽屉或分段标签；移动端将资源导航和上下文栏变为可关闭抽屉，中间工作区保持单列，决策栏固定在底部。所有布局必须保证标题、状态、按钮和反馈输入不互相遮挡。面向作者只显示自然语言和结构化标签，不直接显示 JSON；原始 JSON 仅放在事件详情或开发调试入口。

### 7.2 建议组件边界

- ChapterWorkspace：章节工作流容器，读取并刷新 ChapterWorkflowRead，集中维护后端命令和 SSE 刷新；子组件不得自行拼接“最新记录”推断阶段。
- ResourceTree：作品/卷/章节/场景导航，维护选中资源、展开状态和资源/版本删除入口。
- ChapterIntentForm：以自然语言意图为主，结构化字段作为可选补充；只禁止提交空意图，不要求作者一次性填满目标、状态或风格字段。
- PlanDiscussionPanel：展示 AI 澄清问题、契约建议和作者回答；逐项显示“AI 建议/作者已确认/尚未确定”，作者确认前不能进入 accepted plan。
- WorkflowPhaseBar：展示当前阶段、已完成阶段、阻塞阶段和下一步主动作；阶段名称由服务端 phase 映射为中文，不由前端自行计算。
- ChapterPlanReview：展示契约、场景顺序、必须/禁止事项，提供计划反馈/接受/取消。
- SceneQueue：展示场景顺序、状态、版本、阻塞原因和当前执行项；只负责定位和展示，不能手动插入、跳过或重排章节主队列。
- SceneReviewPanel：展示正文、审校问题、候选事实和场景级决策。
- ChapterReviewPanel：展示章节聚合版本、章节级问题、影响闭包和章节决策。
- ChapterContextRail：承载章节契约、场景简报、阻塞影响、Story Bible/Canon 和版本历史；各区块按优先级折叠，不能同时展开成第二个工作区。
- ChapterRevisionHistory：展示章节计划/场景/章节版本、场景来源映射、比较和回滚；删除作为二级危险操作，不能覆盖接受版本或被当前血缘引用的版本。
- StoryBiblePanel：保留现有 Canon 能力，但显示来源版本和作用域。
- DecisionBar：只渲染当前 phase 允许的主动作和次级动作，负责反馈提交、接受、取消和恢复。
- RunPanel：从“原始事件日志”提升为阶段进度和可读事件，同时保留事件详情折叠查看；不重复渲染 DecisionBar 的决策按钮。

page.tsx 不再承载全部章节工作流状态；资源树、章节工作区、场景编辑、上下文栏和运行面板分别维护清晰边界。现有文件如果暂时不拆分，至少先提取上述视图模型、命令适配函数和事件适配函数，避免继续增加条件分支。

### 7.3 交互规则

- 所有阶段状态、主动作、阻塞原因和版本指针均来自 `ChapterWorkflowRead`；前端只保存选中场景、展开区块、输入草稿等展示状态，不自行推断 accepted、stale 或 out_of_sync。
- 每个阶段只显示一个主动作；反馈、取消、查看详情、比较和删除都是次级动作。主动作根据当前 phase 和 pending decision 动态变化，不能同时出现两个同等级的提交按钮。

| 阶段 | 主动作 | 前端命令或数据来源 |
| --- | --- | --- |
| `intent_required` | 启动章节规划 | `POST /api/chapters/{chapter_id}/runs`，提交 `new_chapter`、`decision_target=plan` 和非空意图 |
| `planning` | 查看规划进度 | 读取 `active_run` 和事件时间线，不重复启动同一运行 |
| `plan_feedback` | 由 `pending_decision.kind` 决定回答 Planner 或接受候选计划 | `answer_planner` 使用计划决策的 `feedback.kind=answer|proposal_review|replan`；`accept_plan` 提交 `plan_revision_id` 和期望版本 |
| `scene_generation` | 查看当前场景 | Worker 自动按 accepted plan 推进；前端不得手动创建、插入、跳过或重排主队列场景 |
| `scene_feedback` | 由 `pending_decision.kind` 决定回答场景问题或接受当前场景结果 | `answer_scene` 恢复当前场景运行；`accept_scene` 使用决策目标 `scene`；反馈只作用于当前场景，不能改写章节计划 |
| `chapter_review` | 接受章节版本 | 决策目标为 `chapter`，提交服务端产生的 `chapter_revision_id` |
| `canon_feedback` | 提交 Canon 决策 | 使用 Canon 专用命令，显示候选作用域和来源 `ChapterRevision` |

- 章节计划页面默认先显示自然语言输入和 AI 讨论区；结构化字段按“已确认 / AI 建议 / 尚未确定”分组，不用空表单制造阻塞。
- AI 提问必须说明缺少的信息会影响什么；AI 建议必须逐项显示来源和确认状态，允许作者采纳、修改或拒绝，不能用一次“生成”动作静默填充所有字段。
- `needs_clarification` 时主动作是回答问题并恢复 Planner；`ready` 时主动作才是审阅并接受候选计划。未确认建议不能进入 accepted plan。
- 计划和场景的澄清都必须在 `pending_decision` 中返回问题关联、运行 ID 和期望运行版本；前端不能只根据 `phase` 猜测当前是回答问题还是接受结果。
- 计划未接受时，场景队列只能展示候选 `SceneBrief[]`，不能启动正文生成；计划接受后，场景状态和顺序由服务端/Worker 推进。
- 存在 stale 或 out_of_sync 时，章节接受按钮禁用，并显示具体场景、来源版本、冲突原因和可执行的恢复动作。
- 场景级 Canon 必须显示“场景作用域”；章节级 Canon 必须显示来源 `ChapterRevision`。AI 生成内容统一标记为“AI 候选/建议”，作者确认后才显示为正式内容。
- Agent 内部节点名称默认转为中文阶段名称，原始节点、事件 payload 和运行 ID 放在可展开详情区；默认视图不显示原始 JSON。
- 失败、澄清、暂停和冲突必须显示原因、受影响对象和下一步操作，支持重试、恢复、反馈、取消或返回工作区，不能只显示错误码。
- 资源树和版本历史继续支持右键菜单，同时提供选中后的 `Delete`/`Backspace` 键和可见的更多操作入口；菜单支持 `Escape` 关闭并恢复焦点。删除必须二次确认，并由服务端返回可删除性；接受版本、被当前血缘引用的版本或不可删除资源显示禁用原因。
- 版本历史显示版本类型、状态、来源映射、创建时间和影响范围；比较、回滚和删除都不是章节主流程的主动作，回滚必须选择明确目标版本并重新读取权威状态。
- 所有主动作、反馈输入、错误提示和运行状态都必须可键盘操作；状态不能只依赖颜色，实时事件和阻塞消息使用可读文本及 `aria-live` 更新。

## 8. 分阶段交付

阶段交付按“产出、退出标准、验证证据”判断，不按新增文件数量或按钮数量判断。当前仓库已有部分底座能力：`/api/chapters/{chapter_id}/runs`、`DecisionRequest`、`ChapterPlanRead`、章节 Canon 独立入口和若干 Agent/版本测试都已存在；但阶段 0 到阶段 4 仍没有形成作者可执行的连续主流程，尤其缺少章节工作流读取视图、Planner 讨论链和 accepted plan 之后的自动场景队列。

### 8.0 依赖与实施顺序

二阶实现必须按以下顺序推进，后一个阶段不得用页面占位或兼容接口绕过前一个阶段：

| 顺序 | 阶段 | 依赖 | 进入条件 | 退出条件 |
| --- | --- | --- | --- | --- |
| 1 | 阶段 0：流程契约和测试夹具 | 无 | 已确认主流程、状态、命令和目标契约 | schema、状态转移、错误码和 fixture 可独立测试 |
| 2 | 阶段 1：章节意图与计划审阅 | 阶段 0 | 目标输入/输出契约已冻结 | 自然语言意图可规划、可讨论、可反馈、可接受，且未确认建议被阻断 |
| 3 | 阶段 2：场景队列与场景审阅 | 阶段 1 的 accepted plan | 计划接受和场景物化事务可恢复 | 场景按 SceneBrief 顺序自动生成，作者可逐场反馈/接受 |
| 4 | 阶段 3：章节聚合、审校与接受 | 阶段 2 的有效 accepted scene revisions | 场景队列、版本映射和影响闭包可读取 | staged ChapterRevision 可审校、反馈、接受、回滚 |
| 5 | 阶段 4：Canon 闭环与完整旅程 | 阶段 3 的 accepted ChapterRevision | 章节接受事件和来源版本稳定 | Canon 逐条决策、Story Bible 写入和完整 Playwright 旅程通过 |

每个阶段都必须同时落地设计契约、后端能力、前端操作和自动化验证；设计文档更新、单独接口可调用或局部页面可显示，都不能单独作为阶段完成证据。每阶段退出时必须保留上一阶段回归结果，出现回归则阶段不得进入下一阶段。

### 阶段 0：流程契约和最小闭环 fixture（当前：未完成）

产出：ChapterWorkflowRead 服务端 schema、阶段枚举、计划/章节决策请求契约、规划血缘消息/问题/建议记录、候选计划持久化字段、`chapter_plan_scene_links` 场景映射、完整流程状态转移表，以及同时包含 API/Worker/Fake provider 的 Playwright fixture。

实施顺序必须固定为：先写状态转移、候选持久化、消息 ID 和幂等边界的失败测试；再实现最小事务和 workflow 查询；最后接入 Worker 与 E2E fixture。阶段 0 不接完整前端工作台，只提供一个能输入意图、读取候选、接受计划并启动一个确定性场景的测试入口。

验收：

1. `new_chapter` 运行保存非空自然语言意图，Worker 能把 Planner 输出写成 `pending ChapterPlanRevision`，并在 workflow 中返回 `message_id`、问题/建议、候选 revision 和 `pending_decision`。
2. 计划反馈能创建同一章节/规划血缘的 Planner 子运行；重复反馈命令返回相同 child run，旧父运行不能覆盖新候选。
3. 计划接受在同一事务中更新 accepted pointer、写入固定场景映射并发布可重放 `chapter_plan.accepted`；旧 pointer、悬空映射和重复命令均有明确结果。
4. Worker 重启后能从 outbox/accepted pointer 恢复第一个场景运行；Playwright 不依赖外部模型，能断言数据库写入、运行事件和最终阶段。
5. 阶段 0 的新增测试通过，且现有 pytest、mypy、前端类型检查不回归；Ruff 的既有基线错误必须在进入阶段 1 前单独清零。

### 阶段 1：章节意图与计划审阅（当前：`/runs` 和章节 Agent 基础存在，主流程未完成）

产出：自然语言章节意图输入、可选结构化字段、真实 new_chapter 规划入口、Planner 输入契约、Agent 字段投影/适配、`CHAPTER_PLAN_SYSTEM_PROMPT`、`_build_prompt()`、`PlannerDiscussionHook`、AI 澄清/建议讨论区、计划正文/有序场景清单展示、计划反馈/澄清/接受/取消；其他 Agent 保持既有职责和调用协议；不以手动新建场景作为章节主流程入口。

验收：只输入自然语言意图即可启动规划；缺少影响生成的关键约束时进入 pending_clarification；作者回答能在下一轮 Planner 输入中被读取；`PlannerDiscussionHook` 能阻断未确认建议和未解决关键假设进入 accepted plan；字段投影和字段泄漏测试通过；提示词/schema/Hook 行为测试通过；计划反馈产生带来源和父级的候选 revision；计划接受后才允许按 `chapter_plan_scene_links.sort_order` 运行场景队列；主流程不要求作者手动创建或插入场景；旧初始化接口不再被新 UI 调用。

### 阶段 2：场景队列与场景审阅（当前：已有场景级能力，章节队列未完成）

产出：按 accepted plan 自动推进场景队列、阶段进度条、场景状态、当前场景正文、检查问题、反馈和接受。

验收：场景严格按计划顺序推进；场景反馈只影响当前场景和计算出的下游闭包；取消不会删除已接受版本；运行断线后可以按 SSE sequence 恢复。

### 阶段 3：章节聚合、审校与接受（当前：已有领域/Agent 基础，工作台闭环未完成）

产出：章节聚合触发、章节审校结果、影响闭包、stale 阻断、章节版本历史、章节接受和回滚。

验收：所有场景具备有效 accepted revision 后才生成 staged chapter revision；存在 stale 时不能接受；章节接受后状态为 in_sync 并发布可重放 outbox 事件。

### 阶段 4：Canon 闭环与完整旅程（当前：已有 Canon API/候选决策测试，端到端旅程未完成）

产出：章节接受后自动进入章节 Canon 运行、候选逐条决策、Story Bible 更新、完整章节工作流 Playwright 测试。

验收：章节 Canon 只能使用 accepted chapter revision；场景级决策不能改全局 Canon；完整测试从填写章节意图开始，最终验证 Story Bible 正式条目和审计事件。

## 9. 验收标准

验收必须区分“已有底层能力回归”和“二阶主流程新增能力”。已有 `pytest`、`ruff`、`mypy`、`test_chapter_plan_init.py`、`test_chapter_graph.py`、`test_real_chapter_worker_chain.py` 和 `editor.spec.ts` 通过只能作为回归或兼容证据，不能替代章节工作台的 API 验收和 Playwright 主流程验收；兼容初始化接口通过也不能替代真实章节规划运行验收。

### 9.1 主流程验收

在干净 fixture 中完成以下操作且不离开章节工作区：

主流程验收中的主动作必须由 Playwright 通过章节工作区 UI 触发；下列 API 仅用于说明对应命令、构造测试前置或读取持久化断言，不能用脚本直接调用 API 代替 UI 旅程。

“干净 fixture”必须同时提供 API、前端、Worker 和确定性 Fake provider；每次状态等待都使用运行快照或 SSE sequence，不能依赖固定 sleep。测试失败时必须输出当前 phase、run_id、pending_decision、最后事件序号和数据库中未完成的 outbox。

1. 只输入一段非空的自然语言章节意图，结构化字段可先留空或只填写少量补充信息。
2. 调用 `POST /api/chapters/{chapter_id}/runs` 创建 `run_scope=chapter`、`request_type=new_chapter`、`decision_target=plan` 的规划运行；Worker 执行后，在 `GET /api/chapters/{chapter_id}/workflow` 中看到阶段、讨论记录、服务端生成的消息 ID 和待决问题。
3. 通过 `POST /api/runs/{run_id}/decisions` 提交 `target=plan` 的反馈，确认作者回答、建议采纳或修改会被持久化到同一规划血缘。
4. 对候选计划提交接受决策，带上服务端生成的 `plan_revision_id` 和版本基线，确认 accepted plan 被冻结、`chapter_plan_scene_links` 映射固定，`chapter_plan.accepted` 事件可重放且重复消费不创建新场景。
5. 系统根据 accepted plan 的有序 `SceneBrief[]` 自动创建场景映射并推进场景队列，主流程不要求作者手动创建或插入场景。
6. 在第一个场景运行处于 `running` 或 `waiting_feedback` 时重启 Worker 或重建 Worker 进程，重放 `chapter_plan.accepted`/运行事件，确认队列从 accepted pointer 和最后事件序号恢复到正确的当前场景；随后对一个场景提交反馈或接受，确认只影响当前场景及其下游闭包。
7. 所有场景接受后调用 `POST /api/chapters/{chapter_id}/runs` 创建 `request_type=review`、`decision_target=chapter` 的章节审校运行，确认 staged `ChapterRevision`、影响闭包和章节版本历史可读取。
8. 通过 `POST /api/runs/{run_id}/decisions` 提交章节级接受，确认 `ChapterWorkflowRead` 的阶段、`accepted_chapter_revision_id` 和回滚指针保持一致。
9. 章节接受事件可靠发布并被幂等消费后，调用 `POST /api/chapters/{chapter_id}/canon-runs` 创建章节 Canon 运行，通过 `POST /api/runs/{run_id}/canon-decisions` 逐条确认一个事实、拒绝一个事件、暂缓一个剧情线。
10. 验证确认项进入 Story Bible，拒绝/暂缓项只保留候选状态，不进入正式 Canon。

### 9.2 阻断验收

- 计划未接受时不能创建正常场景队列运行。
- 未纳入 accepted plan 的手动场景不能加入章节主队列；主流程不得通过手动创建、插入或跳过场景绕过计划顺序。
- 章节没有自然语言意图时不能启动规划；有自然语言意图但缺少关键约束时，Planner 必须进入澄清或提出显式建议，不能静默补全。
- `needs_clarification` 只要求返回可回答的问题，不强制返回完整 `SceneBrief[]`；只有 `ready` 才要求完整、唯一且有序的场景清单。
- 计划反馈必须保存完整讨论记录，并在同一章节/计划血缘下创建或恢复 Planner 子运行；不得只保存反馈哈希，也不得拼接无关运行的上下文。
- 计划接受必须提交服务端产生的 `plan_revision_id`，并校验 `expected_run_version`、计划版本基线、CAS 和 fencing；旧版本或重复命令不得覆盖新结果。
- AI 提出的关键建议在作者确认前不能写入 accepted plan、正文基线、正式 Canon 或章节版本。
- 场景入口/出口不兼容时必须显示 stale 或阻塞原因。
- 章节存在未处理 stale 或 handoff 冲突时不能接受。
- 章节级 Canon 没有 accepted chapter revision 时不能创建；场景级 Canon 的确认不能修改全局 Story Bible。
- 只有 Canon `confirm` 能写入正式 Story Bible，`reject` 和 `defer` 只更新候选状态。
- 新 UI 不得调用 `POST /api/chapters/{chapter_id}/plan`；该接口在迁移期间只允许作为初始化/测试兼容路径，主流程验收不能使用它。
- 非 Canon 运行取消、失败或 superseded 时，未决计划/正文候选不得写入正式版本；Canon 运行取消时未决候选保留 `pending`，且已接受版本不能丢失。

### 9.3 回归验收

- 现有 `backend/tests/api/test_chapter_plan_init.py`、`backend/tests/agents/test_chapter_graph.py` 和 `backend/tests/runtime/test_real_chapter_worker_chain.py` 只能证明兼容初始化或旧章节图底座仍可运行，不能计入主流程通过证据；`frontend/tests/editor.spec.ts` 中“displays the generated chapter plan”也仍验证旧初始化按钮，迁移完成后应删除或改写这些旧测试。
- 后端全量 `pytest`、`ruff`、`mypy` 通过；进入阶段 1 前必须先清零已知 Ruff 基线错误，不能把基线失败归因于新功能。
- 现有编辑器、运行状态、Story Bible、删除和版本历史 Playwright 测试通过。
- 新增 `frontend/tests/chapter-workflow.spec.ts` 覆盖完整主流程、计划反馈、场景反馈、stale 阻断和章节 Canon。
- 新增 API 测试覆盖章节工作流读取视图、计划决策、Planner 子运行 lineage、候选计划持久化、章节聚合资格、章节接受和 Canon 来源绑定。
- 服务重启后，运行、事件、候选计划、场景映射、版本、Canon 和章节工作流读取视图保持一致；Playwright 配置必须包含 Worker 启动或等价的 Worker pump，不得只启动 API/前端。

## 10. 风险与取舍

| 风险 | 当前代码证据与影响 | 处理方式与退出条件 |
| --- | --- | --- |
| 旧初始化接口掩盖真实规划 | 当前前端仍调用 `createChapterPlan()`，后端 `POST /api/chapters/{chapter_id}/plan` 会把已有场景映射后直接接受，不调用 Planner | 新 UI 只调用 `new_chapter` 规划运行；完整主流程、API 和 Playwright 验收通过后，按 6.4 删除 POST、客户端调用和初始化测试 |
| Planner 输入字段没有真正进入模型 | `/runs` 已保存 `chapter_intent`，但 Worker 重建信封和 `_build_prompt()` 仍主要使用 `chapter_contract`，没有独立讨论恢复 | 只扩展 Planner 的输入投影、Prompt 和 `PlannerDiscussionHook`；用字段投影、反馈注入和跨 Agent 泄漏测试证明边界 |
| Planner 输出契约仍是旧结构 | 当前 `ChapterPlanOutput` 仍使用 `scene_contracts`，没有建议来源、字段确认状态和未解决假设 | 增加规范化 `SceneBrief[]`、`PlanFieldProvenance` 和 `PlanDecisionRequest`；`ready`、`needs_clarification` 分别按不同完整性规则校验 |
| 旧 `ChapterGraph` 绕过计划接受和场景队列 | 当前章节图仍是 Planner → ChapterReview → Aggregator，Worker 默认仍构建该图；会导致未接受计划直接进入下游 | 改为章节工作流控制器/队列，或重构图边使其明确等待 accepted plan、场景队列和逐场决策；旧直连不能作为主流程 |
| accepted plan 与首个场景运行跨事务 | 计划接受、场景物化和 Worker 领取不是一个进程内动作，重启或事件投递失败可能留下“计划已接受但没有场景运行” | 事务写入 `chapter_plan.accepted` outbox，消费者幂等，协调器可按 accepted 指针重建队列，并测试重放、重启和 fencing |
| 前端拼接多个最新记录 | 当前页面仍以资源树、场景和运行状态为主，`ChapterWorkflowRead` 尚未实现；按最新行推断会产生状态漂移 | workflow 读取视图成为章节工作区唯一状态源，前端只保存展示草稿和选中项 |
| Planner 候选输出没有持久化落点 | `AgentCallable` 有结构化 output，但章节节点和 Worker 当前只保存运行状态；运行暂停后无法从服务端得到可接受的 `plan_revision_id` | 阶段 0 先把候选、讨论、checkpoint 和幂等键写入持久化记录；重复节点执行返回同一候选，并用 API/Worker 回归测试证明 |
| E2E 没有 Worker 驱动 | 当前 Playwright webServer 只启动 API/前端，章节运行会停在 queued/running，无法证明意图到 Story Bible | fixture 同时启动 Worker 和 Fake provider，使用运行快照/SSE 等待，加入 Worker 重启、outbox 重放和最终数据库断言 |
| accepted 指针、旧计划 CAS 和章节场景映射不一致 | 读取 helper 按最新 accepted 行推断，旧 accepted plan 可绕过 pointer 校验，章节映射写入空 `scene_id` | 阶段 0 先修复三个基础边界并增加回归测试；任何 pointer 悬空、映射缺失或旧版本写入都返回 blocked/conflict |
| 手动场景操作绕过计划顺序 | 现有 `POST /api/chapters/{chapter_id}/scenes` 可直接创建场景，独立场景运行也已存在 | 保留独立编辑能力，但未纳入 accepted plan 的场景不得进入章节主队列；主流程只能由 accepted `SceneBrief[]` 物化和排序 |
| stale、handoff、CAS 和 fencing 被局部实现绕过 | 版本、入口和运行租约已有底座，但新计划/队列/章节决策尚未全部绑定这些基线 | 所有计划、场景、章节和 Canon 命令携带来源版本、幂等键、CAS 和 fencing；冲突必须阻断写入而不是覆盖 |
| 章节接受与 Canon 自动衔接缺失 | 当前章节 Canon 有独立 API 和测试，但没有“章节接受事件 → Canon 运行”的完整主链 | 章节接受只发布 `chapter_revision.accepted` outbox，Canon 幂等消费；只允许使用 accepted chapter revision |
| 真实模型导致验收不稳定或泄漏原文 | Provider 输出非确定，LangSmith 也可能成为外部依赖或泄漏路径 | E2E 断言状态、来源、事件和写入边界，不断言固定文本；LangSmith fail-open、脱敏失败则禁止外发 |
| 迁移删除过早或残留旧调用 | 删除旧 POST 前若仍有前端、测试、OpenAPI 或脚本引用，会造成回归或第二套入口 | 依次完成新入口、workflow、主流程和全量回归，再执行 6.4 的搜索、路由和测试验证；未通过则保留兼容入口 |

## 11. 完成定义

当前代码只具备局部底座，尚未满足以下完成定义。二阶建设必须全部满足；任一条缺失，都只能称为局部实现或迁移中：

1. **主流程闭环**：作者从章节工作区输入非空自然语言意图，经同一个 `ChapterPlannerAgent` 多轮讨论、计划审阅和接受后，系统自动物化并按序生成场景；作者完成逐场决策、章节聚合/审校/接受，最后进入章节 Canon。
2. **Planner 契约落地**：意图、讨论消息、待回答问题、待确认建议、字段来源和作者决策可持久化、可恢复；候选计划与来源运行、checkpoint 和幂等键可追溯；未确认建议和关键未解决假设不能进入 accepted plan；其他 Agent 不获得 Planner 专属讨论上下文。
3. **计划到队列的可靠衔接**：接受指定 `plan_revision_id` 后，服务端在版本 CAS、幂等和 fencing 约束下冻结 accepted plan，写入固定 `chapter_plan_scene_links`，可靠发布 `chapter_plan.accepted`，并能在重启/重复投递后恢复有序场景队列。
4. **场景和章节版本治理**：场景只能绑定 accepted `SceneBrief`、计划版本和场景顺序；所有场景有有效 accepted revision 后才能生成 staged `ChapterRevision`；stale、out_of_sync 或 handoff 冲突会阻断章节接受。
5. **Canon 来源和决策正确**：章节 Canon 只能从 accepted chapter revision 创建；`confirm` 才能写入正式 Story Bible，`reject`/`defer` 只更新候选；场景级 Canon 不得修改全局 Story Bible。
6. **权威读取和恢复**：`ChapterWorkflowRead` 一次提供阶段、pending decision、讨论、计划、场景队列、运行、阻塞、版本和 Canon 来源；刷新、SSE 重放、Worker 重启和暂停恢复后，前端展示与服务端 accepted 指针一致。
7. **前后端真实打通**：章节工作台实际调用目标 API 和决策命令，页面不再依赖 `createChapterPlan()` 或旧初始化 POST；计划、场景、章节审校、版本历史、Canon 和阻塞恢复均有明确 UI 动作。
8. **迁移完成**：新 UI、生产代码、新测试和 OpenAPI 不再引用 `POST /api/chapters/{chapter_id}/plan`；按 6.4 删除后端路由、前端适配和初始化测试，不提供同语义的第二个入口。
9. **验证证据完整**：新增 Planner 讨论、字段投影、Hook 路由前校验、候选计划持久化、子运行 lineage、计划决策、workflow read、场景队列、聚合资格、章节接受、Canon 来源和包含 Worker/Fake provider/重启重放的完整 Playwright 测试通过；后端 `pytest`/`ruff`/`mypy`、前端类型检查和现有编辑器/版本/删除/Story Bible 回归测试通过。
10. **非完成条件明确**：只修改计划书、只修改 Prompt/Hook、只新增页面字段、只让旧初始化接口返回计划、只通过 Agent 单测或局部接口测试，都不计入二阶完成。
