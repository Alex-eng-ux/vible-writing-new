# Task 9 V1 权威与审计哈希规范（authority-hash-spec.md）

本文件定义 V1 工程验收中权威状态哈希（`authority_hash`）与审计状态哈希（`audit_hash`）的语义、表归属、规范化规则与比较约定，是备份恢复、fixture 重置与变更影响断言的一致基线。实现见 `backend/app/acceptance/hashes.py`。

## 1. 两种哈希的含义

### 1.1 authority_hash（权威状态哈希）

表征**作品权威结构状态**：作品层级结构、已接受的正文版本及其指针、正式 Story Bible（实体、正典事实、时间线事件、情节线程、伏笔）、已接受章节 handoff 与正式 Canon 决策结果所依赖的结构链接。

任何修改若改变了权威结构的规范化表示（例如新增/修改/删除作品层级行、已接受正文版本或指针、正式 Story Bible 行、handoff 行、结构链接行），`authority_hash` 必须变化；反之，仅影响候选、临时或派生数据的变化不得改变 `authority_hash`。

### 1.2 audit_hash（审计状态哈希）

表征**作者决策与运行审计轨迹**：作者决策记录、作者反馈、候选审计（事实候选、时间线事件候选、情节线程更新）、正式 Canon 决策记录、`RunEvent` 事件流与运行审计元数据（generation runs、agent runs）。

任何审计轨迹的追加或修改（例如新的决策、新的反馈、新的事件行、运行状态变化）都会使 `audit_hash` 变化；`audit_hash` 变化的方向必须与真实发生的事件序列一致，不允许出现审计记录被覆盖或丢失后哈希保持不变的情形。

## 2. 表归属

以下表清单与 `hashes.py` 中的常量一致（新增/删除表时必须同步更新 `hashes.py` 与本文件）。

### 2.1 权威表（AUTHORITY_TABLES）

`novel_projects`、`volumes`、`chapters`、`scenes`、`scene_revisions`、`chapter_plan_revisions`、`chapter_plan_revision_links`、`chapter_revisions`、`chapter_revision_scenes`、`chapter_handoffs`、`entities`、`canon_facts`、`timeline_events`、`plot_threads`、`foreshadowings`。

### 2.2 审计表（AUDIT_TABLES）

`run_decisions`、`author_feedbacks`、`fact_candidates`、`timeline_event_candidates`、`plot_thread_updates`、`canon_decision_records`、`run_events`、`generation_runs`、`agent_runs`。

### 2.3 排除表（EXCLUDED_TABLES）

`context_manifests`、`scene_snapshots`、`chapter_snapshots`、`run_outbox_records`、`run_leases`、`command_idempotency_records`、`run_event_consumer_cursors`。

## 3. 规范化规则

两种哈希共享同一套规范化规则：

- **算法**：SHA-256（十六进制摘要）。
- **编码**：UTF-8。
- **稳定 JSON**：每行序列化为 JSON 对象，键按字典序（`sort_keys=True`）；表名与记录之间以 NUL（`\x00`）分隔后依次喂入摘要。
- **记录排序**：同一表内所有行先整体规范化为 JSON 字符串列表，再按字符串字典序排序后参与哈希（不依赖数据库物理行序或随机主键序）。
- **时间规范化**：所有时间值统一为 UTC ISO-8601 文本；naive datetime 视为 UTC 后输出 `Z` 后缀（`+00:00` 归一为 `Z`）。
- **null 保留**：`NULL` 与缺失键语义不同，规范化时保留 `null`，不丢弃、不填充默认值。

## 4. 排除项说明

以下内容**不参与**两种哈希（不在库内或属派生/临时数据），核对哈希时不得把它们当作差异来源：

- **派生摘要与向量**：上下文清单（`context_manifests`）、场景快照（`scene_snapshots`）、章节快照（`chapter_snapshots`）——均为可由权威数据重算的派生物。
- **ContextPack / checkpoint**：基于上述快照构建的上下文包与检查点数据（若落库也属于派生数据），不在哈希范围。
- **LangSmith Trace**：外部追踪系统数据，不在本库内。
- **临时 outbox 投递状态**：`run_outbox_records` 仅表示待投递/已投递的临时状态，恢复后可能重建，不参与哈希。
- **运行期基础设施状态**：`run_leases`（租约）、`command_idempotency_records`（幂等键）、`run_event_consumer_cursors`（消费游标）——均为运行期控制数据，随进程/恢复重建，不参与哈希。

若某行同时出现在权威表/审计表与上述派生逻辑中（如快照引用了已接受正文），以权威表/审计表的行内容为准参与哈希，快照本身仍排除。

## 5. clean fixture 与 fixture hash

- clean 重置（`reset_v1_fixture.ps1 -Mode clean`）从空库创建三章六场景 fixture。fixture 清单中的业务标识（如 chapter/scene 的 `local_key`）是稳定的，但**正式 ID 由数据库随机生成**。
- 因此 clean 后使用 `compute_fixture_hash(fixture)`（对 `v1-fixture.json` 解析结果做稳定 JSON 的 SHA-256）生成**独立 fixture hash**，用于确认 fixture 内容未被篡改；**不得**把 fixture hash 与基于正式 ID 的 `authority_hash` / `audit_hash` 直接比较——两者口径不同（一个基于 fixture 清单、一个基于库内行）。
- **restore 必须保留正式 ID**：备份恢复（`backup/restore`）后库内正式 ID、版本指针、事件序列必须与备份时一致，恢复流程不得通过重新执行 fixture 生成新 ID 来“重建”数据。

## 6. 备份恢复比较约定

- 备份时使用 `snapshot_hashes()` 记录 `authority_hash` 与 `audit_hash` 双哈希；恢复后重新计算并与备份值比较。
- **必须同时比较两种哈希**：只比较其中之一不能发现另一侧的损坏（例如正文指针被改但事件流看似完整，或审计事件缺失但正文看似一致）。
- 双哈希一致即认为权威与审计状态均已还原；不一致时必须定位差异表并说明原因，禁止在哈希不一致的情况下继续后续验收。
