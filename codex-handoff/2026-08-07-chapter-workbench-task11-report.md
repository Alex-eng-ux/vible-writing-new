# Chapter Workbench Task 11 类型校验报告

## RED：原始检查

工作目录：`backend`，虚拟环境：`backend/.venv`。

命令：

```text
python -m mypy app
```

原始结果：失败，22 个错误，集中在 4 个模块：

- `app/domain/chapters.py`：17 个错误，涉及 `CommandContext.write_fence` 的 JSON/TypedDict 取值、可空 `ChapterPlanRevision`/`Scene` 窄化、`links`/`pending_decision`/`revision_history`/`requested_ids` 的容器类型，以及动态 review 输出转换。
- `app/services/generation_runs.py`：Planner 候选持久化调用传入了不完整的 `CommandContext`。
- `app/api/canon.py`：章节与场景查询复用同一个变量，导致 SQLAlchemy 模型类型被错误收窄。
- `app/runtime/run_worker.py`：章节审校持久化调用传入了不完整的 `CommandContext`。

未修改 mypy 配置，未使用 `# type: ignore`，未删除测试。

## 修复

修改文件：

- `backend/app/domain/chapters.py`
- `backend/app/services/generation_runs.py`
- `backend/app/api/canon.py`
- `backend/app/runtime/run_worker.py`
- `backend/tests/runtime/test_e2e_fixtures.py`

修复内容：

- 为 fencing token、JSON 动态结果、工作流聚合字典和场景修订 ID 列表增加明确类型，并对可空 ORM 行进行显式分支窄化。
- Planner 持久化新增完整的 agent `CommandContext` 构造，保留运行身份、幂等键、父运行和计划字段。
- Worker 审校写入改为传递完整的 review `CommandContext`，包含 worker lease 与 write fence 字段。
- Canon 章节/场景目标使用独立变量，避免静态类型污染。
- 调整一个章节工作台 fixture 测试的 import 顺序，使全量 Ruff 可执行并通过。

本次仅修复静态类型和 import 排序问题；没有改变 CAS、lease/fencing、outbox、幂等或业务流程语义。未引入行为变化，因此没有新增 TDD 测试；使用现有覆盖修改面的测试进行验证。

## GREEN：验证结果

### 聚焦 pytest

命令：

```text
python -m pytest tests/domain/test_chapter_workflow.py tests/domain/test_chapter_workflow_task5.py tests/api/test_chapter_workflow_api.py tests/runtime/test_chapter_workflow_task4.py tests/runtime/test_chapter_workflow_task6.py tests/runtime/test_chapter_workflow_worker_task2.py tests/runtime/test_e2e_worker.py tests/runtime/test_e2e_fixtures.py tests/agents/test_chapter_graph.py tests/agents/test_real_chapter_chain.py tests/api/test_chapter_plan_init.py tests/api/test_canon_api.py tests/api/test_canon_read_endpoints.py tests/runtime/test_real_chapter_worker_chain.py tests/runtime/test_real_continuity_review_chain.py tests/runtime/test_outbox_publish.py
```

结果：`96 passed, 3 skipped`。测试过程有依赖库弃用告警，但无失败。

### 全量 Ruff

命令：

```text
python -m ruff check .
```

结果：`All checks passed!`

### compileall

命令：

```text
python -m compileall -q app
```

结果：退出码 `0`。

### 最终 mypy

命令：

```text
python -m mypy app
```

结果：`Success: no issues found in 111 source files`。

## 遗留类型债务与关注项

- 未发现本次章节工作台修改面内的已知类型错误。
- pytest 保留 3 个跳过用例；本次未改变其跳过条件，未单独扩展外部数据库/真实服务环境验证。
- 依赖库仍输出 `asyncio.iscoroutinefunction` 与 LangGraph/checkpoint 版本兼容性弃用告警；这些来自依赖，不属于本次类型修复范围。
