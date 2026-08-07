# Chapter Workbench Task 5 实施报告

## Status

已完成章节聚合、章节审校结果持久化、章节版本接受/回滚、版本历史与 workflow/API 读取接线。未创建 Git commit，保留给主 agent 统一验收。

## TDD 证据

RED：

```text
backend/.venv/Scripts/python.exe -m pytest tests/domain/test_chapter_workflow_task5.py -q
5 failed
```

失败分别暴露了空 `scene_id`、未校验 accepted scene 基线、提交未阻断场景基线变化、回滚未复制场景映射以及审校结果没有持久化。

GREEN：

```text
backend/.venv/Scripts/python.exe -m pytest tests/domain/test_chapter_workflow_task5.py tests/agents/test_chapter_graph.py tests/agents/test_chapter_agents.py -q
18 passed

backend/.venv/Scripts/python.exe -m pytest tests/domain/test_chapter_orchestration.py tests/api/test_chapter_handoff.py tests/api/test_chapter_workflow_api.py tests/runtime/test_chapter_workflow_task4.py tests/runtime/test_chapter_workflow_worker_task2.py -q
36 passed

backend/.venv/Scripts/python.exe -m pytest tests/services tests/api -q
79 passed

backend/.venv/Scripts/python.exe -m ruff check app/domain/chapters.py app/db/models.py app/api/chapters.py app/api/schemas.py app/agents/chapter_graph.py app/agents/state.py app/runtime/run_worker.py tests/domain/test_chapter_workflow_task5.py
All checks passed

backend/.venv/Scripts/python.exe -m compileall -q app tests
passed
```

## 变更文件

- `backend/app/domain/chapters.py`：accepted plan/scene 映射校验、固定场景版本、提交 CAS 与 stale 检查、幂等 outbox、回滚复制、审校结果持久化。
- `backend/app/db/models.py`：`ChapterRevision.review_issues`、`review_summary`、`review_run_id`。
- `backend/app/db/migrations/versions/b2c3d4e5f6a7_add_chapter_review_fields.py`：对应数据库迁移。
- `backend/app/agents/chapter_graph.py`、`backend/app/agents/state.py`：审校输出与 staged revision id 在图状态中传递；场景未完成时不调用章节审校 Agent。
- `backend/app/runtime/run_worker.py`：接入 `ChapterAggregator`，在 Worker 事务中持久化章节审校结果。
- `backend/app/api/chapters.py`、`backend/app/api/schemas.py`：版本历史/详情返回固定场景映射、审校摘要和当前 accepted 指针。
- `backend/tests/domain/test_chapter_workflow_task5.py`：Task 5 RED/GREEN 回归测试。

## 已知风险

- 浏览器 Playwright 仍受环境 `spawn EPERM` 限制，本任务未宣称浏览器 E2E 通过。
- 当前章节审校结果以 JSONB 快照保存，尚未拆成独立的审校 issue 表；读取契约已稳定，后续可在不改变 API 的情况下迁移。
- Worker 仍按下一次 tick 异步推进场景队列和章节审校，接受接口不会同步等待整个 Worker 链路。
