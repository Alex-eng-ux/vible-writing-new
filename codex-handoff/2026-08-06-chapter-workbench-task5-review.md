# Chapter Workbench Task 5 独立复核报告

复核范围：章节场景队列完成后的章节审校运行、ChapterGraph 顺序、new_chapter 规划分支以及 Worker 的审校结果持久化。复核只读代码并运行测试，未修改生产代码。

## 结论

当前变更已经实现并通过测试验证：最后一个计划场景同时具备 accepted 场景版本和 accepted 场景运行后，Worker 会幂等创建 `request_type=review`、`decision_target=chapter` 的章节运行；该运行入口是 `ChapterAggregator -> ChapterReviewAgent`，审校完成后由 Worker 持久化审校结果并进入等待作者决策的状态。

但仍有一个重要的结构性风险：ChapterGraph 和 Router 仍保留 `Planner -> Review -> Aggregator` 的旧静态边/默认路由。当前 `new_chapter + ready` 分支靠 `_node()` 的特殊条件提前暂停，因此本次运行没有实际穿过旧路径；不过图的公开 `step()`/未来状态变化仍可触发该边，和设计 6.2 要求主流程只从 accepted plan 的场景队列进入聚合不一致。建议在 Task 5 合入前删除或隔离该旧边，而不是依赖输出状态守卫。

## Critical

无。没有发现已被最小测试证明会绕过 accepted plan、提前聚合或重复创建章节审校运行的 Critical 缺陷。

## Important

### I-1：旧 Planner -> Review -> Aggregator 图边仍然存在

- 文件/行号：`backend/app/agents/chapter_graph.py:95-108`、`backend/app/agents/chapter_graph.py:310-321`；默认下一节点仍见 `backend/app/agents/result_router.py:46-49`。
- 证据：`_PLAN` 的条件边仍允许返回 `_REVIEW`；`_route()` 在 `last_durable_node == _PLAN` 时直接返回 `_REVIEW`。虽然 `_node()` 在 `new_chapter` 且 Planner 输出 `ready` 时于 `163-181` 提前暂停，当前新增测试因此通过，但图结构本身仍暴露旧路径，`ChapterGraph.step(..., "chapter_review")` 也仍可直接执行审校节点。
- 影响：设计 6.2 要求 accepted plan 后按有序场景队列聚合，再进行章节审校；保留旧边会让未来新增 Planner 状态、恢复逻辑或直接图调用重新绕过场景队列。
- 阻塞：是设计级阻塞，建议在 Task 5 合入前改成规划运行只产出候选并暂停，章节审校图仅接受 `request_type=review`/`decision_target=chapter` 入口。

### I-2：Worker 级 aggregate -> review -> waiting_feedback 持久化缺少端到端回归

- 文件/行号：`backend/app/runtime/run_worker.py:490-521`、`backend/app/agents/chapter_graph.py:248-295`。
- 证据：现有 `test_chapter_review_run_aggregates_before_review` 只直接调用图并验证 `calls == ["aggregate", "review"]`；`test_worker_enqueues_chapter_review_after_all_planned_scenes_are_accepted` 只验证 queued 运行创建及幂等性，没有让默认 Worker 执行该 review 运行并断言 staged revision 的 `review_issues/review_summary/review_run_id` 和 `waiting_feedback`。
- 影响：`staged_chapter_revision_id` 与 `chapter_review_output` 是否在真实 Worker checkpoint/事务中同时到达持久化函数，当前没有集成证据；后续改动可能使审校结果丢失而测试仍通过。
- 阻塞：否（当前实现路径静态检查和图单测均符合），但应补一条默认 Worker + fake provider 的端到端回归后再宣称 Task 5 完成。

## Minor

### M-1：`_consume_plan_outbox()` 中存在不可达旧实现

- 文件/行号：`backend/app/runtime/run_worker.py:357-360` 后的 `361-425`。
- 证据：调用 `_ensure_next_scene_run()` 后立即 `continue`，因此 `361-425` 的旧场景循环永远不会执行。
- 影响：重复逻辑增加维护成本，容易造成未来修复只改到不可达分支，掩盖实际队列行为。
- 阻塞：否；建议在后续清理中删除不可达代码并保留单一队列恢复实现。

### M-2：迁移文件 Ruff 基线错误未清理

- 文件/行号：`backend/app/db/migrations/versions/b2c3d4e5f6a7_add_chapter_review_fields.py:6-10`。
- 证据：`ruff check` 报 `I001 import block is un-sorted or un-formatted`。
- 影响：全量 Ruff 仍非绿色，虽然与本次顺序逻辑无关。
- 阻塞：否（局部 Task 5 代码 Ruff 通过），但应在合入前修正。

## 已验证命令

- `backend/.venv/Scripts/python.exe -m pytest -q tests/agents/test_chapter_graph.py tests/runtime/test_chapter_workflow_task4.py`：19 passed。
- `backend/.venv/Scripts/python.exe -m pytest -q tests/domain/test_chapter_workflow_task5.py tests/api/test_chapter_workflow_api.py tests/runtime/test_chapter_workflow_worker_task2.py`：9 passed。
- `backend/.venv/Scripts/python.exe -m ruff check app/agents/chapter_graph.py app/runtime/run_worker.py tests/agents/test_chapter_graph.py tests/runtime/test_chapter_workflow_task4.py app/db/migrations/versions/b2c3d4e5f6a7_add_chapter_review_fields.py`：失败，唯一错误为迁移文件 `I001`。
- `backend/.venv/Scripts/python.exe -m pytest -q tests/runtime/test_real_chapter_worker_chain.py tests/api/test_run_lifecycle.py`：前者通过；后者有 1 个环境错误，测试 fixture 连接的 PostgreSQL 缺少 `run_outbox_records` 表，非本次代码断言失败。
## 复核更新（2026-08-07）

- 已串行重跑 `tests/runtime/test_chapter_workflow_task4.py`，其中 `test_worker_persists_staged_chapter_review_output_before_acceptance` 已覆盖真实 Worker 事务：章节 review run 执行后为 `waiting_feedback`，生成 staged `ChapterRevision`，并持久化 `review_run_id` 与 `review_summary`。
- 已串行重跑 `tests/runtime/test_chapter_workflow_worker_task2.py` 和 `tests/runtime/test_real_chapter_worker_chain.py`，分别为 `3 passed`、`3 passed, 1 skipped`；`new_chapter` 规划停在 `chapter_planner`，未调用章节审校。
- 已执行 `backend/.venv/Scripts/python.exe -m ruff check app tests`，结果为 `All checks passed!`；迁移文件的 I001 未能复现，M-2 关闭。
- 旧 `Planner -> Review -> Aggregator` 静态兼容路径仍保留给非 `new_chapter` 的旧 chapter run；当前主流程由 `new_chapter` 的暂停守卫和独立 `review/chapter` 入口阻断，未发现它在本次主流程中可达。该路径属于后续架构收敛项，不阻塞 Task 5 本轮验收。
