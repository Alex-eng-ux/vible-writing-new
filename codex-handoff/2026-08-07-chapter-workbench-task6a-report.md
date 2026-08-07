# Task 6A：章节接受事件接入 Worker

## 状态

已完成。`RunWorker.tick()` 现在会消费 `chapter_revision.accepted` outbox，调用已有的
`handle_chapter_accepted_outbox()`，并在同一事务中把成功消费的记录标记为 `consumed`。

## TDD 证据

### RED

先运行回归测试：

```text
backend/.venv/Scripts/python.exe -m pytest -q tests/runtime/test_chapter_workflow_task6.py
```

结果：1 failed。`worker.tick()` 返回 `0`，说明 accepted outbox 尚未接入 Worker。

### GREEN

加入 Worker 消费路径后重新运行同一测试：

```text
backend/.venv/Scripts/python.exe -m pytest -q tests/runtime/test_chapter_workflow_task6.py
```

结果：1 passed。测试确认章节接受后自动创建一个章节 Canon run、运行进入
`waiting_feedback`、outbox 变为 `consumed`，第二次 `tick()` 不会重复创建运行。

## 改动文件

- `backend/app/runtime/run_worker.py`
  - 在 `tick()` 中接入章节接受 outbox 消费。
  - 新增 `_consume_chapter_accepted_outbox()`，筛选
    `resource_type="chapter_revision"` 与 `event_type="chapter_revision.accepted"`。
  - 成功调用已有 Canon 消费者后标记 `consumed`；无效 payload 标记 `failed`。
  - Canon 运行的 `(chapter_id, accepted_revision_id)` 幂等与 advisory lock 继续由已有服务负责。
- `backend/tests/runtime/test_chapter_workflow_task6.py`
  - 保留并清理 Worker 集成回归测试的未使用导入。

## 验证结果

- `tests/runtime/test_chapter_workflow_task6.py`：1 passed。
- `tests/api/test_canon_api.py -k chapter_accepted_event_auto_enqueue_and_idempotent_consume`：1 passed。
- `tests/agents/test_canon_graph.py`：5 passed。
- `tests/runtime/test_chapter_workflow_task4.py`：12 passed。
- `ruff check app/runtime/run_worker.py tests/runtime/test_chapter_workflow_task6.py`：通过。
- `compileall app/runtime/run_worker.py`：通过。

## 当前关注点

复核修复前，`handle_chapter_accepted_outbox()` 抛出的服务异常会让当前消费事务回滚并冒出 `tick()`；现已通过 savepoint 隔离，业务 handler 异常会保留可重放状态并继续处理其他事件。savepoint 外的数据库/连接级异常仍需由统一 Worker 外层处理。
## Task 6A 复核修复

### 新增 RED 回归

- `test_worker_isolates_chapter_consumer_failure_and_keeps_event_replayable`：handler 异常原先直接冒出 `tick()`。
- `test_worker_rejects_chapter_acceptance_outbox_metadata_mismatch`：原先错误 schema 和错误 resource id 仍会调用 Canon handler。

两条测试在修复前均失败（`2 failed`）。

### 修复与 GREEN

- 每条章节接受事件使用 `session.begin_nested()` savepoint；handler 异常只回滚当前事件，保留原 delivery status 和错误信息，继续处理后续记录。
- 消费前校验 `payload_schema == "canon-auto.v1"`，并要求 payload 的 `accepted_chapter_revision_id` 严格等于 `RunOutboxRecord.resource_id`；不匹配标记 `failed`，不调用 Canon handler。

修复后：

```text
backend/.venv/Scripts/python.exe -m pytest -q tests/runtime/test_chapter_workflow_task6.py
```

结果：3 passed。

复核验证：

- `tests/api/test_canon_api.py -k chapter_accepted_event_auto_enqueue_and_idempotent_consume`：1 passed。
- `tests/agents/test_canon_graph.py tests/runtime/test_chapter_workflow_task4.py`：17 passed。
- Ruff 与 compileall：通过。

复核后的剩余风险仅限于 savepoint 外的数据库/连接级异常；业务 handler 异常已不会终止 Worker。
