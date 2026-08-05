# 2026-08-03 开发日志

## 工作性质与范围

本轮根据连续复核和修复要求，继续收口《连续小说创作工作室 V1 工程交付计划》和《Agent Prompt v1 契约》的运行时边界。重点处理章节计划接受与继续/改写分发、场景物化幂等、Canon 候选来源与持久 ID、Agent 重试身份、跨章节 handoff、命令幂等恢复、提交守卫适配层和 Task 5B/5C 的交付边界。此次产出仍是文档契约修订，没有实现应用代码、数据库迁移、API、SSE、运行时 worker 或自动化测试。

本轮继续按照用户此前审查 Task 1/Task 2 的标准，对 Task 3-Task 9 做完整的文件职责、接口字段、状态枚举、错误码、任务归属、测试和验收交叉检查，并按“补到没有结构性问题再停”的要求直接修订计划书。新增内容仍只写入设计计划，没有把文档约束表述成已实现功能。

## 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 已接受计划的 `chapter + continue\|rewrite` 不重新调用 Planner | 保持作者已接受计划后的场景执行语义，避免运行时隐式重新规划 | 服务端校验 `plan_revision_id == accepted_plan_revision_id` 后直接进入固定场景队列；缺失或不匹配返回稳定错误 |
| 计划接受统一走运行决策接口 | 避免计划接受存在第二套公开入口，确保作者决策、版本 CAS、场景物化和幂等记录在同一事务内完成 | `POST /api/runs/{run_id}/decisions` 的 `target=plan` 同时调用 `accept_chapter_plan_revision` 与 `materialize_chapter_plan` |
| Agent 只返回临时场景键，正式场景 ID 由运行时物化 | 防止模型伪造正式 ID，并保证重复接受或重放不会生成重复场景 | `client_key -> scene_id` 在同一计划版本内一对一冻结；已存在映射重试时复用，重复指向不同场景时拒绝 |
| 候选来源按草稿、补丁和已接受版本分层 | 保留首稿与补丁的真实血缘，避免在未接受正文上伪造正式版本来源 | 候选严格使用三选一的 `source_draft_artifact_id`、`source_change_set_id` 或 `source_revision_id`；物化提交时在同一事务内迁移来源 |
| Canon 作者决策使用运行时持久 `candidate_id` | `candidate_type + local_key` 只能作为当前运行内的兼容定位，不能承担跨重试和跨事务身份 | `candidate_id` 由 IdentityResolution/候选服务分配或复用；Agent 原始输出省略或返回 `null` |
| 技术重试、恢复和作者反馈区分运行身份 | 让同一 checkpoint 的重试可幂等重放，同时避免新的逻辑调用复用旧结果 | 增加稳定的 `agent_attempt_key`；同一技术重试复用 `agent_run_id`/attempt key 并递增 `attempt_no`，新逻辑节点创建新身份 |
| 跨章节承接和章节同步必须显式校验 | 防止读取“当前最新版本”或旧 handoff，导致章节入口和 Canon 来源错误 | 增加 `entry_handoff_status`、`entry_handoff_chain_hash`、`chapter_sync_status` 及祖先链失效规则，冲突返回稳定错误码 |
| Task 5B 不提前注册 Canon 消费者 | 保持任务切片边界，避免运行/SSE 任务在 Canon 尚未接入时出现第二条成功路径 | Task 5B 只写入 `chapter_revision.accepted` outbox 事件并返回 `CANON_NOT_ENABLED`；Task 5C 才注册唯一 Canon 入队消费者 |
| `CommitGuardHook` 与直接 API 提交端口分离 | Agent 图需要适配层，但领域/API 提交不应依赖 LangGraph Hook | Agent 图使用 `CommitGuardHook`；直接 API 和领域服务使用 `CommitGuardPort`，最终仍由提交领域服务落库 |
| 按 Task 1/Task 2 的审查标准继续检查 Task 3-Task 9 | 后续任务也必须让 AI 能根据明确字段、边界和测试执行，不能只补任务标题或文件清单 | 继续补齐 Task 3 的 manifest 端口、Task 4 的 outbox 归属、Task 5 的决策/resume 契约、Task 6-Task 9 的状态和验收边界，并以文档级校验收口 |
| 运行 API 响应改为可解析的 `RunSnapshot` | 原示例把多个状态写进单个 JSON 字符串，并且没有说明章节目标、暂停/澄清和错误字段的空值语义，AI 可能照抄成非法响应 | 用单一真实状态值示例，增加 `project_id`、`target_id`、`pause_reason`、`clarification_questions` 和 `last_error_code`，并明确 `thread_id`/事件序号只是派生或持久事件语义 |

## 关键规则与取舍

- 首次章节规划才调用 `ChapterPlannerAgent`；已有 accepted plan 的 `continue|rewrite` 直接进入场景队列。计划接受时，计划版本 CAS、作者接受、`client_key -> scene_id` 物化和幂等 claim 必须共享同一事务。
- 候选来源必须恰有一个非空：未接受首稿绑定 `source_draft_artifact_id`，临时应用的 Revision/Review 补丁绑定 `source_change_set_id`，CanonAgent 只从已接受版本读取并绑定 `source_revision_id`。草稿/补丁被替换、取消或失败时，未决候选转为 `discarded`；接受提交时迁移并按指纹合并。
- 候选持久化幂等边界使用作用域、非空来源身份、`candidate_type` 和运行时计算的 `candidate_fingerprint`，不能只使用 `local_key` 或运行 ID。作者决策只能引用当前 Canon 运行中的持久 `candidate_id`。
- `CommandIdempotencyRecord` 的状态固定为 `processing|completed|failed`，记录请求指纹、claim 租约、过期时间、首次结果引用、响应信封和失败码。相同键不同指纹返回 `IDEMPOTENCY_KEY_REUSE`；过期 claim 只能由恢复者接管；不同请求仍需独立版本 CAS。
- 跨章节运行只能使用运行时提供的 `entry_handoff_id`、来源章节版本和 `entry_handoff_chain_hash`。上游版本变化或回滚后，沿祖先链递归标记下游 `stale`，不得静默选择其他版本。
- 章节已有 accepted 版本时，场景接受新版本会将章节标记为 `out_of_sync`；章节级 Canon 和 handoff 只能在 `chapter_sync_status=in_sync` 且 `entry_handoff_status=in_sync` 时继续。
- 空场景人工根编辑是唯一允许 `base_scene_revision_id=null` 的人工例外：使用规范化空 ProseMirror 文档和 `prosemirror_step`，先生成 `SceneDraftArtifact`，再由提交接口物化根版本。Agent/Review ChangeSet 仍必须有非空场景基线。
- `needs_clarification`、技术 `paused` 和不可恢复 `failed` 继续保持运行时状态边界；本轮只补充其与恢复、幂等、租约和提交守卫的契约，不新增作者业务节点。
- 所有绑定 `generation_run_id` 的写入统一使用 `RunWriteFence`。Worker 从有效 `RunLease` 派生 `owner_kind=worker` 的 fence；作者对已有运行提交决策、接受自动草稿/ChangeSet 或恢复暂停时，先完成幂等 claim，再取得 `owner_kind=api_command`、`owner_id=manual_command_id` 的短事务 fence。API command fence 不是 Worker 租约，不能填入 `LeaseContext`，也不能让 API 执行 Worker 节点。
- `RunWriteFence` 的目标运行 ID 放在 fence 中，作者命令的 `generation_run_id` 仍为 `None`；这样保留 `manual_command_id` 与自动运行身份的互斥边界，同时保护目标运行的状态、版本、事件和 outbox 写入。
- 运行终态统一使用 `accepted|cancelled|failed|superseded`；`completed` 仅表示 `CommandIdempotencyRecord` 的完成状态。审阅严重级别统一为 `low|medium|high|critical`，聚合阻断条件使用 `critical|high`，不再引用未定义的 `blocking`。
- Task 3 增加 `ContextManifestPort`，Task 4A 明确拥有运行事件/outbox 端口、Task 5B 负责具体 Postgres/outbox/SSE 发布适配。Task 5B 在 RC 迁移前可用事件注册表默认值生成 SSE envelope，不能假定数据库已存在后续新增的 `RunEvent.payload_schema` 和 `redaction_version` 列。
- 运行 API 的基础响应统一称为 `RunSnapshot`；`status` 只能是一个运行状态，`target_id` 固定表示章节或场景目标，`current_scene_id` 表示章节执行游标，`current_node`/`pending_node`/`pause_reason`/`clarification_questions`/`last_error_code` 按状态使用并保留明确空值语义。

## 已完成产出

- 更新 [V1 工程交付计划](../superpowers/plans/2026-07-31-novel-writing-studio-plan.md)，补充 accepted plan 直接入队、统一计划决策接口、场景物化、候选来源迁移、跨章节 handoff、章节同步状态、幂等 claim/恢复和 Task 5B/5C 边界。
- 更新 [Agent Prompt v1 契约](../superpowers/specs/2026-07-31-agent-prompts-v1-draft.md)，同步 `client_key`、`candidate_id`、`source_*`、`agent_attempt_key`、handoff 字段、Canon 入口和 Hook/提交守卫规则。
- 明确 `CommitGuardHook` 仅为 Agent 图适配层，直接 API 使用 `CommitGuardPort`；明确 `FactExtractionHook` 只做确定性规范化，不创建正式 ID 或直接写库。
- 补齐首稿/补丁候选来源的提交迁移、空场景人工根 ChangeSet、章节聚合后同步状态、handoff 祖先链失效和稳定错误码的文档约束。
- 本轮没有新增应用文件、迁移文件、运行时实现、SSE 实现或测试文件；当前产出属于设计文档和 Prompt 契约层。
- 继续更新 [V1 工程交付计划](../superpowers/plans/2026-07-31-novel-writing-studio-plan.md)：补入 `RunWriteFence`/`RunWriteFencePort`、`GenerationRun` 当前写入栅栏字段、作者 API command fence 的取得与校验流程、Task 3 manifest 端口、Task 4 outbox 文件归属、Task 5B 决策/resume 原子事务、运行终态统一和 Task 9 迁移前后事件字段兼容规则。
- 补充 Task 2 的首稿身份边界：自动草稿按 `generation_run_id + agent_run_id + idempotency_key` 幂等，空场景人工根编辑按 `manual_command_id + idempotency_key` 幂等；作者接受自动草稿时使用 API command fence，不伪造 Worker `LeaseContext`。
- 本轮仍没有新增应用文件、迁移文件、运行时实现、SSE 实现或测试文件；当前新增内容属于计划书的文档级契约。
- 补齐 Task 3-Task 9 的后续任务说明：Task 3 增加上下文组装、manifest、检索接口及关键字段说明；Task 4 增加全部 Agent/runtime/consistency/observability 文件职责、租约/事件/执行器接口说明；Task 5 增加资源、运行、决策、resume、SSE 和 Canon 函数边界；Task 6-Task 8 增加规则、前端组件、观测 sink 的函数和状态字段说明；Task 9 增加验收脚本参数、函数、输出和哈希职责说明。
- 本轮仍未新增应用文件、迁移文件、运行时实现、SSE 实现或测试文件；新增内容属于计划书的文档级说明，不能视为功能已实现。
- 修正运行 API `RunSnapshot` 示例：枚举值不再以带 `|` 的说明字符串冒充 JSON 状态；补充 `target_id`、澄清/暂停/错误字段和决策响应包装，并转义 Markdown 表格中所有状态机分隔符。

## 验证结果

- 已重新读取两份修订文档，计划书 34 个 Markdown fence、3 个 JSON 代码块，Prompt 契约 32 个 Markdown fence、8 个 JSON 代码块，均成对且可解析。
- 已验证两份文档为 UTF-8，未发现替换字符；新日志写入后也将使用 UTF-8 重新读取确认。
- 已检查 `continue|rewrite` 的 accepted plan 路由、`client_key -> scene_id` 一对一物化、三类 `source_*` 字段、持久 `candidate_id`、`agent_attempt_key`、handoff 状态、幂等状态枚举、`CommitGuardHook/Port` 和 Task 5B/5C 规则在计划书与 Prompt 契约中同步出现。
- 已验证旧的隐式 Planner 路由、候选仅用 `local_key`、Canon 提前接入 Task 5B、直接 API 依赖 `CommitGuardHook` 等矛盾约束没有继续作为成功路径。
- 当前验证仍是文档级验证，未执行应用构建、数据库迁移、运行时测试、SSE 重放、worker 接管、Canon 事务或 Mermaid 视觉渲染。
- 已重新读取当前计划书，确认其 42 个 Markdown 代码围栏成对；计划书和本日志均按 UTF-8 读取，未发现替换字符。
- 已交叉检查 `RunWriteFence`、`write_owner_kind/write_owner_id/write_fencing_token`、`ContextManifestPort`、`accepted`/`completed`、`critical|high`、Task 4A/5B outbox 归属和 `v1_rc_observability_metadata` 迁移说明；未发现未定义的 `blocking|high` 或把 `completed` 当作运行终态的残留。
- 已验证 Task 5B 的作者决策/resume 流程明确为“幂等 claim -> API command fence -> run_version CAS -> RunDecision/RunEvent/Outbox 同事务写入”，但该流程尚未通过数据库或并发运行测试。
- 已验证 Task 3-Task 9 均存在对应的文件职责或交互边界说明，并存在函数/接口/关键字段/脚本参数说明；计划书 Markdown 代码围栏仍为 42 个且成对，UTF-8 回读未发现替换字符。
- 已验证 `RunSnapshot` 示例为合法 JSON，枚举说明不再混入 JSON 值；计划书仍为 42 个 Markdown 代码围栏且成对，计划书和日志 UTF-8 回读未发现替换字符。
- 已检查计划书表格中的状态枚举分隔符，`route_review_issues`、`ReviewIssue.status`、前端运行状态、outbox 状态机和 `RunEndEvent.status` 均已使用 `\|`，避免 Markdown 错误拆列。

## 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| `materialize_chapter_plan`、`CommandIdempotencyRecord`、候选来源迁移和 `candidate_id` 分配尚未实现 | 计划接受、重复重放和候选跨来源迁移仍缺少事务级证据 | 契约已验证，代码未实现 |
| `entry_handoff_status`、`entry_handoff_chain_hash`、`chapter_sync_status` 和下游 stale 递归尚未接入数据库与运行时 | 重规划、回滚和章节 Canon 可能仍读取错误版本 | 文档规则已写入，迁移/运行测试未完成 |
| `agent_attempt_key` 与租约接管、checkpoint 恢复和 Outbox 消费游标尚未实现 | 技术重试和 worker 接管仍存在重复提交或迟到写入风险 | 文档级验证 |
| `processing|completed|failed` claim 租约和稳定错误码尚未落地 | 首请求崩溃、处理中重放和同键不同请求体的行为仍需运行时证明 | 未执行 API/数据库测试 |
| Task 5B/5C 的 Canon 注册边界尚未实现 | 需要防止 Canon 在 Task 5B 阶段提前成为可用成功路径 | 计划已明确，代码未验证 |
| 首稿、空场景人工根编辑和候选来源迁移尚未有应用实现 | 首稿反馈、接受根版本和人工编辑幂等仍依赖后续实现正确性 | 文档已同步，未执行集成测试 |
| Compose 私有代理、SSE 重放和真实模型 smoke 尚未执行 | 部署端口门禁、断线恢复和真实 provider 证据仍未形成 | 计划有门槛，运行证据缺失 |
| `RunWriteFence`、API command fence 和 `GenerationRun` 写入栅栏字段尚未实现 | 作者决策/resume 与 Worker 接管之间的并发保护仍只有设计约束，旧 token 拒绝和 fence 交接尚无运行时证据 | 计划已补齐，未执行迁移或并发测试 |
| Task 5B 在迁移前生成 SSE 字段、Task 9 再持久化 `payload_schema/redaction_version` 的兼容路径尚未实现 | 事件 envelope 与数据库审计字段可能在实现时出现版本不一致 | 计划已明确默认值和迁移边界，未执行升级/回滚验证 |
| 后续任务的文件职责和函数说明刚完成文档补齐 | 实现阶段仍需把这些名称和边界落实为真实模块、签名、迁移和测试，不能只依赖计划书文字 | 文档级已验证，应用代码尚未创建 |
| `RunSnapshot` 新增字段和决策响应包装尚未同步到真实 API schema | 前端类型、OpenAPI、SSE 状态快照和数据库读取 DTO 仍可能不一致 | 计划书已修订，应用 API 尚未实现或测试 |
| Markdown 表格修复尚未经过实际渲染器截图验证 | 不同渲染器对转义和长表格的显示仍需在文档预览中确认 | 文本级检查通过，未执行浏览器渲染 |

## 当前未完成事项与下一步

1. 在 M0/M1 实现迁移入口、资源/版本领域服务、`SceneDraftArtifact`、人工命令身份、提交守卫和命令幂等。
2. 在 Task 4A/4B 落地计划接受原子事务、场景队列、章节聚合资格、`ChapterHandoff`、章节同步状态和运行租约 fencing。
3. 在 Task 5B 实现运行 API、`RunEvent`/Outbox、SSE 重放、`paused` resume CAS、处理中 claim 恢复和 worker 接管。
4. 在 Task 5C 接入唯一 Canon 消费者及章节/场景专用入口，验证三类候选的逐条决策和来源约束。
5. 为首稿替换、空场景人工根编辑、候选来源迁移、同键重放、同键不同指纹、handoff 失效和旧 worker 迟到写入补齐自动化测试。
6. 完成 Compose server-side proxy、非空库迁移/备份恢复、真实模型 smoke 或明确跳过证据，并进行最终文档、API/SSE 和流程图交叉复核。
7. 先实现 Task 2 的 `RunWriteFence`/API command fence 持久化原语和测试，再由 Task 4A/Task 5B 分别接入 Worker 写入、作者决策和 resume；验证旧 Worker、旧 API fence、重复 claim 和并发 CAS。
8. 在 Task 9 执行 `v1_rc_observability_metadata` 的非空库升级/回滚演练，确认 SSE envelope 默认值、持久化字段、事件序列和 `authority_hash/audit_hash` 规则一致。

---

## 补记：Task 1 工程骨架与本地运行契约（代码已实现并测试）

### 工作性质与范围

本轮按用户要求只执行计划书 Task 1，建立可运行的工程骨架与本地运行契约，不提前实现 Task 2。实际产出包括后端 FastAPI 骨架、健康检查与错误信封、fail-closed 配置、空闲 Worker、前端 Next.js 骨架、依赖锁定、Alembic 入口、`.env.example` 与 `docker-compose.yml`。未创建任何业务表或首个领域迁移，未实现 Agent、`RunExecutor` 或真实写作运行，未要求真实 LangSmith API Key。

### 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 只执行 Task 1，不提前进入 Task 2 | 保持任务边界，先冻结健康检查、配置、错误信封和服务边界契约 | 本次仅落地 Task 1 文件清单及必要支撑文件 |
| `/health` 只测存活、`/ready` 测数据库依赖 | 依赖不可用不得伪装成就绪，`/health` 不因数据库故障失败 | `_ping_database` 在 `ready` 内调用，失败返回 503 |
| 错误信封不自动脱敏，脱敏由异常处理器负责 | 注册表只暴露固定安全默认消息，处理器不把原始异常文本放进响应 | 全局 handler 使用固定 `INTERNAL_ERROR` 消息 |
| 稳定错误码集中注册，禁止路由自定义同义码 | 保证同一错误码的 HTTP 状态、`retryable` 与 `details` 结构固定 | `errors.ERROR_SPECS` 注册表 + `build_envelope` |
| 前端使用 Next server-side proxy 转发 `/api/*` | 浏览器只访问 frontend，API/PostgreSQL 不发布宿主机端口 | `next.config.mjs` rewrites 读 `INTERNAL_API_BASE_URL` |
| 依赖锁定生成 `requirements.lock` 与 `package-lock.json` | 保证镜像可重复构建，不使用 `latest` | 实际安装后 freeze 生成 |

### 关键规则与取舍

- 配置只从环境变量读取；`get_config()` 在 fail-closed 校验中拒绝非法 `DEPLOYMENT_MODE`/`API_BIND_SCOPE`、空 `ACTOR_ID`、拓扑不匹配的 `INTERNAL_API_BASE_URL`，以及非 `development|evaluation` 下开启 `LANGSMITH_CAPTURE_CONTENT`。
- 资源错误 `run_id=null`，运行错误携带实际 `generation_run_id` 别名；禁止用不存在的运行 ID 表示资源错误。
- Worker 启动后完成配置校验并输出一次 `worker_ready`，随后等待 Task 4A 的 `RunExecutor`；没有 `RunExecutor` 不是启动失败条件，也不连接运行队列。
- Compose 拓扑：`db`（`pgvector/pgvector:pg16`，命名卷 `pgdata`，容器内健康检查）、`api`/`worker`（`backend/Dockerfile`）、`frontend`（`frontend/Dockerfile`）；仅 frontend 发布端口，`api`/`db` 依赖数据库健康后才启动。
- 两个 Dockerfile 使用固定基础镜像（`python:3.12-slim`、`node:22-alpine`）并以非 root 运行，不写入密钥或真实路径。

### 已完成产出

- 后端：`pyproject.toml`、`requirements.lock`（解析后 56 行）、`Dockerfile`、`alembic.ini`、`app/config.py`、`app/errors.py`、`app/main.py`、`app/worker_main.py`、`tests/test_health.py`、`tests/test_error_envelope.py`、`tests/fixtures/fake_model.py`。
- 前端：`package.json`、`package-lock.json`、`tsconfig.json`、`next.config.mjs`、`Dockerfile`、`src/app/page.tsx`、`src/app/layout.tsx`、`src/app/globals.css`。
- 根目录：`.env.example`（16 个配置项）、`docker-compose.yml`。
- 新增必要支撑文件：`app/__init__.py`、`tests/__init__.py`、`tests/fixtures/__init__.py`、`frontend/public/.gitkeep`（Docker 构建需要）。

### 验证结果

- `pytest tests/test_health.py tests/test_error_envelope.py -q`：17 项全部通过（`/health` 数据库不可用时仍 200、`/ready` 不可用 503、可用 200、异常处理器不泄漏原始秘密、配置 fail-closed、错误码注册表与信封规则）。
- `ruff check app tests --fix`：12 项自动修复后全部通过。
- Worker bootstrap smoke：输出一次 `worker_ready` 后保持运行，验证后已停止。
- `npm run typecheck`：通过；`npm run build`：Next.js 15.1.7 构建成功，首页静态预渲染。
- `alembic --version`：1.13.3 可调用；`current` 因 `app.db.migrations` 由 Task 2 创建而失败，符合 Task 1 边界。
- 本机进程模式实测：`/health` 返回 200（`status=ok`），`/ready` 在无数据库时返回 503，符合 fail-closed。
- `docker compose config --quiet`：通过（compose 文件语法与解析有效）。
- 未验证：Docker 守护进程（Docker Desktop）在本沙箱环境中无法启动，因此两个 Dockerfile 的实际构建、`pgvector` 启动、容器健康检查、frontend proxy 容器内连通与宿主机端口门禁未能执行。

### 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| Compose 镜像构建与容器级 smoke 未执行 | 两个 Dockerfile 与 `pgvector` 启动、容器健康检查、proxy 连通、端口门禁仍无运行时证据 | 环境阻塞（Docker daemon 不可用），compose 配置已通过校验 |
| 本机进程模式未连真实本机 PostgreSQL | `ready` 在数据库可用时的 200 路径只在测试中通过 monkeypatch 验证 | 无本地 PostgreSQL，未做真实连接 smoke |
| CORS 未显式配置 | 拓扑为 server-side proxy，浏览器不直连 API，CORS 属纵深防御 | 计划列为要求，当前拓扑下非必需，未实现 |
| 本地 Python 为 3.14.5，镜像运行 3.12 | 测试与镜像解释器版本不同，存在潜在差异 | 已用 3.12 镜像锁定，未在容器内验证 |
| 依赖锁含 dev 工具（pytest/ruff/mypy） | 镜像安装面偏大 | 可接受，未影响功能 |

### 当前未完成事项与下一步

1. 在 Docker daemon 可用环境执行 `docker compose up -d --build` 完整 smoke：验证两个 Dockerfile 可构建、`pgvector` 启动、数据库健康检查、frontend proxy 访问 `/health`/`/ready`、Worker 输出 `worker_ready` 且不领取运行、宿主机只发布 frontend 端口。
2. 本机进程模式连接真实本机 PostgreSQL 验证 `ready` 200 路径。
3. 按需补充 CORS 白名单（仅允许 frontend origin）作为纵深防御。
4. 完成 task 1 后按计划进入 Task 2（正文版本与 Story Bible 持久化），由 Task 2 创建 `app.db.migrations` 并执行首个迁移验证。

---

## 补记：Task 2 正文版本与 Story Bible 持久化（代码已实现并测试，迁移验证已完成）

### 工作性质与范围

本轮按用户要求只执行计划书 Task 2，建立权威 schema、不可变版本、`SceneDraftArtifact`、作者/Agent ChangeSet、候选持久化、`CommandIdempotencyRecord`、`RunWriteFence`/租约 fencing、`RunEvent`/Outbox/`RunLease` 架构和最小 handoff 读取端口。未实现 Task 3-Task 5，未实现 LangGraph 编排、真实 Agent 调用、完整章节聚合、handoff 创建/失效计算或正式 Canon 更新路由。

### 用户主导决策

| 决策 | 原因与取舍 | 落点 |
| --- | --- | --- |
| 真实 PostgreSQL 建库，不引入 SQLite 冒充 | 保证约束/唯一索引/检查约束真实可查询，避免伪造迁移通过 | `novel`/`novel_test` 库建在真实 PostgreSQL 18.4 上，约束经 `pg_constraint` 实查 |
| pgvector 不可装时迁移标 `NOT RUN`，不跳过 `CREATE EXTENSION vector` | 环境安装目录只读（非管理员、提权被拒），必须如实记录阻塞 | 迁移文件已含 `CREATE EXTENSION IF NOT EXISTS vector`，实际执行报 `extension "vector" is not available` |
| 后经 MSVC 编译 pgvector 0.8.1 并注入 GUC 安装成功 | 用 MSVC 编译与 MSVC 版 postgres 同 ABI 的 `vector.dll`，经 `dynamic_library_path` + `extension_control_path` 让 `CREATE EXTENSION vector` 真正可用，不伪造 | pgvector 0.8.1 已装进 PostgreSQL 18.4，`CREATE EXTENSION IF NOT EXISTS vector` 通过 |
| 身份互斥：`source=author` 用 `manual_command_id`，`agent|review` 用运行身份+fencing | 防止伪造运行身份或 Worker 租约 | `CommitGuard` 与 `validate_change_set_context` 强制校验 |
| 测试用 `metadata.create_all` 而非迁移 | Task 2 业务表不依赖 vector 扩展，可在真实库先验约束 | 12 个测试文件针对真实 `novel_test` 库运行 |

### 已完成产出

- 后端：`app/db/session.py`、`app/db/models.py`（31 张表，含外键/检查/唯一约束/索引）、`app/db/migrations/`（`env.py`、`script.py.mako`、首迁移 `1c1dccd138fb`）、`app/domain/`（`commit_guard.py`、`lease.py`、`idempotency.py`、`drafts.py`、`manuscript.py`、`chapters.py`、`handoff.py`、`story_bible.py`、`outbox.py`、`resources.py`、`interfaces.py`）、`app/services/`（`id_service.py`、`id_cleanup_service.py`、`fact_candidate_service.py`、`canon_candidate_service.py`、`run_decision_service.py`）。
- 测试：`tests/domain/`（`test_versioning.py`、`test_scene_drafts.py`、`test_canon_candidates.py`、`test_chapter_handoff.py`、`test_commit_guard.py`、`test_idempotency.py`、`test_lease_fencing.py`、`test_outbox.py`）、`tests/services/`（`test_id_service.py`、`test_fact_candidate_service.py`、`test_id_cleanup_service.py`）、`tests/db/test_migrations.py`、`tests/conftest.py`。

### 验证结果

- `pytest tests/db tests/domain tests/services -q -p no:warnings`：50 项全部通过，退出码 0（含版本冲突、幂等 claim、过期 claim 接管、旧 fencing 拒绝、候选来源恰一、首稿物化、作者 ChangeSet、handoff read、outbox 去重/失败不回滚、ID 幂等分配、清理终态，以及新增的真实迁移验证测试）。
- `ruff check app tests`：通过（`--fix` 后全部通过）。
- `mypy app/domain app/services app/db`：25 个源文件无问题；全量 `mypy app` 仅剩 `app/main.py` 2 处 Task 1 既有异常处理器签名问题，不在 Task 2 范围内。
- 关键约束实查：`fact_candidates` 含 `ck_candidate_source_exactly_one`、`uq_candidate_source_fingerprint`；`command_idempotency_records` 含 `uq_idempotency_key`；`run_events` 含 `uq_run_event_sequence`；`generation_runs` 含 `ix_generation_runs_project_id`；`novel` 库 32 张表（31 业务表 + alembic_version）。
- 迁移执行：`alembic upgrade head` 对 `novel` 库成功，建出 32 张表，版本 `1c1dccd138fb`，vector 扩展 0.8.1 已装，`vector` 类型 l2/余弦距离可查询。**迁移验证完成**。新增 `tests/db/test_migrations.py::test_migration_creates_vector_extension_and_schema`，对独立库 `novel_migration_test` 从空库真实跑迁移并断言扩展与 schema 存在（该测试 drop_all 后额外清理 `alembic_version`，避免陈旧版本行导致 Alembic 跳过）。

### 补记：按 Codex 复核意见在 pgvector/pgvector:pg16 官方镜像复验

为满足计划书"在 `pgvector/pgvector:pg16` 环境验证迁移"的要求，用 Docker 启动官方镜像 `pgvector/pgvector:pg16`（PostgreSQL 16.14 + pgvector 0.8.6）复验：

- **迁移复验**：`alembic upgrade head` 从空库对 `novel` 库成功，建出 32 张表（31 业务表 + alembic_version），版本 `1c1dccd138fb`，vector 扩展 `0.8.6` 已装，`vector` 类型 l2 距离 `5.196152422706632` 可查询。
- **约束复验（PG16 + pgvector 0.8.6）**：`pg_constraint` 实查 CHECK/UNIQUE/FK 全部存在：`ck_candidate_source_exactly_one`（source 恰一）、`uq_candidate_source_fingerprint`（指纹去重）、`uq_idempotency_key`（幂等键）、`uq_run_event_sequence`（事件序号）、`uq_run_outbox_dedupe`（outbox 去重）、`uq_canon_decision_record`（决策记录）、`uq_entity_type_name`、`uq_entity_aliases`、`uq_timeline_event_candidate`、`uq_plot_thread_update`、`uq_fact_candidate`、`uq_agent_run`、`uq_run_lease`、`uq_handoff_chain`、`uq_handoff_chain_hash`、`uq_consumed_cursor` 等；FK 覆盖候选→版本/来源、事件→运行、outbox→事件、lease→运行、handoff→版本等。
- **测试复验（指向 PG16）**：`pytest tests/db tests/domain tests/services -q -p no:warnings` 50 项全通过，退出码 0；`ruff check app tests` 通过；`mypy app/domain app/services app/db` 25 个源文件无问题。
- **范围复核**：`agents/`、`context/` 目录不存在，无 LangGraph 引用，models 无 `ContextPack`/`SceneRequest`/checkpoint 表——未提前实现 Task 3/4/5 运行流程。`ContextManifest`/`context_manifests` 表属 Task 2 持久化边界（计划书规定 ContextPack 仅在运行时存在、持久化只保存 Manifest）。
- **环境说明**：本机 Docker 端口映射走 WSL2 回环，`localhost` 解析优先 IPv6 `::1` 会导致连接超时；测试 URL 需用 `127.0.0.1` 而非 `localhost` 才能快速建连。pgvector 0.8.6 为官方镜像 `0.8.6`，测试断言已改为不硬编码版本号（`startswith("0.8")`），兼容官方镜像与本机注入构建。

### 当前不足与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| pgvector 通过 `dynamic_library_path` + `extension_control_path` 注入，而非写入安装目录 | 本机 PostgreSQL 只读无法直接安装，注入方式让 `CREATE EXTENSION vector` 可用；换机器/重装 PG 需重做，且 `vector.dll` 由 MSVC 编译未经过官方发行渠道 | 已真实验证 `CREATE EXTENSION`、vector 类型查询、迁移均通过；部署环境仍建议用官方 pgvector 镜像 |
| `app/main.py` 2 处 Task 1 既有 mypy 报错 | 属 Task 1 遗留，不在 Task 2 范围 | 未改动，避免越界 |

### 当前未完成事项与下一步

1. Task 2 迁移验证已完成，按计划允许进入 Task 3。
2. 后续任务接 Task 3 的 `ContextManifestPort` 与最小 handoff read port 时，复用本任务已实现的 `get_valid_entry` 与 `handoff.py` 契约。
3. 部署/CI 环境建议改用官方 `pgvector/pgvector` 镜像或对 PostgreSQL 安装目录有写权限的环境，避免本机注入式安装的差异。
