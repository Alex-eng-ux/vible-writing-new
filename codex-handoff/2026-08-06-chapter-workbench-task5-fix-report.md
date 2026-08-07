# Chapter Workbench Task 5 顺序修复报告

## 修复范围

本次只修复章节 Worker/Graph 的阶段顺序：所有 accepted plan 场景完成后，创建
`request_type=review`、`decision_target=chapter` 的章节运行；该运行按“章节聚合 ->
ChapterReviewAgent -> 等待作者接受”执行。普通 `new_chapter` 规划运行仍保持原有
Planner -> Review -> Aggregator 行为。

## TDD 证据

### RED

```text
backend/.venv/Scripts/python.exe -m pytest tests/runtime/test_chapter_workflow_task4.py::test_worker_enqueues_chapter_review_after_all_planned_scenes_are_accepted -q
1 failed
E       assert 0 == 1
```

失败原因是 Worker 在最后一个场景 accepted 后没有创建章节级运行。

新增 Graph 顺序测试后，旧实现同样按 Planner 开始并触发：

```text
AssertionError: chapter review run must not invoke planner
```

### GREEN

```text
backend/.venv/Scripts/python.exe -m pytest tests/runtime/test_chapter_workflow_task4.py::test_worker_enqueues_chapter_review_after_all_planned_scenes_are_accepted -q
1 passed

backend/.venv/Scripts/python.exe -m pytest tests/agents/test_chapter_graph.py::test_chapter_review_run_aggregates_before_review -q
1 passed

 backend/.venv/Scripts/python.exe -m pytest tests/runtime/test_chapter_workflow_task4.py -q
12 passed

backend/.venv/Scripts/python.exe -m pytest tests/domain/test_chapter_workflow_task5.py -q
5 passed

backend/.venv/Scripts/python.exe -m pytest tests/agents/test_chapter_graph.py -q
8 passed

backend/.venv/Scripts/python.exe -m ruff check app/runtime/run_worker.py app/agents/chapter_graph.py tests/agents/test_chapter_graph.py
All checks passed!

backend/.venv/Scripts/python.exe -m compileall -q app tests
passed
```

测试数据库 fixture 不能并行启动；并行运行时曾出现 PostgreSQL 表创建竞争，改为
串行运行后上述结果稳定通过。

## 代码变更

- `backend/app/runtime/run_worker.py`
  - 增加计划场景完成检查：每个场景同时具备 accepted revision 和 accepted scene run。
  - 增加章节审校运行的幂等创建，写入 `run_queued` 事件与 outbox。
  - 场景未完成时不创建章节审校运行。
- `backend/app/agents/chapter_graph.py`
  - 对 `review/chapter` 运行从聚合节点进入。
  - 聚合成功后进入 ChapterReviewAgent；审校完成后结束本次执行，交由 Worker 持久化为
    `waiting_feedback`，等待章节接受决策。
  - 保持普通规划运行的既有路径不变。
- `backend/tests/agents/test_chapter_graph.py`
  - 增加聚合先于审校、且不调用 Planner 的回归测试。
- `backend/tests/runtime/test_chapter_workflow_task4.py`
  - 增加 Worker 领取章节审校运行后持久化 staged revision 和 review 摘要的集成回归。
- 修正章节审校迁移文件的 import 顺序，确保全量 Ruff 检查无遗留错误。

## 当前限制

Playwright 仍受环境 `spawn EPERM` 限制，未宣称浏览器 E2E 通过。本修复没有扩大到前端
或 Task 6 Canon 流程。
