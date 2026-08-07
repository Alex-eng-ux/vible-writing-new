# 阶段 0 后端章节工作流任务报告

状态：DONE_WITH_CONCERNS

## 改动文件

- `backend/app/agents/schemas.py`
- `backend/app/agents/chapter_planner.py`
- `backend/app/api/schemas.py`
- `backend/app/api/chapters.py`
- `backend/app/db/models.py`
- `backend/app/domain/chapters.py`
- `backend/app/services/generation_runs.py`
- `backend/tests/domain/test_chapter_workflow.py`
- `backend/tests/api/test_chapter_workflow_api.py`

## 实现摘要

- 扩展 `AgentInputEnvelope` 和 `ChapterPlanOutput`，支持自然语言章节意图、规划讨论、待回答问题、待确认建议、来源与未解决假设。
- 新增候选计划元数据和章节规划持久化模型：讨论消息、问题、建议、固定场景映射；消息序号、候选版本和 source run 具备稳定/幂等约束。
- 新增 `persist_chapter_plan_candidate`、讨论消息/问题/建议持久化以及 `persist_planner_output`，同一 Planner 运行重试不会创建第二个候选。
- 计划接受增加 CAS 锁、候选来源校验；接受候选后固定场景映射并准备 `chapter_plan.accepted` outbox。
- 新增 `GET /api/chapters/{chapter_id}/workflow` 组合读取视图，返回阶段、pending decision、意图、讨论、候选/accepted 指针、场景队列和阻塞原因。
- 计划 feedback 保存作者正文和问题/建议决策，并创建同规划血缘的 Planner child run；父运行在子运行入队后标记 `superseded`。
- `new_chapter + decision_target=plan` 入口要求非空 `chapter_intent.text`；旧 `/plan` 初始化接口保持兼容。

## 测试与验证

- `backend\\.venv\\Scripts\\python.exe -m pytest tests/agents/test_chapter_agents.py tests/agents/test_chapter_agents_provider.py tests/api/test_chapter_plan_init.py tests/api/test_chapter_workflow_api.py tests/domain/test_chapter_workflow.py -q`
  - 结果：22 passed。
- `ruff check backend/app/agents/schemas.py backend/app/agents/chapter_planner.py backend/app/api/schemas.py backend/app/api/chapters.py backend/app/db/models.py backend/app/domain/chapters.py backend/app/services/generation_runs.py backend/tests/domain/test_chapter_workflow.py backend/tests/api/test_chapter_workflow_api.py`
  - 结果：All checks passed。
- `python -m compileall -q backend/app`
  - 结果：通过。
- `git diff --check`
  - 结果：通过（仅有 Git 的换行格式提示）。

## 未完成依赖与风险

- 本任务 brief 限定未修改 Alembic migration；新增五张规划业务表及计划新增列需要后续迁移任务补齐，生产数据库升级前不能宣称已完成部署。
- 未修改 `run_worker.py`、默认章节图和 Playwright fixture；Planner 候选真正由 Worker 节点落库、Worker 重启恢复和端到端场景生成仍由后续任务接线。
- 既有 `tests/api/test_run_lifecycle.py` 的旧 helper 没有发送 `chapter_intent.text`，运行该文件时有 8 个用例因新契约返回 `COMMAND_CONTEXT_MISMATCH`；这是入口收紧造成的基线测试不兼容，需要后续统一更新测试 fixture，而非放宽新契约。

