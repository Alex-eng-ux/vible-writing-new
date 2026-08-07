# 2026-08-05 开发日志

## 工作性质与范围

本轮在《连续小说创作工作室 V1 工程交付计划》既有边界内，不新增任务编号，完成六项真实模型 Agent 接线/验证工作：

1. **章节 Agent 接线**：找出章节图中实际使用的章节规划、章节审校和聚合 Agent，将 `ChapterPlannerAgent`/`ChapterReviewAgent` 接入真实 `ModelProvider`/DeepSeek 配置，分别用已有输出 schema 严格校验；`ChapterAggregator` 是领域服务（确定性聚合）不接入模型。
2. **契约提示词补齐**：为所有已接入 Provider 的真实模型 Agent 建立独立 system prompt，逐项对应 Pydantic 输出 schema 的字段/枚举/嵌套/必填；`CanonAgent` 先准备与 `CanonOutput` 对齐的提示词与测试设计；`ChapterAggregator` 不加提示词。
3. **CanonAgent 真实模型接线**：在用户确认后，将 `CanonAgent` 接入真实 `ModelProvider`/DeepSeek 配置，复用 `traced_call` 与 `CANON_SYSTEM_PROMPT`，以 `CanonOutput` 严格校验；保留 Fake 默认行为，Agent 只返回候选不直接写 Canon。
4. **Worker 层章节链路修复**：修复 run_worker 构造章节运行信封缺 `chapter_contract` 的问题，使 Worker 驱动的 `ChapterGraph` 能真正调用 `ChapterPlannerAgent`/`ChapterReviewAgent`；补充 Worker 层成功链路、缺失契约与失败不写库测试。
5. **Provider 失败自动重试**：为 `DeepSeekModelProvider` 增加有限次 + 指数退避的失败自动重试，仅对可重试错误（限流/不可用/服务端/未知）重试，认证与结构化响应错误不重试；重试只发生在模型出站调用层，不产生重复版本/候选/Canon/事件写入（保持幂等与 fencing 约束）。
6. **本机进程重启与三链路真实模型验证**：重启本机 API 与 Worker 使真实 Provider、章节契约、自动重试配置生效；health 检查 + 真实模型 smoke + 场景/章节/Canon 三链路门控真实链路验证；发现并修复 `max_tokens` 默认值过小导致真实输出被截断的根因。

六项均保留了 Fake model 默认行为（未注入 provider 时确定性输出，普通测试不访问网络）。`RevisionAgent` 接线（场景图）于本会话前序已完成，本轮未见回归。

本轮补充了二阶工作台设计审查，并根据用户确认修正章节规划交互语义：章节意图不应是作者必须一次性填满的结构化表单，而应允许作者先用自然语言表达想法，再由 AI 通过提问和可编辑建议逐步形成章节契约。本轮继续校对现有 Agent 的真实字段使用，补充 Planner 专属 Prompt/Hook、Agent 职责收紧、共享信封字段投影和兼容迁移边界。本轮只修改设计文档和开发日志，没有修改应用代码。

## 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 章节图只接 `ChapterPlannerAgent`/`ChapterReviewAgent`，`ChapterAggregator` 不接模型 | 章节图实际调用的是规划与审校两个模型 Agent；聚合器从库中聚合 accepted 章节版本生成 staged 版本，是确定性领域服务，不需要 Provider | `run_worker.py` 章节图构造器、`chapter_planner.py`、`chapter_review_agent.py` |
| 每个接入 Provider 的 Agent 建立独立 system prompt，统一收敛到 `prompts.py` | 审查发现 `model_provider.py` 默认系统提示词的 `candidate_facts` 用了旧字段 `entity_id/fact_text/source_ref`，与实际 `WritingOutput.CandidateFact( local_key/claim/scope/status )` 不符；改为每 Agent 独立提示词并移除默认提示词 | `app/agents/prompts.py`（新建 7 个常量） |
| `system_prompt` 从可选改为必填 | 阻止再出现"默认提示词与 schema 脱节"的被遮蔽用法，强制调用方显式注入与 schema 对齐的提示词 | `model_provider.py` 的 `ModelProvider`/`DeepSeekModelProvider`/`invoke_structured` |
| `CanonAgent` 先备提示词与测试设计，验证通过后接入真实 Provider | 用户明确"先完成章节 Agent 验证，再等待下一条指令接入 CanonAgent"；提示词与对齐测试先行，真实调用点由下一条指令落地 | `prompts.py` 的 `CANON_SYSTEM_PROMPT`、`test_prompt_contracts.py` |
| `CanonAgent` 接入真实 Provider，复用 `ModelProvider`/`traced_call`/`CANON_SYSTEM_PROMPT`，严格校验 `CanonOutput`；保留 Fake 默认；只返回候选不直接写 Canon | 用户下一条指令明确扩展 CanonAgent 真实模型接线；将其加入已接线 Agent 集合，避免再出现"提示词与真实调用点脱节" | `canon_agent.py`、`run_worker.py` 的 Canon 分支 |
| 每个提示词补"合法响应"与"非法响应"测试 | 确认 schema 校验仍是最终边界，而非提示词靠语言约束兜底 | `tests/agents/test_prompt_contracts.py`（20 项） |
| 章节 Agent 测试用 `chapter_contract={}` 而非 `None` | `ChapterPlanOutput.chapter_contract` 是必填 dict 字段，`None` 会被 Pydantic 拒绝 | `test_chapter_agents_provider.py` |
| 修复 run_worker 章节信封缺 `chapter_contract`，Worker 驱动的 ChapterGraph 真正调用 Planner/Review | 用户指令明确修复 worker 构造章节信封缺 chapter_contract 的问题；此前 Worker 驱动章节运行在 planner 处缺契约即暂停、不触达模型（仅 ChapterGraph 层以 bound 契约信封验证） | `run_worker.py` 的 `_chapter_contract_for`、`tests/runtime/test_real_chapter_worker_chain.py` |
| Provider 失败自动重试：仅重试可重试错误，认证/结构化响应错误不重试，有限次数 + 退避，重试不重复写入 | 用户指令明确实现 Provider 失败自动重试；重试白名单显式排除 `LLM_RESPONSE_INVALID`（错误注册表虽标 retryable，但结构化输出错误重试大概率重演缺陷）；重试只发生在模型出站调用层，不重新执行 Agent 业务逻辑，故无重复版本/候选/Canon/事件写入 | `model_provider.py` 的 `_RETRYABLE_CODES`/`_invoke_with_retry`/`_retry_delay`、`tests/agents/test_model_provider.py`、`tests/runtime/test_real_writing_chain.py` |
| 重启本机 API 与 Worker，执行 health + 真实模型 smoke，确认场景/章节/Canon 三链路可用；不输出 API Key | 用户指令明确重启进程使真实 Provider、章节契约、自动重试配置生效，并实测三链路 | 本机 API/Worker 进程、`smoke_real_model.ps1`、门控真实链路测试 |
| 修复 `max_tokens` 默认值过小（512→4096） | 真实模型 deepseek-v4-flash 在 512 下 finish_reason=length 截断 JSON，导致全部真实链路无法解析；实测场景写作 0.4k 字符、Canon 候选 2.1-2.6k 字符，512/2048 均截断、4096 完整 | `model_provider.py` 构造器默认、`smoke_real_model.ps1`（512→2048） |
| 门控真实链路测试按关键不变量断言（模型被调用并观测、run 未 failed、无写入），不再断言确定性全遍历 | 真实模型非确定性（可在任意节点返回 needs_clarification 提前暂停，属正确行为）；原断言"必须遍历到全部下游节点"对真实模型过度 | 4 个 `real_deepseek` 门控测试 |
| 新增 `POST /api/chapters/{id}/plan` 幂等命令：初始化并接受章节计划，使场景续写/改写/审校可用 | 界面「章节尚无 accepted plan」实为前后端断链：前端只有 createSceneRun、无章节计划初始化；章节计划运行 waiting_feedback 事件 payload 无 plan_revision_id，前端拿不到要接受的 plan id；Agent planner 只返回候选不写库。本接口把既有场景映射进计划并接受（scene_id 复用不重复建场景），只做初始化不绕过 Task 2 计划事务 | `api/chapters.py`、`tests/api/test_chapter_plan_init.py`、`frontend/src/services/api.ts`、`frontend/src/app/page.tsx` |
| 章节意图改为“自然语言起步、结构化字段可选、AI 讨论后冻结契约” | 用户明确不希望作者被迫一次性填满题材、读者、文风、开场/结尾状态等字段；AI 可以追问和提出建议，但关键内容必须由作者确认 | `docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md` 的用户目标、流程图、状态、接口、组件和验收标准 |
| 规划讨论使用同一个 `ChapterPlannerAgent`，不新增通用聊天 Agent | 规划问题、契约建议和场景拆解必须共享同一份章节上下文，避免聊天 Agent 与规划 Agent 产生两套不一致的计划语义 | 二阶设计文档的 `ChapterPlannerAgent` 多轮规划模式；当前代码尚未实现该模式 |
| AI 推断的关键目标、状态、人物关系和必达剧情点只能作为建议 | 关键内容未经作者确认不得进入 accepted plan、正文基线、章节版本或正式 Canon；接受动作仍由作者决策和事务服务完成 | 二阶设计文档的 `plan_discussion`、建议来源标记和阻断验收；当前代码尚未实现建议确认字段 |
| 二阶计划书核心目标压缩为一条端到端主流程，并将产品目标与工程约束分层 | 用户认为此前计划内容完整但不够简洁明了；保留“意图→Planner 讨论→计划展示→作者确认→自动逐场生成→逐场反馈/接受→章节聚合”作为唯一主线，API、状态、迁移和回归测试下沉到工程章节 | `docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md` 已完成压缩 |
| 恢复压缩前的完整工程交接版计划 | 用户确认精简版偏离初衷，要求恢复完整的前后端目标、状态、接口、前端、阶段交付、验收和风险内容；同时保留端到端主流程作为主线 | `docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md` 已恢复为完整版本 |
| 章节规划只由 `ChapterPlannerAgent` 承担，其他 Agent 收紧职责并复用现有能力 | 用户要求通过收紧 Agent 分工控制任务范围；不新增通用聊天 Agent，不把章节规划讨论逻辑扩散到 Writing、Continuity、Review、Revision、ChapterReview、Aggregator 或 Canon | 计划书新增 Agent 职责收紧与复用边界；其他 Agent 只适配 accepted plan、`SceneBrief`、版本指针和来源引用 |
| 共享 `AgentInputEnvelope` 与各 Agent Prompt 分层处理 | 代码核对发现 Worker 会把多个字段装入共享信封，但不同 Agent 实际读取字段不同；不能把“信封携带”误写成“所有 Agent 都应读取” | 计划书新增共享信封、服务端投影、Agent 专属 Prompt 三层字段适配表和字段泄漏测试要求 |
| Planner 的提示词和专属 Hook 必须作为独立实现任务 | 用户指出原计划只写了结果目标，没有明确 `CHAPTER_PLAN_SYSTEM_PROMPT`、`_build_prompt()` 和 Planner Hook 的改动逻辑 | 计划书新增 Planner 输入/提示词/`PlannerDiscussionHook` 章节，并规定其与 `SchemaHook`、`AgentResultRouter`、`ChapterGraph` 的接线顺序 |

## 关键规则与取舍

- **复用端口与配置**：章节 Agent、契约提示词测试统一走 `ModelProvider` 协议 + `DeepSeekModelProvider`（httpx、`response_format=json_object`、`traced_call` 埋点），不新增第二套调用栈。
- **fail-closed**：Provider 失败（401/超时/非法响应）时运行进入 `failed`，不产生章节版本、候选、Canon 或 handoff 写入。错误码映射：401→`LLM_AUTH_ERROR`、超时→`LLM_UNAVAILABLE`、非法输出→`LLM_RESPONSE_INVALID`。
- **提示词与 schema 逐项对齐**：写作/连续性/评审/修订/章节规划/章节审校/Canon 七类提示词均覆盖对应 Pydantic schema 的 `status` 枚举、嵌套结构、必填字段；`CanonOutput`/`CanonCandidate` 对齐提示词已于 CanonAgent 接线时落到真实调用点。
- **测试不触网**：普通测试注入 Fake provider 或桩 provider，真实 DeepSeek 走门控 smoke（未显式开启不访问网络）。
- **worker 层章节契约装配**：`run_worker` 新增 `_chapter_contract_for`，从已接受章节计划修订源填充 `chapter_contract`（优先 `run.plan_revision_id` 指向的计划，否则回退 `ChapterPlanRevisionLink` 当前接受计划）；无章节或无接受计划返回空 dict 让 planner 进入 needs_clarification（缺契约不触达模型）。Worker 驱动的章节运行在有契约时真正调用 ChapterPlannerAgent/ChapterReviewAgent。
- **Provider 失败自动重试**：`_RETRYABLE_CODES` 白名单显式排除 `LLM_RESPONSE_INVALID`（错误注册表虽标记 retryable，但结构化输出错误重试大概率重演同一缺陷）；认证错误（`LLM_AUTH_ERROR`）与请求被拒（`LLM_INVALID_REQUEST`/`LLM_ENDPOINT_NOT_FOUND`）不重试；`max_retries` 表示首次失败后的额外重试次数（默认 3），每次重试前休眠 `retry_backoff * 2**n + 抖动`；重试只在模型出站调用层，不重新执行 Agent 业务逻辑，故不产生重复版本/候选/Canon/事件写入（保持幂等与 fencing 约束）。
- **`max_tokens` 默认 4096**：真实模型 deepseek-v4-flash 在默认 512 下以 `finish_reason=length` 截断长 JSON，512/2048 均无法完整解析 Canon 候选（2.1-2.6k 字符），4096 完整；默认值取 4096 以承载最长输出 + schema。
- **真实模型时序非确定性**：门控真实链路测试按关键不变量断言（真实模型被调用并被观测到至少一个 `:llm:` 节点、run 未 failed、无业务写入），不再断言"必须遍历到全部下游节点"——模型可在任意节点返回 needs_clarification 提前暂停（正确行为）。
- **章节规划对话边界**：最小输入只要求非空自然语言意图；题材、目标读者、文风、章节目标、开场/结尾状态、POV、必达和禁止事项均可选。AI 可以提问或提出可编辑建议；只有作者回答、采纳或修改后，内容才能冻结为 `ChapterContract`。
- **Planner Agent 复用**：AI 讨论不是独立通用聊天 Agent，而是 `ChapterPlannerAgent` 在初次规划、澄清恢复和计划反馈场景下的多轮调用；`RunService`、`AgentResultRouter` 和 checkpoint 负责保存讨论与恢复，不负责生成内容。
- **当前 Hook 边界**：通用 `SchemaHook` 已要求 `needs_clarification` 携带问题，`AgentResultRouter` 已设置 `pending_node` 并暂停，`ChapterGraph` 已支持 checkpoint 恢复；二阶设计需要新增 Planner 输入上下文/建议归一化边界，且 Hook 不直接写正式计划。
- **Planner 专属接线边界**：二阶实现只新增 `PlannerDiscussionHook` 并接入章节 Planner 结果链路；执行顺序固定为 `SchemaHook → PlannerDiscussionHook → AgentResultRouter`，Hook 只做讨论上下文归一化、建议来源/确认状态和阻断校验，不写计划、场景或 Canon。
- **Agent 字段投影边界**：`AgentInputEnvelope` 是共享传输信封，不等于 Prompt 输入；服务端必须按 Planner、场景图、章节审校和 Canon 图投影最小字段，Planner 讨论不得泄漏到其他 Agent。
- **Agent 职责收紧**：`ChapterPlannerAgent` 是唯一章节规划讨论 Agent；Writing/Continuity/Review/Revision 处理场景执行，ChapterReview 处理章节审校，Aggregator 保持确定性聚合，CanonAgent 处理候选提取，均不扩展为聊天 Agent。

## 已完成产出

- 完成章节 Agent、CanonAgent 的真实 Provider 接线，补齐七类 Agent 独立 system prompt，并保留未注入 Provider 时的 Fake 默认行为。
- 修复 Worker 章节契约装配、Provider 有限重试和 `max_tokens` 截断问题；补充对应的单元、运行时和真实模型门控测试。
- 新增章节计划初始化 API 与前端“生成章节计划”入口，打通新建章节后的 accepted plan 前置流程。
- 形成二阶章节工作台设计文档，明确自然语言意图起步、同一 Planner 多轮讨论、作者确认后冻结契约，以及后续输入契约/提示词/Hook/测试/UI 的实现顺序。
- 根据现有代码逐个核对 Writing、Continuity、Review、Revision、ChapterPlanner、ChapterReview 和 CanonAgent 的实际字段读取，确认 `scene_brief`、作者反馈、正文基线、版本指针和 `canon_scope` 的真实使用边界。
- 更新二阶设计文档，新增 Planner 输入/提示词/`PlannerDiscussionHook` 实施任务、Agent 职责收紧与复用边界、共享信封字段投影表、字段泄漏测试、影响边界和兼容迁移规则。
- 完成 Git 仓库初始化与 GitHub 远程仓库配置，远程地址为 `https://github.com/Alex-eng-ux/vible-writing-new.git`；整理 `.gitignore`，排除 `.env`、缓存、`node_modules`、日志、`egg-info` 和 `.trae` 等生成或敏感文件。
- 完成本地首个提交、与远程 README 初始提交的合并，并将 `main` 成功推送到 GitHub；当前本地 `main` 跟踪远程 `main`。

## 验证结果

- 章节 Agent：`backend/tests/agents/test_chapter_agents_provider.py`（10 项：注入成功、缺契约、错误上抛、schema 非法、Fake 确定性输出）+ `test_real_chapter_chain.py`（成功链路、Planner 401、Review 超时、非法输出、真实 DeepSeek 门控）。
- 契约提示词：`backend/tests/agents/test_prompt_contracts.py`（20 项：提示词独立性、status 枚举覆盖、schema 字段/枚举参数化覆盖、合法响应通过、非法响应被拒为最终边界、Canon 对齐、CanonAgent 注入 provider 时使用 CANON_SYSTEM_PROMPT）。
- CanonAgent：`backend/tests/agents/test_canon_agent_provider.py`（5 项：注入成功、缺版本指针不调模型、Provider 错误原样上抛、schema 非法→LLM_RESPONSE_INVALID、未注入保持 Fake）+ `test_real_canon_chain.py`（成功链路持久化候选并暂停确认无正式 Canon 写入、401/超时/非法失败不写库、真实 DeepSeek 门控）。
- Worker 层章节链路：`backend/tests/runtime/test_real_chapter_worker_chain.py`（4 项执行 + 1 项门控：成功链路 Planner→Review 均被真实 Provider(mock) 调用且暂停无版本写入、缺失契约不触达模型 pending_clarification、401 失败不写库 LLM_AUTH_ERROR、真实 DeepSeek 门控）。
- Provider 重试：`backend/tests/agents/test_model_provider.py`（5 项新增：限流后成功重试 1 次、耗尽 max_retries 后上抛、认证错误不重试、结构化响应错误不重试、退避指数增长）+ `test_real_writing_chain.py`（重试幂等链路：先 429 后成功、出站 2 次、事件无失败无重复、无重复业务写入）。
- 本机进程重启与三链路验证：health（`/health` ok、`/ready` ready）通过；`smoke_real_model.ps1` **PASS（exit 0）**（provider 200、写作 schema 校验通过、401/超时映射、无 Key 泄漏、只读）；场景/章节/Canon 三链路门控 `real_deepseek` **6 项全部通过**。
- 全量 `pytest`：**471 passed + 6 skipped**，退出码 0；`ruff check app tests` 通过；`mypy`（model_provider.py）通过。
- 排障记录：`test_prompt_contracts.py` 中 Agent 需 `.run(envelope)` 而非 `Agent()` 调用；Revision 用例信封补 `author_feedback` 才触达模型；桩 Provider 与 `test_model_provider.py` 多处调用补 `system_prompt`；worker 章节测试 `before` 快照改在 setup 之后；重试链路测试事件断言写死为 `run_waiting_feedback` 但场景带 brief 实际进入 `run_pending_clarification`；**真实链路全失败根因 = `max_tokens=512` 截断**（实测 512 截断 150 字符、2048 截断 Canon 761 字符、4096 完整 2119 字符）；smoke 脚本 PS `$pyOut.Trim()` 对数组抛错 + 旧 schema 提示词；门控测试对真实模型过度断言需放宽为关键不变量。
- 二阶设计文档已写入并完成 UTF-8 回读校验；本轮未重跑应用测试，因为没有应用代码变更。设计文档明确了自然语言意图、AI 讨论、Planner 复用、`plan_discussion` 读取视图、Planner 专属 Hook 方向和完整旅程验收。
- 二阶设计文档已按用户要求压缩：从 402 行/27143 字节降为 267 行/14033 字节；保留 11 个章节结构、端到端主流程、接口迁移边界、状态读取模型、阶段交付和验收；UTF-8 回读、10 项关键要求和占位符检查通过。
- 随后按用户反馈恢复二阶设计文档完整版本：当前 406 行/27394 字节，保留完整前后端交付目标、状态读取模型、接口迁移边界、前端组件、阶段交付、主流程验收、回归验收和风险取舍；UTF-8 回读、12 项关键要求和占位符检查通过。
- 当前代码核对结果：`CHAPTER_PLAN_SYSTEM_PROMPT` 只声明 `ChapterPlanOutput` JSON 字段，没有自然语言意图/可选字段/建议确认规则；`AgentInputEnvelope` 没有独立 `chapter_intent` 或讨论历史；章节图虽将 `author_feedback` 放入 envelope，但 `ChapterPlannerAgent._build_prompt()` 当前未把该反馈加入模型 prompt；现有测试只覆盖整个 `chapter_contract` 缺失时的澄清，不覆盖多轮规划对话。
- 本轮 Agent 字段核对为文档级验证：`WritingAgent` 当前读取 `scene_brief`、`accepted_text` 和作者反馈；`RevisionAgent` 读取场景基线和作者反馈；Continuity/Review 主要读取正文和来源；CanonAgent 校验 `canon_scope` 与对应 accepted revision；Planner/ChapterReview 读取 `chapter_contract`。字段投影适配尚未实现。
- 二阶设计文档新增内容已完成 UTF-8 回读和关键条目检查；本轮仍未运行应用测试，因为没有应用代码变更。
- Git 验证结果：提交历史包含本地首个提交 `67b0003`、远程初始提交 `c8243b7` 和已推送的合并提交 `ff89a72`；`git status --short --branch` 显示 `main...Alex-eng-ux/vible-writing-new/main`，确认本地与远程同步。
- 本轮 Git 操作只涉及仓库元数据、`.gitignore` 和开发日志；未重新运行应用测试，已有应用测试结论未因本轮 Git 操作改变。

## 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| 章节聚合器不接模型 | 无模型输出需校验，确定性聚合逻辑无提示词 | 已文档级说明 |
| mypy 类型：`_run_fake` 传 `envelope.canon_scope`（`Literal['chapter','scene',None]`）与 `_build_candidate(canon_scope: str)` 不兼容 | 编译期类型告警 | 已在 `_run_fake` 内 cast 为 `Literal["chapter","scene"]`（run() 已校验非 None），mypy 通过 |
| 重试退避含随机抖动，测试用注入 `sleep` 捕获延迟 | 抖动使延迟不确定，单元测试按区间断言（base ≤ 延迟 < base+抖动） | 已实现并测试；生产使用真实 time.sleep |
| 真实模型时序非确定性 | 模型可在任意节点返回 needs_clarification 提前暂停（正确行为），需门控测试按关键不变量断言 | 三链路门控测试已按不变量通过 |
| Planner 当前只在整个 `chapter_contract` 为空时澄清，不能对缺失的单个关键约束提出问题 | 作者无法从自然语言逐步讨论计划；部分契约可能被模型或 Fake 默认静默补全 | 已通过 `chapter_planner.py`、`prompts.py`、`schemas.py` 源码核对发现；尚未实现修复 |
| 作者反馈虽被章节图写入 envelope，但 Planner prompt 没有读取 `author_feedback` | 计划反馈/澄清恢复可能不会进入下一次模型输入，讨论链路语义不完整 | 已通过 `chapter_graph.py` 与 `chapter_planner.py` 源码核对发现；尚未实现修复 |
| 当前只有通用 SchemaHook、Router 和 TraceHook，没有 Planner 专属讨论上下文/建议确认 Hook | 无法统一校验提问、建议来源、作者确认和未决假设；可能把未确认建议误当正式计划 | 已通过 `nodes.py`、`hooks.py`、`hook_registry.py` 和 `result_router.py` 源码核对发现；尚未实现修复 |
| 现有 Planner 提示词测试只校验 JSON/schema 字段与非法响应 | 即使测试全绿，也不能证明自然语言意图、多轮问答、建议确认行为成立 | 已通过 `test_prompt_contracts.py`、`test_chapter_agents_provider.py` 核对发现；需新增行为测试 |
| 二阶计划书曾存在产品目标、工程接口、迁移策略和回归要求重复混排 | 精简版虽然降低篇幅，但用户认为它削弱了完整前后端交付目标 | 已先压缩为 267 行，后按用户反馈恢复为 406 行完整工程交接版 |
| “场景自动生成”的放行语义仍需在实现前固定 | 主流程要求按计划顺序自动生成，作者逐场查看、反馈和接受；Worker 的推进门控必须与该交互一致 | 压缩版计划书已明确语义；待实现阶段用状态和测试固定 |
| `.vs/` 目录仍为未跟踪项 | 后续提交可能误带入 Visual Studio 本地状态文件，造成仓库噪声 | `git status --short --branch` 已确认；本轮未修改 `.vs/` 或 `.gitignore`，待后续决定是否加入忽略规则 |
| 共享信封当前没有 Agent 字段投影层 | 新增 Planner 讨论字段后，若直接沿用完整信封传给所有 Agent，可能造成 Prompt 污染、职责越界或旧调用回归 | 已通过现有源码核对发现；计划书已定义投影边界，但代码尚未实现 |
| 当前场景 Agent 对 `scene_brief` 的 Prompt 使用不一致 | WritingAgent 已读取 `scene_brief`，Continuity/Review/Revision 主要使用正文和来源；若不补服务端资格校验，场景执行可能无法严格绑定 accepted plan | 已通过 Agent 源码核对发现；待字段投影和场景版本绑定实现/测试 |
| Planner 专属 Prompt/Hook 尚未落地 | 当前 Planner 仍以 `chapter_contract` 为空作为主要澄清条件，无法完成多轮意图讨论和建议确认 | 设计任务已写入计划书；应用代码和行为测试尚未实现 |

## 当前未完成事项与下一步

1. 场景图四 Agent、章节图两 Agent、Canon 分支 CanonAgent 均已接入真实模型并通过门控链路验证；契约提示词已收敛到 `backend/app/agents/prompts.py`（7 个独立提示词）。
2. Task 9 交接文档已更新 CanonAgent 接线状态、测试结果与剩余风险；Worker 层章节链路已修复（`_chapter_contract_for` + `test_real_chapter_worker_chain.py`）；Provider 失败自动重试已实现（`_RETRYABLE_CODES`/`max_retries`/退避注入）。
3. 本机 API 与 Worker 已重启，真实 Provider、章节契约与自动重试配置已生效；三链路门控真实链路全部通过（`max_tokens` 默认改 4096）。
4. 场景运行前置断链已修复：新增 `POST /api/chapters/{id}/plan` 幂等命令 + 前端「生成章节计划」按钮，界面新建章节后点按钮即可获得 accepted plan，场景续写/改写/审校可用；API 已重启加载新路由，真实 API 端到端 smoke 通过（before=None → accepted v1 → 回读一致）。
5. 已形成二阶设计：章节意图以自然语言起步，AI 通过同一个 `ChapterPlannerAgent` 进行澄清和建议，作者确认后才冻结 `ChapterContract`；计划书路径为 `docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md`。
6. 待实现的直接任务：扩展 `AgentInputEnvelope`/`ChapterPlanOutput` 的意图、讨论和建议字段；改写 `CHAPTER_PLAN_SYSTEM_PROMPT` 与 `_build_prompt()`；补 Planner 专属上下文/建议校验 Hook；补多轮澄清、反馈恢复和“未确认建议不得进入 accepted plan”的测试；完成后再接章节工作台 UI。
7. 真实模型时序非确定性已由门控测试以关键不变量覆盖；Provider 失败自动重试的退避/次数参数配置化仍可作为后续增强。
8. 本次交接以二阶章节规划设计文档为实现基线：下一轮先落实 Planner 的输入契约、提示词与专属 Hook，再补多轮对话/反馈恢复/建议确认测试，最后进入章节工作台 UI；本轮日志补记没有改变应用代码，应用测试仍未重跑。
9. 二阶计划书曾按用户意见完成压缩，但用户随后确认精简版偏离初衷；已恢复完整工程交接版，保留端到端主流程并恢复前后端目标、状态、接口、前端、阶段交付、验收和风险章节；本轮仍只修改文档，不修改应用代码。
10. GitHub 发布已完成，后续提交前需评估 `.vs/` 是否加入 `.gitignore`；当前应用功能开发和测试仍按二阶章节工作台计划推进。
11. 已根据现有 Agent 源码校对并修正计划书字段适配：下一步先实现共享信封到 Planner/场景图/章节审校/Canon 图的最小字段投影，再实现 Planner Prompt、`_build_prompt()` 和 `PlannerDiscussionHook`，禁止把讨论上下文扩散到其他 Agent。
12. 计划书新增的职责收紧、字段投影和兼容迁移规则目前只有文档级验证；完成这些设计实现后，必须补字段泄漏、旧构造兼容、Planner 多轮恢复和完整章节工作流测试。
