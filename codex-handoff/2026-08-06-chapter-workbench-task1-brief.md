# 阶段 0 后端章节工作流任务

## 目标

把真实章节规划运行需要的后端业务契约落地到可测试的持久化和读取接口：候选计划不能直接成为 accepted plan；作者必须能读取候选、讨论、问题/建议并用版本基线接受具体计划。

## 负责范围

- `backend/app/agents/schemas.py`
- `backend/app/api/schemas.py`
- `backend/app/db/models.py`
- `backend/app/domain/chapters.py`
- `backend/app/api/chapters.py`
- `backend/app/services/generation_runs.py`
- 可新增一个后端 workflow 服务模块，但不要修改前端、`run_worker.py`、Playwright 配置或旧场景图编排。
- 新增或修改对应后端测试，优先放在 `backend/tests/api/`、`backend/tests/domain/`、`backend/tests/agents/`。

## 必须满足

1. 保持旧数据可读；不要删除旧 `GET /api/chapters/{chapter_id}/plan` 或旧初始化 POST，本任务只为迁移准备真实路径。
2. 规划运行的 `chapter_intent.text` 必须能进入 Planner 输入契约；Planner 输出需要有明确的候选语义，未确认内容不能写入 accepted plan。
3. `ChapterWorkflowRead` 至少能返回阶段、pending decision、意图、候选/accepted plan 指针、SceneBrief、讨论消息 ID/顺序、问题/建议、活动运行和阻塞原因。
4. 候选计划持久化必须可重试幂等；同一运行节点重试不能产生第二个语义候选。
5. 计划接受必须校验具体 `plan_revision_id`、当前指针和版本基线；接受成功后才更新 accepted pointer、物化固定场景映射并准备 `chapter_plan.accepted` outbox。
6. 计划反馈必须保留作者正文和同规划血缘的父/子运行关系；不能只保存反馈哈希，也不能静默按最新记录选运行。
7. 不改变场景生成 Worker 的默认图行为；Worker 接线由下一项任务负责。

## 验证要求

- 至少新增覆盖：候选持久化幂等、workflow read、计划接受 CAS/重复命令、反馈子运行 lineage 或明确记录未能在本任务完成的依赖。
- 运行相关后端测试，并报告精确命令和通过/失败数量。
- 运行 `ruff` 针对改动文件；发现既有基线错误要区分报告。

## 交付格式

完成后写入 `codex-handoff/2026-08-06-chapter-workbench-task1-report.md`，报告：改动文件、实现摘要、测试命令/结果、未完成依赖和风险。返回状态只能是 `DONE`、`DONE_WITH_CONCERNS`、`NEEDS_CONTEXT` 或 `BLOCKED`。
