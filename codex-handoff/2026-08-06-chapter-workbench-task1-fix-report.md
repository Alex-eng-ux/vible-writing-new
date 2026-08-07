# 章节工作台任务 1 修复报告

状态：DONE_WITH_CONCERNS

## 修复范围

- backend/app/domain/chapters.py
  - 首次计划接受允许 expected_current_plan_revision_id=None。
  - accepted 计划重放必须同时匹配当前 accepted pointer 和 expected_plan_version。
  - Planner question/proposal 在缺少稳定 ID 时，按 source_run_id + planning_lineage_id + text/field_path 复用已有记录。
  - workflow read 对 accepted pointer 指向不存在或未 accepted 的计划、以及 accepted plan 缺失 SceneBrief 映射返回 phase=blocked。
- backend/app/services/generation_runs.py
  - _apply_accept_action 保留 expected_current_plan_revision_id=None，不再转换为空字符串。
  - persist_planner_output 的 plan、existing_message 变量恢复为可执行代码。
  - Planner discussion message 去重只依赖 source_run_id + planning_lineage_id，不再只检查 kind=proposal。
  - 对缺失的可选 Planner 输出字段使用兼容默认值。
- backend/tests/domain/test_chapter_workflow.py
  - 新增首次接受、accepted 重放 pointer/version 冲突、Planner ready/clarification 重放幂等、workflow blocked 回归。
- backend/tests/api/test_run_lifecycle.py
  - 新增首次计划接受 API 路径回归。
  - 更新 new_chapter 测试 helper，统一传入非空 chapter_intent.text。

## 验证命令

在 E:\vible-writing-new\backend 执行：

    .venv\Scripts\python.exe -m pytest -q tests/domain/test_chapter_workflow.py tests/api/test_chapter_workflow_api.py tests/api/test_run_lifecycle.py

结果：19 passed。

    .venv\Scripts\python.exe -m ruff check app/domain/chapters.py app/services/generation_runs.py tests/domain/test_chapter_workflow.py tests/api/test_chapter_workflow_api.py tests/api/test_run_lifecycle.py

结果：All checks passed!

## 遗留问题

- 本修复没有新增 Alembic migration；ChapterPlanRevision 新列和新增规划业务表仍需后续迁移任务补齐，当前不能据此宣称生产数据库可直接升级。
- ChapterPlannerAgent 本身没有生成稳定的 question_id / proposal_id；本次在持久化层增加了基于 source run、规划血缘和语义字段的回退幂等键。后续可在 Agent 输出契约中显式生成并验证稳定 ID。
- 未覆盖 Worker 队列恢复、outbox 重放和 Playwright 端到端章节旅程。
