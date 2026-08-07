# Task 5 旧章节路由修复报告

## 根因

`ChapterGraph` 的 `_PLAN` 与 `_REVIEW` 条件边以及 `_route()` 仍保留 Planner -> Review -> Aggregator 的旧静态路径。即使当前 `new_chapter + ready` 分支会提前暂停，未来状态恢复或直接调用仍可能绕过 accepted plan 场景队列。`AgentResultRouter` 也继续向章节下游返回旧节点名，无法把路由责任收回 workflow controller。

## 改动

- 普通规划图的 `_PLAN` 仅允许进入作者暂停或结束，不再转入 `_REVIEW`。
- 章节审校运行（`request_type=review`、`run_scope=chapter`、`decision_target=chapter`）从 `_AGGREGATE` 进入 `_REVIEW`，审校完成后结束；聚合失败/资格不足仍进入作者暂停。
- 规划候选接受后由 workflow controller 接管场景队列，ChapterGraph 不再重复执行 Planner。
- `AgentResultRouter` 对 `chapter_planner`/`chapter_review` 仅返回完成状态，不暴露章节下游节点。
- `ChapterGraph.step()` 对 Planner、Review、Aggregator 校验运行范围、章节身份、`request_type` 与 `decision_target`；不匹配时抛 `COMMAND_CONTEXT_MISMATCH`，其中普通 `new_chapter` 直接调用章节审校明确拒绝。
- 更新受影响图链路测试，真实 Provider 测试改为验证 Aggregate -> Review 入口。

## 测试命令与结果

```text
backend/.venv/Scripts/python.exe -m pytest -q backend/tests/agents/test_chapter_graph.py backend/tests/agents/test_real_chapter_chain.py backend/tests/runtime/test_chapter_workflow_worker_task2.py backend/tests/runtime/test_chapter_workflow_task4.py
14 passed, 1 skipped

backend/.venv/Scripts/python.exe -m ruff check backend/app backend/tests
All checks passed!

backend/.venv/Scripts/python.exe -m compileall -q backend/app backend/tests
通过（无输出）
```

## 剩余风险

图的 `_PAUSE` 条件映射仍保留审校/聚合节点作为合法章节审校暂停恢复目标，但 `_route_after_pause()` 会按运行上下文拒绝普通规划运行的这些目标；该映射是 LangGraph 条件边声明所需的候选集合，不构成普通规划主流程可达路径。未运行依赖外部 PostgreSQL 的完整端到端测试。
