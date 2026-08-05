# 交接：执行 Task 3 Context Pack 与检索边界

- 发起方：Codex
- 日期：2026-08-03
- 优先级：高
- 状态：已完成（代码已实现并测试，允许进入 Task 4A）

## 背景

Task 2 已通过 Codex 二次复核，允许进入 Task 3。Task 3 负责为一次运行读取、筛选、组装和登记上下文来源，不负责执行 Agent 或写入权威正文/Canon。

相关文件：

- 计划书：`docs/superpowers/plans/2026-07-31-novel-writing-studio-plan.md`
- Task 2 交接和复核结果：`codex-handoff/inbox-2026-08-03-task2-start.md`
- Agent 契约：`docs/superpowers/specs/2026-07-31-agent-prompts-v1-draft.md`
- 项目规则：`AGENTS.md`

## 要办的事

只执行计划书 Task 3：

1. 先读取 Task 3 全部内容、Task 2 的 `ChapterHandoffReadPort`、`ContextManifest` 持久化边界和现有测试。
2. 创建计划书列出的文件：
   - `backend/app/context/models.py`
   - `backend/app/context/composer.py`
   - `backend/app/context/manifest.py`
   - `backend/app/context/retrievers.py`
   - `backend/tests/context/test_composer.py`
   - `backend/tests/context/test_manifest.py`
   - `backend/tests/context/test_retrievers.py`
   - `backend/tests/context/test_context_contracts.py`
3. 定义 `SceneRequest`、`ContextItem`、`ContextPack`、`ContextManifest`、manifest entry 和检索端口；字段、枚举和错误码必须与计划书一致。
4. 实现固定优先级：`P0` 场景契约/已接受基线/硬规则，`P1` 有效章节 handoff，`P2` 已接受 Canon/时间线/剧情线，`P3` 相邻已接受场景和实体，`P4` 文风摘要及 pgvector 补充片段。
5. 实现确定性预算和排序：保留所有 P0 必需项；必需项超预算返回 `CONTEXT_BUDGET_EXCEEDED`；可选项截断必须记录 `truncation_reason` 和 `omitted_source_ids`；`items` 按计划规定的稳定键排序。
6. 实现当前 `generation_run_id` 绑定的 Manifest：同一请求指纹、来源顺序、版本映射和 handoff 时复用 `manifest_id`；跨运行、基线变化、handoff 链哈希变化或请求指纹变化必须拒绝并返回 `CONTEXT_MANIFEST_MISMATCH`。
7. 跨章节读取只能通过 Task 2 的 `ChapterHandoffReadPort`，只能读取已接受、`entry_handoff_status=in_sync` 且祖先链哈希匹配的 handoff；首章允许为空，不能读取上一章“当前最新版本”替代 handoff。
8. 元数据检索必须先执行项目/章节/场景/版本/故事时间/实体范围过滤；pgvector 只能在允许的 `source_id` 白名单内补充，不能扩大到整本作品。
9. 向量服务不可用时只跳过 P4 补充并记录降级原因；必需元数据来源不可用返回 `CONTEXT_SOURCE_UNAVAILABLE`；不得生成 embedding，不调用模型，不写入正文版本、候选或 Canon。
10. 按 TDD 先写并观察失败测试，再实现最小逻辑。Fake retriever 可用于端口契约测试，但不能用它掩盖来源范围、版本和 handoff 校验。

## 冲突 / 边界

- 不创建 `GenerationRun`，不执行 Agent，不创建 `agents/`、`runtime/` 或 LangGraph 图。
- 不生成 embedding，不调用真实 LLM，不写 `CanonFact`、`SceneRevision`、`ChapterRevision` 或任何候选。
- 不重新定义 Task 2 的持久化来源语义；运行时 `ContextManifest` DTO 与持久化 Manifest 必须保持字段含义一致，必要时使用适配器，不建立第二套互斥规则。
- 不把 staged、草稿、候选或派生摘要标记为正式 Canon 来源。
- 不修改 Task 1、Task 2 的领域语义和数据库迁移，除非发现明确的契约阻塞并先报告。

## 验收

- `pytest backend/tests/context -q` 全部通过。
- 覆盖固定优先级、必需项超预算、可选项截断、稳定排序、无相关结果和向量降级。
- 覆盖 Manifest 幂等、同运行复用、跨运行拒绝、请求指纹冲突、版本映射冲突和 handoff 链哈希冲突。
- 覆盖首章空入口、顺序承接、上一章回滚后的失效入口和来源不可用错误。
- `ruff check backend/app/context backend/tests/context` 通过。
- `mypy backend/app/context` 通过，或记录实际等价命令和退出码。
- 更新当天开发日志，明确 Task 3 已实现、已验证和仍未验证的部分。
- 交接状态只有在测试和边界复核完成后才能写成“已完成”。

## 完成后填写

- 状态：已完成（Task 3 代码已实现并测试，边界复核通过，允许进入 Task 4A）
- 实际修改文件：
  - `backend/app/context/__init__.py`、`models.py`、`composer.py`、`manifest.py`、`retrievers.py`
  - `backend/tests/context/__init__.py`、`test_composer.py`、`test_manifest.py`、`test_retrievers.py`、`test_context_contracts.py`
- 执行命令：
  - `pytest backend/tests/context -q -p no:warnings`（退出码 0）
  - `pytest backend/tests/db backend/tests/domain backend/tests/services backend/tests/context -q -p no:warnings`（退出码 0，80 项全过，无回归）
  - `ruff check backend/app backend/tests`（通过）
  - `mypy backend/app/context backend/app/domain backend/app/services backend/app/db`（30 个源文件无问题）
- 测试数量和退出码：context 30 项全过；db/domain/services 50 项全过；合计 80 项，退出码 0
- 边界复核结果：
  - `app/` 下仅 `context/db/domain/services`，无 `agents/`、`runtime/` 目录，无 LangGraph 引用
  - `app/context/` 内无 `session.add/commit/delete`，无 `CanonFact/SceneRevision/ChapterRevision` 写入，无 embedding/langgraph 调用
  - 只读组装：不建 GenerationRun、不执行 Agent、不写正文/候选/Canon
  - 跨章节读取仅经 Task 2 `get_valid_entry`（只读 active + in_sync + chain hash 匹配），首章允许空
  - 向量服务不可用时 P4 降级为空并保留元数据，不硬失败
- 未完成或环境阻塞：无。context 仅定义 `ContextManifestPort` 契约与 `create_or_reuse/get_manifest/validate_replay` 实现；未提前实现 Task 4 的 embedding 或运行流程
- 是否允许进入 Task 4A：是

### Codex 复核通过记录

- 复核时间：2026-08-04
- 复核结论：Codex 复核通过。Task 3 实现范围与验证证据覆盖计划要求，manifest 复用、跨运行拒绝、handoff 校验、稳定排序和向量降级均有记录；未创建 Agent/runtime，未执行模型、embedding 或权威数据写入。
- 文档状态修正：本文件顶部状态已由“待处理”改为“已完成（代码已实现并测试，允许进入 Task 4A）”，与“完成后填写”保持一致，状态冲突已消除。
- 放行：Task 3 验收通过，允许进入 Task 4A。Task 4A 必须从单场景图和运行端口开始，不得一次性实现 4B 章节聚合或 4C Canon 路由。

---

## Codex 复核意见（2026-08-04）

从交接记录看，Task 3 的实现范围和验证证据已覆盖计划要求：

- Context 契约、composer、manifest 和 retriever 文件已建立；
- context 测试 30 项通过，叠加 Task 2 回归测试共 80 项通过；
- ruff 和目标范围 mypy 通过；
- 未创建 Agent/runtime，未执行模型、embedding 或权威数据写入；
- Manifest 复用、跨运行拒绝、handoff 校验、稳定排序和向量降级均有记录。

当前唯一需要修正的是本文件顶部的状态仍为“待处理”，与“完成后填写”中的“已完成”冲突。

### TRAE 需要补做

1. 将本文件顶部状态改为：`已完成（代码已实现并测试，允许进入 Task 4A）`。
2. 在完成记录中补上“Codex 复核已通过”的时间和结论。
3. 不修改 Task 3 的业务实现，不提前创建 `agents/` 或 `runtime/` 文件。

### 放行规则

完成上述文档状态修正后，Task 3 验收通过，允许进入 Task 4A。Task 4A 必须从单场景图和运行端口开始，不得一次性实现 4B 章节聚合或 4C Canon 路由。
