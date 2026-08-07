# Task 6A 独立审查报告

## Spec Compliance

- `RunWorker.tick()` 在 queued 运行处理前调用章节接受 outbox 消费器；合法的 `chapter_revision.accepted` 事件会调用 `handle_chapter_accepted_outbox()`，成功后在同一事务中标记 `consumed`。
- Canon 创建的 `(chapter_id, accepted_revision_id)` 幂等仍由服务层 advisory lock + 已有运行查询保证，Worker 重复 tick/重启不会创建第二个 Canon run。
- 服务异常不会在当前事务中提交 `consumed` 标记，因此数据库中的 outbox 状态保持可重放；但异常会继续冒泡到 `tick()`。

## Strengths

- `backend/app/runtime/run_worker.py:433-464` 的消费边界清晰，成功创建或复用 Canon 后才写 `consumed`，并清理旧错误。
- `backend/app/services/canon_runs.py:531-563` 将并发串行化与幂等判断放在服务层，避免仅依赖 Worker 本地查重。
- `backend/tests/runtime/test_chapter_workflow_task6.py:40-94` 覆盖了真实 Worker 首次消费、`waiting_feedback`、outbox consumed、第二次 tick 不重复创建。
- `backend/tests/api/test_canon_api.py -k chapter_accepted_event_auto_enqueue_and_idempotent_consume` 通过，说明 API 侧事件入队与消费契约未回归。

## Issues

### Important

1. **服务异常会终止 Worker 轮询**

   位置：`backend/app/runtime/run_worker.py:94-110`、`433-464`。

   `handle_chapter_accepted_outbox()` 抛出异常时，`_consume_chapter_accepted_outbox()` 没有逐记录隔离或外层恢复，异常直接离开 `tick()`；`run_forever()` 也没有捕获该异常。因此 outbox 数据库状态通常保持可重放，但 Worker 进程会退出，后续事件不会自动重试。报告中已记录该风险；建议后续将失败隔离到单条记录并保留 retry/backoff，或由 Worker 顶层捕获并告警后继续轮询。

2. **accepted payload 校验不足，未绑定 outbox 身份**

   位置：`backend/app/runtime/run_worker.py:438-462`。

   当前只校验 `event_type` 以及两个字段是否 truthy，没有校验 `payload_schema`，也没有校验 `accepted_chapter_revision_id == record.resource_id`，或 payload 中章节/修订是否与 outbox 资源一致。只要 payload 字段非空，Worker 就可能把记录标记为 `consumed` 或把事件路由到另一合法章节；服务层部分校验不能替代消息自身的身份绑定。应补齐校验并增加错误 payload 不消费的回归测试。

### Minor

3. **存在不可达的重复旧实现**

   位置：`backend/app/runtime/run_worker.py:363-429`。

   `continue`（约 362 行）之后仍保留一整段旧的场景物化循环，因此该段永远不会执行。它增加维护成本并掩盖真正的 accepted-plan 路径，建议后续删除并保留 `_ensure_next_scene_run()` 单一路径。

4. **异常重放与非法 payload 测试覆盖不足**

   位置：`backend/tests/runtime/test_chapter_workflow_task6.py:40-94`。

   当前只有成功消费和第二次 tick 的幂等断言，没有测试 handler 抛异常后 outbox 状态保持 pending/published 并可再次消费，也没有测试缺字段、错误 schema、resource_id 不一致时不创建 Canon run。该缺口使上述 Important 问题缺少自动回归保护。

## Verification

```text
backend/.venv/Scripts/python.exe -m pytest -q backend/tests/runtime/test_chapter_workflow_task6.py
1 passed

backend/.venv/Scripts/python.exe -m pytest -q backend/tests/api/test_canon_api.py -k chapter_accepted_event_auto_enqueue_and_idempotent_consume
1 passed

backend/.venv/Scripts/python.exe -m ruff check backend/app/runtime/run_worker.py backend/app/services/canon_runs.py backend/tests/runtime/test_chapter_workflow_task6.py
All checks passed!

backend/.venv/Scripts/python.exe -m compileall -q backend/app/runtime/run_worker.py backend/app/services/canon_runs.py backend/tests/runtime/test_chapter_workflow_task6.py
通过（无输出）
```

## Verdict

**有条件通过（Conditional Pass）**。核心成功链路、服务层幂等和重复 tick 行为符合设计；在补齐 payload 身份校验、Worker 异常隔离/重试以及对应回归测试前，不建议将该消费者视为生产级完整落地。

## Task Quality

实现方向正确，改动范围基本集中，TDD 成功链路证据充分；但异常控制面、消息契约校验与死代码清理尚未完成，整体质量为 **中上，未达到无保留通过**。
