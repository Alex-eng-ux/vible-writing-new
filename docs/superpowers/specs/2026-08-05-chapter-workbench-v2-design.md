# 连续小说创作工作室二阶设计计划书

> 本文是基于《连续小说创作工作室 V1 工程交付计划》与当前成品现状形成的产品与工程设计。目标不是重新建设底层 Agent，而是把已有能力收敛为作者可以从章节意图走到 Story Bible 的完整前后端工作流。

## 1. 设计结论

当前系统已经具备资源树、场景编辑、场景级运行、版本治理、Canon 数据模型、Worker、章节 Agent 和大量后端测试。当前缺口集中在“作者可见的章节主流程”：章节意图没有被完整采集，计划生成被初始化接口替代，章节级运行、聚合、审校和接受没有形成连续入口。

二阶建设采用“流程契约优先”路线：先定义章节工作流的状态、命令和读取视图，再补齐后端编排接线，最后完成前端章节工作台，并用一条真实 Playwright 流程验收。最终交付必须是可用的前后端功能，不是只完成设计文档、局部接口或局部页面。原有场景编辑器、版本治理、幂等、租约、事件和 Canon 领域约束继续复用，不做无关重写。

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
- “计划生成”的定义与接口约束：计划生成是一次真实的章节规划运行，不是把已有场景重新包装成计划。新 UI 必须调用 POST /api/chapters/{chapter_id}/runs，提交 run_scope=chapter、request_type=new_chapter、decision_target=plan 和非空 chapter_intent.text；ChapterPlannerAgent 根据意图生成 ChapterContract 与有序 SceneBrief，允许澄清和计划反馈，作者接受后才形成 accepted plan。现有 POST /api/chapters/{chapter_id}/plan 只在迁移期间作为兼容/测试接口，用于初始化已有场景并直接接受；它不能作为新 UI 的主入口，不能被称为 AI 章节规划，完成迁移后必须删除。
- 场景自动生成约束：章节主流程不以作者手动创建场景为前置。ChapterPlannerAgent 必须从章节意图生成有序 SceneBrief；作者接受计划后，领域服务才物化场景映射，Worker 再按 accepted plan 的顺序自动生成场景正文。
- 每个场景的正文生成、确定性检查、连续性检查和质量审校必须绑定对应的 SceneBrief、accepted plan 版本和场景顺序；当前场景未完成作者决策时，Worker 不得跳过、插入或直接推进后续主流程场景。
- 章节聚合只能在计划清单中的所有场景具备有效 accepted revision 后执行；章节反馈或重新规划必须产生新版本并计算受影响场景，不能覆盖旧计划或旧章节版本。
- 章节和场景的 accepted、staged、stale、out_of_sync 等状态必须来自服务端权威视图，前端不得根据“最新行”自行推断。
- 正文、计划、章节版本、Canon 和审计记录继续保持不可变血缘、事务边界、幂等和 fencing 约束。
- ChapterWorkflowRead 必须一次性提供当前阶段、Planner 讨论、待决问题/建议、当前计划、场景队列、运行事件和阻塞原因，使前端能按同一状态恢复工作区，而不是拼接多个“最新记录”接口。
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
- “生成章节计划”调用初始化路径；该路径映射已有场景并直接接受，不经过规划运行和作者计划审阅。
- 前端只调用场景运行入口，没有把章节入口接到主界面。
- 前端没有章节计划反馈/接受、章节队列、章节聚合、章节审校、章节版本接受和章节版本历史视图。
- 前端没有展示 affected_scene_ids、stale_scene_ids、chapter_sync_status、entry_handoff_status 等阻塞信息。
- 场景当前被当作作者必须手动创建和管理的主要层级，和“章节为作者工作单位、场景为 Agent 内部执行单位”的产品规则不一致。
- 当前 Playwright 测试覆盖资源、编辑器、场景运行和 Story Bible，缺少从章节意图到 Story Bible 的完整旅程。

## 4. 目标用户流程

~~~mermaid
flowchart TD
    A[作者打开章节工作区] --> B[输入自然语言意图<br/>结构化字段可选]
    B --> C[启动章节规划运行]
    C --> D[ChapterPlannerAgent 生成并展示 ChapterContract 和有序 SceneBrief]
    D --> E{作者审阅章节计划}
    E -->|反馈| C
    E -->|需要澄清| E1[AI 提出问题或契约建议]
    E1 --> E2[作者回答、采纳或修改]
    E2 --> E3[resume_pending_node]
    E3 --> D
    E -->|取消| X[运行取消并丢弃未决候选]
    E -->|接受| F[作者确认，冻结 accepted plan 并建立场景映射]

    F --> G{还有未完成场景?}
    G -->|有| H[系统按计划顺序自动生成当前场景正文]
    H --> I[WritingAgent 与确定性检查、连续性检查、质量审校]
    I --> J{作者审阅场景结果}
    J -->|反馈| K[RevisionAgent 生成补丁]
    K --> I
    J -->|澄清| H
    J -->|取消| X
    J -->|接受| G

    G -->|无| L[ChapterAggregator 生成 staged ChapterRevision]
    L --> M[ChapterReviewAgent 章节审校]
    M --> N{作者审阅章节结果}
    N -->|修改| O[计算影响闭包并重跑场景]
    O --> H
    N -->|重新规划| C
    N -->|取消| X
    N -->|接受| P[提交 accepted ChapterRevision]

    P --> Q[幂等创建章节 Canon 运行]
    Q --> R[CanonAgent 生成候选]
    R --> S{作者逐条决策}
    S -->|反馈| R
    S -->|确认/拒绝/暂缓| T[更新候选与正式 Story Bible]
    S -->|取消| U[保留未决候选]
    T --> V[完成章节任务]
    U --> V
~~~

### 4.1 主流程与独立操作的边界

主流程以章节为入口，场景由计划和 Worker 队列推进。作者可以在章节工作区内定位某个场景，但不需要通过“新建场景”手动拼接章节主结构。

在主流程中，SceneBrief[] 是场景清单的唯一来源：规划阶段只展示候选场景计划，计划接受后才创建/映射主流程场景，Worker 按清单顺序推进正文生成。作者可以通过反馈修改计划或审阅单个场景，但不能通过手动新建、插入或跳过场景来绕过 accepted plan。现有手动场景接口仅作为兼容和独立场景操作保留，不得把未纳入 accepted plan 的场景加入章节主队列。

场景续写、改写、审校和场景级 Canon 仍保留为独立操作。独立操作必须明确显示其不属于当前章节主流程，并要求携带正确的计划版本和场景基线。

### 4.2 反馈语义

- 计划反馈：重新调用 ChapterPlannerAgent，生成新的不可变计划版本。
- 场景反馈：调用 RevisionAgent，只生成当前场景补丁并重新检查。
- 章节反馈：先计算入口/出口状态影响闭包，再决定场景重跑或 stale 阻断。
- Canon 反馈：只写入 Canon 运行反馈，不修改正文和章节版本。
- feedback 永远不是运行终态；只有作者接受、取消或系统失败才结束当前决策阶段。

## 5. 工作流状态与读取模型

### 5.1 章节工作流状态

章节工作流视图使用服务端计算结果，不新增与领域状态冲突的前端状态。对作者展示以下阶段：

| 阶段 | 含义 | 主动作 |
| --- | --- | --- |
| intent_required | 没有可供规划的自然语言意图 | 输入一句想法并启动规划 |
| planning | 规划运行中 | 查看进度 |
| plan_feedback | 计划等待作者决策 | 接受、反馈、取消 |
| scene_generation | 按计划处理场景 | 查看当前场景 |
| scene_feedback | 场景等待作者决策 | 接受、反馈、取消 |
| chapter_review | 章节聚合和审校完成 | 查看问题、反馈、接受 |
| chapter_feedback | 章节存在影响闭包或重跑任务 | 处理受影响场景 |
| canon_feedback | Canon 候选等待决策 | 逐条确认、拒绝、暂缓 |
| completed | 章节版本和 Canon 决策已完成 | 查看历史 |
| blocked | 存在 stale、out_of_sync、handoff 冲突或失败 | 查看阻塞原因 |

### 5.2 ChapterWorkflowRead

新增章节工作台读取视图，建议结构如下：

~~~typescript
type ChapterWorkflowRead = {
  chapter_id: string;
  phase:
    | \"intent_required\" | \"planning\" | \"plan_feedback\" | \"scene_generation\"
    | \"scene_feedback\" | \"chapter_review\" | \"chapter_feedback\" | \"canon_feedback\"
    | \"completed\" | \"blocked\";
  chapter_status: string;
  intent: {
    text: string;
    optional_fields: Record<string, unknown>;
    unresolved_questions: string[];
  };
  plan_discussion: {
    messages: Array<{
      role: \"author\" | \"assistant\";
      kind: \"intent\" | \"question\" | \"answer\" | \"proposal\" | \"decision\";
      text: string;
      created_at: string;
    }>;
    pending_questions: string[];
    pending_proposals: Record<string, unknown>;
  };
  plan: {
    current_revision_id: string | null;
    accepted_revision_id: string | null;
    version: number | null;
    contract: Record<string, unknown> | null;
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
  active_run: RunSnapshot | null;
  affected_scene_ids: string[];
  stale_scene_ids: string[];
  blocking_reasons: string[];
  canon_run_id: string | null;
};
~~~

服务端只返回与当前 accepted plan、accepted scene revision、accepted chapter revision 和有效 handoff 一致的结果。前端不使用“最新记录”替代这些指针。

## 6. 后端设计

### 6.1 章节规划命令

将当前初始化接口降级为兼容/测试用途，主界面改用章节运行入口：

- POST /api/chapters/{chapter_id}/runs
  - 首次规划：run_scope=chapter、request_type=new_chapter、decision_target=plan，携带至少一个非空的 chapter_intent.text；结构化字段可选。
  - 已接受计划后的继续生成：显式携带 plan_revision_id，服务端校验等于当前 accepted plan。
  - 章节审校：request_type=review、decision_target=chapter，不能调用 WritingAgent。
- POST /api/runs/{run_id}/decisions
  - 扩展 target 为 plan|scene|chapter，保持 accept|feedback|cancel 状态机。
  - feedback 可以是作者自然语言回答，也可以是对 AI 契约建议的采纳、修改或拒绝；服务端将其作为可恢复运行的讨论记录保存。
  - 计划接受必须提交服务端产生的 plan_revision_id。
  - 章节接受必须提交服务端产生的 chapter_revision_id。
- GET /api/chapters/{chapter_id}/plan
  - 过渡期只读当前 accepted plan 指针，不创建计划、不触发 Agent；待 workflow 完全接管前端读取后，再决定是否一并移除。
- GET /api/chapters/{chapter_id}/workflow
  - 返回 ChapterWorkflowRead，供章节工作区首屏和 SSE 后刷新使用。
- GET /api/chapters/{chapter_id}/revisions
  - 继续复用现有章节版本读取接口，并补齐版本中场景版本映射和审校摘要。
- POST /api/chapters/{chapter_id}/rollback
  - 继续使用显式目标版本和作者决策，回滚后重新计算同步状态和 handoff 影响。

### 6.2 Worker 与领域边界

- ChapterPlannerAgent 只返回结构化计划，不直接写数据库。
- 计划接受由计划领域服务在事务中更新 accepted 指针并物化场景映射。
- 章节主流程的场景清单来自 accepted plan 的 SceneBrief[]；计划接受事务负责物化场景映射，不能由前端逐个手动拼装。
- 场景队列由 Worker 根据 accepted plan 顺序推进；当前场景没有 accepted 结果时不能跳过、插入或直接运行下一个场景。
- 未纳入 accepted plan 的手动场景不得进入章节主队列；手动场景只能走独立场景操作，并携带明确的场景基线和计划版本（如适用）。
- ChapterAggregator 只在场景资格检查通过后生成 staged 章节版本。
- ChapterReviewAgent 只负责章节级审校，不能替代场景审校。
- 章节接受事务只发布 chapter_revision.accepted outbox 事件；Canon 服务幂等消费该事件创建章节 Canon 运行。
- 运行取消、失败或被取代时，当前运行未决候选必须按既有规则转为 discarded。

### 6.3 兼容策略

- POST /api/chapters/{chapter_id}/plan 只作为迁移期兼容接口：新 UI、服务端主流程和新测试不得调用它。待章节规划运行入口、章节工作流读取视图和完整 Playwright 主流程验收通过后，删除该 POST 路由、前端 createChapterPlan 调用及其初始化测试；删除后不提供同语义的第二个初始化接口。
- GET /api/chapters/{chapter_id}/plan 只读 accepted plan，不属于计划生成；前端迁移到 GET /api/chapters/{chapter_id}/workflow 后，可以删除该 GET 路由及其客户端读取函数，或保留为明确标注的只读兼容别名。
- 现有场景级接口和独立场景操作保持兼容。
- 新增的章节工作流读取视图通过已有领域服务组合，不复制一套版本或状态存储。
- 旧数据没有章节意图时显示“需要补充章节意图”，不得用默认字符串静默补全。

## 7. 前端设计

### 7.1 页面信息架构

章节工作台保留三栏布局，但主对象从“选中场景”提升为“当前章节”：

~~~text
左侧：作品、卷、章节导航
中间：章节工作区
      - 阶段进度条
      - 章节计划/正文/章节审校三个视图
      - 当前场景内容与检查结果
右侧：
      - 章节契约
      - 当前场景简报
      - 阻塞与影响范围
      - Story Bible / Canon
      - 版本历史
底部：当前运行事件、反馈输入和决策按钮
~~~

章节页面默认展示章节正文和场景顺序。进入某个场景只是定位到章节队列中的一个执行单元，不改变主流程上下文。

### 7.2 建议组件边界

- ChapterWorkspace：章节工作流容器，读取并刷新 ChapterWorkflowRead。
- ChapterIntentForm：以自然语言意图为主，结构化字段作为可选补充；只禁止提交空意图，不要求作者一次性填满目标、状态或风格字段。
- PlanDiscussionPanel：展示 AI 澄清问题、契约建议和作者回答；作者确认前，建议内容不能进入 accepted plan。
- WorkflowPhaseBar：展示当前阶段、已完成阶段、阻塞阶段和下一步主动作。
- ChapterPlanReview：展示契约、场景顺序、必须/禁止事项，提供计划反馈/接受/取消。
- SceneQueue：展示场景顺序、状态、版本、阻塞原因和当前执行项。
- SceneReviewPanel：展示正文、审校问题、候选事实和场景级决策。
- ChapterReviewPanel：展示章节聚合版本、章节级问题、影响闭包和章节决策。
- ChapterRevisionHistory：展示章节版本、场景来源映射、比较和回滚。
- StoryBiblePanel：保留现有 Canon 能力，但显示来源版本和作用域。
- RunPanel：从“原始事件日志”提升为阶段进度和可读事件，同时保留事件详情折叠查看。

page.tsx 不再承载全部章节工作流状态；资源树、章节工作区、场景编辑和运行面板分别维护清晰边界。现有文件如果暂时不拆分，至少先提取上述视图模型和事件适配函数，避免继续增加条件分支。

### 7.3 交互规则

- 每个阶段只显示一个主动作，反馈、取消、查看详情作为次级动作。
- 章节计划页面默认先显示自然语言输入和 AI 讨论区；结构化字段按“已确认 / AI 建议 / 尚未确定”分组，不用空表单制造阻塞。
- AI 提问必须说明缺少的信息会影响什么；AI 建议必须允许作者逐项采纳、修改或拒绝，不能用一次“生成”动作静默填充所有字段。
- 章节主流程的主动作是“审阅/接受计划”和“运行下一场景”，不是“新建场景”；场景清单由计划展示，作者通过计划反馈调整清单。
- 计划未接受时，场景队列不能启动正文生成。
- 存在 stale 或 out_of_sync 时，章节接受按钮禁用，并显示具体场景和原因。
- 场景级 Canon 必须显示“场景作用域”；章节级 Canon 必须显示来源 ChapterRevision。
- Agent 内部节点名称默认转为中文阶段名称，原始节点和事件 payload 放在详情区。
- 失败、澄清、暂停和冲突必须显示下一步操作，不能只显示错误码。
- 继续保留右键删除，但删除资源和删除版本不能成为章节主流程中的主操作。

## 8. 分阶段交付

### 阶段 0：流程契约和测试夹具

产出：ChapterWorkflowRead、阶段枚举、计划/章节决策请求契约、章节意图 fixture、完整流程状态转移表。

验收：后端 schema、状态转移和错误码测试先行；现有 pytest、ruff、mypy 不回归。

### 阶段 1：章节意图与计划审阅

产出：自然语言章节意图输入、可选结构化字段、真实 new_chapter 规划入口、AI 澄清/建议讨论区、计划正文/有序场景清单展示、计划反馈/澄清/接受/取消；不以手动新建场景作为章节主流程入口。

验收：只输入自然语言意图即可启动规划；缺少影响生成的关键约束时进入 pending_clarification；AI 建议在作者确认前不进入 accepted plan；计划反馈产生新 plan revision；计划接受后才允许按 SceneBrief[] 顺序运行场景队列；主流程不要求作者手动创建或插入场景；旧初始化接口不再被新 UI 调用。

### 阶段 2：场景队列与场景审阅

产出：按 accepted plan 自动推进场景队列、阶段进度条、场景状态、当前场景正文、检查问题、反馈和接受。

验收：场景严格按计划顺序推进；场景反馈只影响当前场景和计算出的下游闭包；取消不会删除已接受版本；运行断线后可以按 SSE sequence 恢复。

### 阶段 3：章节聚合、审校与接受

产出：章节聚合触发、章节审校结果、影响闭包、stale 阻断、章节版本历史、章节接受和回滚。

验收：所有场景具备有效 accepted revision 后才生成 staged chapter revision；存在 stale 时不能接受；章节接受后状态为 in_sync 并发布可重放 outbox 事件。

### 阶段 4：Canon 闭环与完整旅程

产出：章节接受后自动进入章节 Canon 运行、候选逐条决策、Story Bible 更新、完整章节工作流 Playwright 测试。

验收：章节 Canon 只能使用 accepted chapter revision；场景级决策不能改全局 Canon；完整测试从填写章节意图开始，最终验证 Story Bible 正式条目和审计事件。

## 9. 验收标准

### 9.1 主流程验收

在干净 fixture 中完成以下操作且不离开章节工作区：

1. 只输入一段自然语言章节意图，故意不填写部分结构化字段。
2. 启动规划，查看 AI 的澄清问题或契约建议，并逐项回答、采纳或修改。
3. 查看 ChapterContract 和至少两个 SceneBrief，确认未确认的 AI 建议没有进入 accepted plan。
4. 对计划提交一次反馈，确认生成新计划版本。
5. 接受计划，系统根据 accepted plan 的有序 SceneBrief 物化场景映射并按顺序处理所有场景，不要求作者手动创建场景。
6. 对一个场景反馈，确认只生成该场景补丁并重新检查。
7. 接受所有场景，生成 staged ChapterRevision。
8. 查看章节审校结果和影响闭包，确认章节版本。
9. 进入章节 Canon，逐条确认一个事实、拒绝一个事件、暂缓一个剧情线。
10. 验证确认项进入 Story Bible，拒绝/暂缓项不进入正式 Canon。

### 9.2 阻断验收

- 计划未接受时不能创建正常场景队列运行。
- 未纳入 accepted plan 的手动场景不能加入章节主队列；主流程不得通过手动创建、插入或跳过场景绕过计划顺序。
- 章节没有自然语言意图时不能启动规划；有自然语言意图但缺少关键约束时，Planner 必须进入澄清或提出显式建议，不能静默补全。
- AI 提出的关键建议在作者确认前不能写入 accepted plan、正文基线、正式 Canon 或章节版本。
- 场景入口/出口不兼容时必须显示 stale 或阻塞原因。
- 章节存在未处理 stale 或 handoff 冲突时不能接受。
- 章节级 Canon 没有 accepted chapter revision 时不能创建。
- 取消、失败、superseded 运行的未决候选必须被丢弃，已接受版本不能丢失。

### 9.3 回归验收

- 后端全量 pytest、ruff、mypy 通过。
- 现有编辑器、运行状态、Story Bible、删除和版本历史 Playwright 测试通过。
- 新增 frontend/tests/chapter-workflow.spec.ts 覆盖完整主流程、计划反馈、场景反馈、stale 阻断和章节 Canon。
- 新增 API 测试覆盖章节工作流读取视图、计划决策、章节聚合资格、章节接受和 Canon 来源绑定。
- 服务重启后，运行、事件、版本、Canon 和章节工作流读取视图保持一致。

## 10. 风险与取舍

| 风险 | 影响 | 处理方式 |
| --- | --- | --- |
| 继续复用初始化计划接口 | UI 看似有计划，实际没有作者规划闭环 | 新 UI 只调用真实章节规划运行，迁移期后删除初始化 POST |
| 把场景所有状态都复制到前端 | 状态漂移、刷新后不一致 | 以 ChapterWorkflowRead 为唯一工作台读取源 |
| 章节反馈直接重跑全部场景 | 成本高且破坏版本语义 | 服务端计算影响闭包，闭包外只重新验证 |
| 章节接受和 Canon 在同一同步请求内完成 | 超时、重复写入和恢复困难 | 章节接受只发布 outbox，Canon 独立运行并幂等消费 |
| 先堆按钮再补流程 | 操作入口多但主线仍不清晰 | 每个阶段固定一个主动作，阶段状态先于按钮 |
| 真实模型输出非确定 | E2E 断言不稳定 | 断言状态契约、来源、事件和写入边界，不断言固定文本 |

## 11. 完成定义

只有同时满足以下条件，二阶建设才算完成：

- 作者能够从章节意图开始，不手动创建场景，也不依赖初始化快捷接口，完成章节计划到章节接受。
- 前端章节工作台能够展示并操作计划讨论、场景队列、正文审阅、章节审校、版本历史、Canon 和阻塞恢复。
- 后端能够提供对应的规划、决策、场景生成、版本、事件、读取和恢复能力，并与前端通过真实端到端流程打通。
- 计划书流程图中的计划审阅、场景审阅、章节审校、章节接受和 Canon 决策在 UI 中都有对应入口。
- 后端状态、版本指针、事件、候选来源和 Story Bible 约束均由测试覆盖。
- 新增完整 Playwright 旅程通过，并保留现有功能回归测试全绿。
- 页面不再把“单场景编辑器”误称为完整章节工作台；当前阶段、下一动作和阻塞原因对作者清晰可见。
