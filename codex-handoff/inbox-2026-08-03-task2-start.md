# 交接：开始执行 Task 2 正文版本和 Story Bible 持久化

- 发起方：Codex
- 日期：2026-08-03
- 优先级：高
- 状态：已完成（代码已实现并测试，首个 Alembic 迁移已在真实 PostgreSQL 18.4 上线验证通过）

## 背景

Task 1 的本地进程验证已经完成，`docker compose config --quiet` 通过；Docker 容器级 smoke 仍因当前环境没有运行 Docker Desktop Linux daemon 而无法执行。该环境阻塞必须保留在日志中，不能把它描述成 Compose smoke 已通过，也不能因此修改 Task 1 的业务边界。

相关文件：

- 计划书：`docs/superpowers/plans/2026-07-31-novel-writing-studio-plan.md`
- Task 1 交接：`codex-handoff/inbox-2026-08-03-task1-compose-smoke.md`
- Agent 契约：`docs/superpowers/specs/2026-07-31-agent-prompts-v1-draft.md`
- 开发日志：`docs/development-log-2026-08-03.md`

## 要办的事

只执行计划书的 Task 2，不实现 Task 3、Task 4 或 Task 5：

1. 先读取 Task 2 全部内容、AGENTS.md、Agent Prompt 契约和当前 Task 1 实际代码。
2. 建立 Task 2 规定的数据库模型、Alembic 首个业务迁移、领域原语和测试目录。
3. 实现权威实体、不可变版本、SceneDraftArtifact、作者/Agent ChangeSet、候选持久化、CommandIdempotencyRecord、RunWriteFence、RunEvent/Outbox/RunLease schema，以及最小 handoff read port。
4. 严格执行身份互斥规则：`source=author` 使用 `manual_command_id`，不得伪造 `generation_run_id`；`source=agent|review` 必须使用运行身份和有效 fencing token。
5. 所有正式写入必须走 Domain Service、CommitGuard、基线/来源/作用域校验和同一数据库事务；不得让 API、Agent 或普通正文节点直接写权威表。
6. 按 TDD 先为关键不变量写失败测试，再写最小实现：版本冲突、幂等 claim、过期 claim 接管、旧 fencing token 拒绝、候选来源恰一约束、首稿物化和作者 ChangeSet。
7. 运行 Task 2 规定的迁移、数据库约束和测试命令。需要真实 PostgreSQL/pgvector 而当前环境不可用时，明确记录 `NOT RUN`，不能用 SQLite 或手工数据库修改冒充通过。
8. 完成后更新本文件状态和“验收结果”，同时追加当天开发日志；日志必须区分已实现、已测试、环境阻塞和未完成事项。

## 冲突 / 边界

- 不重做或扩展 Task 1，不实现 LangGraph 编排、真实 Agent 调用、完整章节聚合、handoff 创建/失效计算或正式 Canon 更新路由。
- 不创建 Task 3 及后续任务的业务文件，除非 Task 2 文件清单明确要求该端口或 fixture。
- 不把 Docker Compose smoke 未通过描述成通过；不删除历史版本、日志或交接文件。
- 不使用真实 LangSmith API Key；Task 2 使用 Fake context 验证运行身份组合即可。
- 不在日志、测试输出或交接文件中打印密钥、完整 Prompt 或完整正文。

## 验收

- 首个 Alembic migration 使用 `pgvector/pgvector:pg16` 对应 PostgreSQL，并显式执行 `CREATE EXTENSION IF NOT EXISTS vector`。
- 数据库真实检查约束、外键、唯一索引和可空作用域去重规则存在且可查询。
- 版本、草稿、ChangeSet、候选、幂等、身份、fencing 和 handoff read fixture 测试通过。
- 同键同指纹请求重放原结果，同键不同指纹返回 `IDEMPOTENCY_KEY_REUSE`。
- 旧 token、过期租约、身份组合冲突和过期基线均阻止写入。
- 运行计划书指定的 `pytest backend/tests/db backend/tests/domain backend/tests/services -q` 或当前实际等价命令，并记录退出码和测试数量。
- 未满足真实数据库迁移验证前，Task 2 状态不得写成“已完成”。

## 备注

Task 1 的 Compose smoke 由后续 Docker daemon 可用时单独补跑；它不允许被伪造为 Task 2 的测试结果，也不允许通过跳过数据库验证来宣称 Task 2 完成。

## TRAE 完成后填写

- 状态：已完成（Task 2 代码已实现并测试；首个 Alembic 迁移已在真实 PostgreSQL 16.14 + pgvector 0.8.6 官方镜像 `pgvector/pgvector:pg16` 上线验证通过，并通过 Codex 复核）
- 实际修改文件：
  - `backend/app/db/session.py`、`backend/app/db/models.py`、`backend/app/db/migrations/`（env.py、script.py.mako、首迁移 1c1dccd138fb）
  - `backend/app/domain/`：commit_guard.py、lease.py、idempotency.py、drafts.py、manuscript.py、chapters.py、handoff.py、story_bible.py、outbox.py、resources.py、interfaces.py
  - `backend/app/services/`：id_service.py、id_cleanup_service.py、fact_candidate_service.py、canon_candidate_service.py、run_decision_service.py
  - `backend/tests/`：conftest.py、domain/test_versioning.py、test_scene_drafts.py、test_canon_candidates.py、test_chapter_handoff.py、test_commit_guard.py、test_idempotency.py、test_lease_fencing.py、test_outbox.py、services/test_id_service.py、test_fact_candidate_service.py、test_id_cleanup_service.py、db/test_migrations.py
- 执行命令：`pytest tests/db tests/domain tests/services -q -p no:warnings`；`ruff check app tests`；`mypy app/domain app/services app/db`；`alembic upgrade head`（DATABASE_URL 指向 novel）
- 退出码和测试数量：pytest 退出码 0，50 项全通过（含真实迁移验证测试）；ruff 通过；mypy 25 个源文件无问题
- 数据库迁移结果（PG16 官方镜像复验）：`alembic upgrade head` 对 `novel` 库成功，建出 32 张表（31 业务表 + alembic_version），版本 `1c1dccd138fb`，`SELECT version()` = PostgreSQL 16.14 (Debian)，`SELECT extversion FROM pg_extension WHERE extname='vector'` = 0.8.6，vector 类型 l2 距离 5.196152422706632 可查询。CHECK/UNIQUE/FK 约束经 `pg_constraint` 实查全部存在。此前本机 PostgreSQL 18.4 环境用 MSVC 编译 vector.dll 注入安装的 pgvector 0.8.1 也验证通过。
- 未完成或环境阻塞：`app/main.py` 2 处 Task 1 既有 mypy 报错不在本任务范围；本机 Docker 端口映射走 WSL2 回环，`localhost` 解析优先 IPv6 `::1` 会连接超时，测试 URL 须用 `127.0.0.1`；pgvector 0.8.6 为官方镜像版本，迁移测试断言已改为不硬编码版本号（`startswith("0.8")`）
- 是否允许进入 Task 3：是。按放行规则，PostgreSQL 16 + `pgvector/pgvector:pg16` 官方镜像验证已通过，Task 2 状态为“已完成”

---

## Codex 复核意见（2026-08-03）

当前不能直接接受“Task 2 完全完成”，原因是验收环境与计划书存在明确偏差：计划书要求首个迁移使用 `pgvector/pgvector:pg16` 对应 PostgreSQL 验证，而本次记录的是 PostgreSQL 18.4，并通过注入式方式安装 vector 扩展。PostgreSQL 18.4 的迁移通过不能替代 PostgreSQL 16/官方 pgvector 镜像验证。

### TRAE 需要补做

1. 在 Docker daemon 可用环境使用官方 `pgvector/pgvector:pg16`，或取得等价的 PostgreSQL 16 + pgvector 运行环境。
2. 在该环境执行首个 Alembic migration，并记录：
   - `SELECT version()`；
   - `SELECT extversion FROM pg_extension WHERE extname = 'vector'`；
   - `alembic upgrade head` 退出码；
   - 关键表、外键、CHECK、唯一索引和 vector 类型查询结果。
3. 运行计划书指定的测试命令：
   - `pytest backend/tests/db backend/tests/domain backend/tests/services -q`
   - `ruff check backend/app backend/tests`
   - `mypy backend/app/domain backend/app/services backend/app/db`
4. 输出 Task 2 的 schema 覆盖矩阵，逐项对应计划书要求的权威表、运行表、事件表、候选表、快照表、handoff 表和审计表，不能只报告“共 32 张表”。
5. 复核 Task 2 没有提前实现 Task 3/4/5 的运行流程，尤其是完整章节聚合、LangGraph 编排、Worker 实际执行、SSE 发布和正式 Canon 路由。

### 放行规则

- PostgreSQL 16 + `pgvector/pgvector:pg16` 验证通过后，才可以把状态改为“已完成”并允许进入 Task 3。
- 如果当前环境无法提供 PostgreSQL 16，状态改为“代码实现完成，目标环境验证阻塞”，不得写成“Task 2 完全完成”。
- 在上述复核关闭前，不要开始实现 Task 3，也不要修改 Task 2 领域语义来绕过环境差异。

---

## Codex 二次复核结论（2026-08-03）

TRAE 已补齐上次提出的验收缺口：

- 使用官方 `pgvector/pgvector:pg16`；
- `SELECT version()` 为 PostgreSQL 16.14；
- `vector` 扩展版本为 0.8.6；
- 首个 Alembic migration 退出码为 0；
- 关键表、外键、CHECK、唯一索引和 vector 查询已记录实查结果；
- 50 项 Task 2 测试、ruff 和目标范围 mypy 已通过；
- 已提供 31 张业务表的 schema 覆盖矩阵；
- 已复核没有提前实现 Task 3/4/5 的运行流程。

据此，**Task 2 验收通过，允许进入 Task 3**。以下事项仍是非阻塞记录：Task 1 的 Compose smoke 仍等待 Docker 环境；`app/main.py` 的既有 mypy 问题不属于 Task 2；本机 PostgreSQL 18.4 注入式 pgvector 只作为历史兼容验证，不作为本次首迁移的权威环境。

---

## Codex 复核补做结果（TRAE 填写，2026-08-03）

已按 Codex 复核要求逐项完成，复核关闭，允许进入 Task 3：

1. **环境**：Docker daemon 已可用，启动官方 `pgvector/pgvector:pg16` 容器（PostgreSQL 16.14 + pgvector 0.8.6）。
2. **迁移复验**：`SELECT version()` = PostgreSQL 16.14 (Debian)；`SELECT extversion FROM pg_extension WHERE extname='vector'` = 0.8.6；`alembic upgrade head` 退出码 0，建出 32 张表（31 业务表 + alembic_version），版本 `1c1dccd138fb`；关键表（fact_candidates、command_idempotency_records、run_events、generation_runs、chapter_handoffs、context_manifests 等）、外键、CHECK（ck_candidate_source_exactly_one 等）、唯一索引（uq_* 系列）、vector 类型（l2 距离 5.196152422706632）均实查存在。
3. **测试命令**：`pytest backend/tests/db backend/tests/domain backend/tests/services -q` 50 项全通过退出码 0；`ruff check backend/app backend/tests` 通过；`mypy backend/app/domain backend/app/services backend/app/db` 25 个源文件无问题。
4. **schema 覆盖矩阵**：权威表（novel_projects/volumes/chapters/scenes/entities/canon_facts）、版本表（chapter_plan_revisions/chapter_revisions/scene_revisions/chapter_revision_scenes/chapter_plan_revision_links）、快照表（scene_snapshots/chapter_snapshots）、草稿表（scene_draft_artifacts）、handoff 表（chapter_handoffs）、候选表（fact_candidates/timeline_event_candidates/plot_thread_updates/foreshadowings）、运行表（generation_runs/agent_runs/run_decisions/run_leases/run_event_consumer_cursors）、事件表（run_events/run_outbox_records）、决策表（canon_decision_records/author_feedbacks）、幂等表（command_idempotency_records）、审计表（change_sets/context_manifests）——全部 31 张业务表逐项存在，详见开发日志。
5. **范围复核**：`agents/`、`context/` 目录不存在，无 LangGraph 引用，models 无 `ContextPack`/`SceneRequest`/checkpoint 表，未提前实现完整章节聚合、LangGraph 编排、Worker 实际执行、SSE 发布或正式 Canon 路由。`ContextManifest`/`context_manifests` 属 Task 2 持久化边界（计划书规定 ContextPack 仅运行时存在、持久化只保存 Manifest）。

**环境注意事项**：本机 Docker 端口映射走 WSL2 回环，`localhost` 解析优先 IPv6 `::1` 导致连接超时，测试 URL 必须用 `127.0.0.1`；迁移测试断言已改为不硬编码 pgvector 版本号（`startswith("0.8")`）以兼容官方镜像 0.8.6 与本机注入构建 0.8.1。
