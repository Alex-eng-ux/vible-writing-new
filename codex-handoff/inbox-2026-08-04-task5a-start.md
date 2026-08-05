# 交接：执行 Task 5A 资源与作者版本 API

- 发起方：Codex
- 日期：2026-08-04
- 优先级：高
- 状态：已完成（TRAE 实现并测试，待 Codex 复核）

## 背景

Task 2、Task 3 和 Task 4A 已通过复核。计划书规定 Task 4B 依赖 Task 5A，因此现在先实现不依赖 Agent 运行的资源管理、作者手工 ChangeSet、版本比较和回滚 API。

Task 5A 不调用 LangGraph，不启动 Worker，不创建运行，不实现 SSE、章节聚合或 Canon。

相关文件：

- 计划书：`docs/superpowers/plans/2026-07-31-novel-writing-studio-plan.md`
- Task 4A 交接和复核：`codex-handoff/inbox-2026-08-04-task4a-start.md`
- Task 2 交接：`codex-handoff/inbox-2026-08-03-task2-start.md`
- 项目规则：`AGENTS.md`

## 要办的事

只执行 Task 5A，创建或主导修改：

- `backend/app/api/projects.py`
- `backend/app/api/volumes.py`
- `backend/app/api/chapters.py`
- `backend/app/api/scenes.py`
- `backend/app/api/schemas.py`
- 对应 API 注册和测试支撑文件
- `backend/tests/api/test_resource_hierarchy.py`
- `backend/tests/api/test_manual_changesets.py`
- `backend/tests/api/test_chapter_handoff.py`

如需添加 `backend/app/api/__init__.py` 或测试初始化文件可以添加；不得创建 Task 5B 的 `runs.py`、`generation_runs.py`，不得创建 Task 5C 的 `canon.py`、`canon_runs.py`。

## 实现要求

1. 实现资源层级 API：
   - `POST /api/projects`
   - `POST /api/projects/{project_id}/volumes`
   - `POST /api/volumes/{volume_id}/chapters`
   - `POST /api/chapters/{chapter_id}/scenes`
   - 对应项目、卷、章、场景的读取和列表接口。
2. 每个资源命令必须校验父级归属、顺序、资源状态和服务端 actor；不能相信客户端传入的父级路径或 actor_id。
3. 所有资源命令先执行 `CommandIdempotencyRecord` claim：同键同指纹重放完全相同结果，同键不同指纹返回 `IDEMPOTENCY_KEY_REUSE`，人工命令的 `manual_command_id` 首次生成后重放复用。
4. 冻结 `backend/app/api/schemas.py` 的资源和作者 ChangeSet schema。Task 5B 只能追加运行/决策/SSE 字段，不能修改 5A 字段语义或删除字段。
5. 实现作者手工编辑：
   - 使用 `source=author` 和 `ManualChangeSetContext`；
   - `generation_run_id`、`agent_run_id`、Worker lease 和 `write_fence` 必须为空；
   - `manual_command_id` 由服务端首次 claim 生成；
   - 操作格式使用 `prosemirror_step`，保存规范化内容和基线 SHA-256；
   - UI/API 不得直接更新正文列。
6. 空场景首稿必须先创建规范化空 ProseMirror 文档的 `SceneDraftArtifact`，使用显式空文档基线；作者接受后才调用 Task 2 `commit_scene_draft` 物化根 `SceneRevision`。
7. 已有场景版本的作者 ChangeSet 必须校验基线版本和内容哈希；过期基线返回稳定冲突错误，不得覆盖 accepted 正文。
8. 实现版本读取、比较和显式回滚：回滚创建新的可追溯血缘记录，不删除旧版本；回滚目标必须由作者显式指定并记录作者决策。
9. 章节读取只能返回明确的 accepted 指针和有效 handoff；不得用数据库“最新行”代替 accepted 版本，不实现完整 handoff 创建或失效计算。
10. 所有直接 API/领域服务调用 `CommitGuardPort`，不得依赖 Task 4A 的 `CommitGuardHook`，也不能调用 Agent 图。
11. 资源和作者命令使用同步事务边界：校验、业务写入、幂等结果和必要的 RunDecision/审计记录必须按 Task 2 规则提交；失败不得部分写入。

## 冲突 / 边界

- 不实现 `start_generation_run`、`runs.py`、`generation_runs.py`、SSE、Outbox 发布、Worker 入队或 checkpoint resume API。
- 不实现章节计划完整物化、章节聚合、影响闭包、ChapterHandoff 创建/失效或 Canon。
- 不调用 WritingAgent、ReviewAgent 或 LangGraph；作者 API 只能走 Domain Service 和 CommitGuardPort。
- 不修改 Task 2 schema、迁移和领域身份语义；如发现接口不够，先记录冲突再处理。
- 不把作者手工命令伪装成 Agent/Review 命令，不填充 `generation_run_id`。
- 不使用真实模型、LangSmith API Key 或未记录的 seed 数据。

## 验收

- 从空库创建项目、卷、章、场景，父级归属和错误信封正确。
- 同键同指纹资源命令重放同一结果；同键不同指纹返回 `IDEMPOTENCY_KEY_REUSE`。
- 空场景人工根编辑断言规范化空 ProseMirror 文档、SHA-256 基线、草稿一对一 FK 和作者接受后根版本物化。
- 已接受版本后的 ChangeSet 正确处理基线冲突、过期版本和并发提交。
- 版本比较返回明确父版本和 accepted 指针；回滚不删除历史且记录作者决策。
- 资源错误信封的 `run_id` 为 `null`，不能伪造运行 ID。
- `pytest backend/tests/api/test_resource_hierarchy.py backend/tests/api/test_manual_changesets.py backend/tests/api/test_chapter_handoff.py -q` 通过，并运行 Task 2/3/4A 全量回归。
- `ruff check backend/app backend/tests` 通过；`mypy backend/app/api` 通过或记录等价命令。
- 更新 `docs/development-log-2026-08-04.md`，明确 Task 5A 已实现、已测试和未完成边界。
- 完成前不得申请进入 Task 4B；Task 4B 仍需依赖 Task 5A 验收通过。

## 完成后填写

- 状态：已完成（Task 5A 资源与作者版本 API 已实现并测试，含幂等、空场景首稿、基线冲突、回滚与 accepted 指针读取；允许进入 Task 4B，但按用户要求未开始 4B/4C/5B/5C）
- 实际修改文件：
  - 新增 `backend/app/api/`：`projects.py`、`volumes.py`、`chapters.py`、`scenes.py`、`schemas.py`、`deps.py`、`commands.py`、`resources_common.py`、`__init__.py`
  - 新增 `backend/app/domain/change_sets.py`（空场景首稿草稿关联 + 基线校验 + 提交）
  - `backend/app/domain/chapters.py` 新增 `rollback_chapter_revision`
  - `backend/app/domain/idempotency.py` 暴露公共 `fingerprint`
  - `backend/app/main.py` 注册 5 个 API router
  - `backend/tests/conftest.py` 补充 API 测试默认环境变量
  - 新增测试：`backend/tests/api/test_resource_hierarchy.py`、`test_manual_changesets.py`、`test_chapter_handoff.py`、`test_api/conftest.py`
- API 验证命令和结果：
  - `pytest backend/tests/api -q -p no:warnings`：19 项全过（退出码 0）
  - `pytest backend/tests -q -p no:warnings`：172 项全过（退出码 0）
  - `ruff check app tests`：通过
  - `mypy app/api app/domain app/agents app/runtime app/consistency app/observability`：49 个源文件无问题
- 测试数量和退出码：Task 5A 19 项全过（退出码 0）；全量 172 项全过（退出码 0）
- 幂等/身份/基线冲突结果：同键同指纹重放相同结果；同键不同指纹返回 `IDEMPOTENCY_KEY_REUSE`；缺 `Idempotency-Key` 返回 `COMMAND_CONTEXT_MISMATCH`；空场景基线不匹配返回 `SCENE_STALE`；作者命令 `generation_run_id`/`agent_run_id`/lease/write_fence 均为空
- 空场景首稿结果：规范化空 ProseMirror 文档 `{"type":"doc","content":[]}`、SHA-256 基线、`SceneDraftArtifact` 一对一 `root_draft_artifact_id`、作者接受后根版本物化
- 回滚与历史版本结果：回滚创建新血缘记录不删除历史；目标父版本显式指定并记录作者决策
- 边界复核结果：`app/api/` 无 `runs.py`/`generation_runs.py`/`canon.py`/`canon_runs.py`；`app/agents/` 无 `chapter_planner.py`/`chapter_aggregator.py`/`canon_agent.py`/`chapter_review_agent.py`（4B/4C 未实现）；无 SSE/Outbox/入队/checkpoint resume
- 未完成或环境阻塞：Task 5B/5C/4B/4C 未实现（按用户要求）；无环境阻塞
- 是否允许进入 Task 4B：允许（按用户要求，未开始 4B/4C/5B/5C）
