# 章节工作台任务 1 残余修复报告

状态：已完成本轮残余修复，指定阻断项未发现未解决项。

## 改动摘要

- `backend/app/domain/chapters.py`
  - 计划接受事务统一负责场景物化与 accepted outbox 写入。
  - 兼容只有 `chapter_contract.scenes` 的计划，回退到该字段生成场景映射。
- `backend/app/services/generation_runs.py`
  - 删除 `_apply_accept_action()` 中重复的 `materialize_chapter_plan()` 调用，避免接受命令重复触发物化/outbox。
  - 删除因上述调整产生的未使用导入和局部变量。
  - feedback 子运行保留 `child.supersedes_run_id = parent.id`，不再反向写入 `parent.supersedes_run_id`。
- `backend/tests/domain/test_chapter_workflow.py`
  - 接受计划测试直接验证接受事务已建立 scene link、accepted outbox 和 workflow 场景映射。
- `backend/tests/api/test_run_lifecycle.py`
  - 新增 feedback parent/child `supersedes_run_id` 方向回归测试。

## 既有 blocker 复核

- 首次 accept 仍传递 `expected_current_plan_revision_id=None`，领域 CAS 分支允许空 current pointer。
- Planner 重试按 `source_run_id`、规划血缘及稳定问题/建议键复用 discussion、question、proposal 记录。
- `chapter_workflow_read()` 对悬挂 accepted pointer、非 accepted 目标计划和缺失 scene mapping 返回 `phase="blocked"`。
- `test_run_lifecycle.py` 的 new-chapter fixture 默认携带非空 `chapter_intent.text`。

## 验证结果

在 `E:\vible-writing-new\backend` 执行：

` .venv\\Scripts\\python.exe -m pytest -q tests/domain/test_chapter_workflow.py tests/api/test_chapter_workflow_api.py tests/api/test_run_lifecycle.py `

结果：`20 passed`。

` .venv\\Scripts\\python.exe -m ruff check app/domain/chapters.py app/services/generation_runs.py tests/domain/test_chapter_workflow.py tests/api/test_chapter_workflow_api.py tests/api/test_run_lifecycle.py `

结果：`All checks passed!`。

` .venv\\Scripts\\python.exe -m compileall -q app/domain/chapters.py app/services/generation_runs.py app/db/migrations/versions/a1b2c3d4e5f6_add_chapter_planning_workflow_persistence.py tests/domain/test_chapter_workflow.py tests/api/test_chapter_workflow_api.py tests/api/test_run_lifecycle.py `

结果：通过。

`git diff --check`

结果：通过；仅有既有 LF/CRLF 提示，无 whitespace error。
