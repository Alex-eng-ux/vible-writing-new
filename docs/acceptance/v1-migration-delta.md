# Task 9 V1 迁移差异说明（v1-migration-delta.md）

本文件记录 Task 9 V1-RC 观测元数据迁移的差异与回滚策略，供验收对照。迁移实现见 `backend/app/db/migrations/versions/v1_rc_observability_metadata_add_run_event_audit_fields.py`。

## 1. 迁移链位置

| 项目 | 值 |
| --- | --- |
| 上游 head（迁移前最新） | `f8a9b0c1d2e3`（Task 5C：add GenerationRun.canon_source_revision_id） |
| 本迁移 ID | `v1_rc_observability_metadata` |
| 上游依赖 | `f8a9b0c1d2e3` |
| 迁移后 head | `v1_rc_observability_metadata` |

执行 `alembic upgrade head` 后，数据库版本号必须为 `v1_rc_observability_metadata`；验收时以 `alembic current` 的输出为准。

## 2. 字段差异

在 `run_events` 表新增两个非空审计字段（均为 VARCHAR(64)）：

| 字段 | 类型 | 约束 | 服务端默认值 | 语义 |
| --- | --- | --- | --- | --- |
| `payload_schema` | VARCHAR(64) | NOT NULL | `'run-event.v1'` | 事件负载的 schema 标识，作为事件审计来源 |
| `redaction_version` | VARCHAR(64) | NOT NULL | `'redaction.v1'` | 脱敏规则版本标识，作为脱敏审计来源 |

说明：

- 两个字段均为**服务端默认值 + 非空**，既有事件行在升级时自动以默认值回填，无需数据迁移脚本。
- 字段内容仅作审计/观测用途，**不改变** `event_id`、`sequence` 与 `payload` 的既有语义；事件序列（`sequence`）仍是事件流的唯一有序标识。

## 3. 升级 / 降级影响

### 3.1 升级影响（upgrade）

- 既有 `run_events` 行：`payload_schema` 回填为 `'run-event.v1'`、`redaction_version` 回填为 `'redaction.v1'`。
- 新增事件行：由模型/服务端默认值写入与 `RunEventEnvelope` 默认值相同的标识。
- 行为不变：事件写入、消费、SSE 按序重放逻辑不受影响。

### 3.2 迁移前 SSE 信封兼容性

- Task 5B 的 SSE 信封 `RunEventEnvelope` 在迁移前已携带同样的默认值（`payload_schema="run-event.v1"`、`redaction_version="redaction.v1"`），但**不假定数据库列存在**——旧版本代码不得直接查询这两个列。
- 迁移后，以**持久化字段**（`run_events.payload_schema` / `redaction_version`）作为审计来源；SSE 信封字段与持久化字段应保持一致，差异视为审计不一致。

### 3.3 降级影响（downgrade）

- 回滚到 `f8a9b0c1d2e3` 时删除这两个列，`event_id`、`sequence`、`payload` 及事件序列**不丢失**；仅丢失新增的观测元数据字段。
- 降级后旧版本代码可继续按原逻辑运行。

## 4. 回滚策略

- 失败迁移演练：升级过程中任一步失败，应先执行 `alembic downgrade -1`（回到 `f8a9b0c1d2e3`）并确认事件数据完整，再修复后重新 `upgrade`；若库已不可用，用备份恢复并比较双哈希。
- `downgrade` 先删 `redaction_version` 再删 `payload_schema`，两步均为纯列删除，无数据依赖，可安全回滚。
- 任何回滚/恢复操作后必须验证 `run_events` 的 `event_id`/`sequence`/`payload` 与操作前一致（对比事件序列与双哈希）。

## 5. 验证查询

迁移成功后执行以下查询，确认两列已存在且既有行已回填默认值、序列完整：

```sql
SELECT event_id, sequence, payload_schema, redaction_version
FROM run_events
ORDER BY sequence;
```

预期断言：

- 查询成功，无列不存在错误；
- 每条既有行的 `payload_schema = 'run-event.v1'`、`redaction_version = 'redaction.v1'`；
- `sequence` 无空洞、无重复（与迁移前备份的事件序列一致）。
