# Chapter Workbench Task 4 交接报告

## 范围

实现 accepted plan 后的场景队列、按计划顺序推进、场景基线传播、逐场决策阻断、反馈影响闭包和 workflow 权威状态读取。未提交 Git commit，未回滚其他 agent 的改动。

## 已完成

- `RunWorker` 每次 tick 消费 accepted-plan outbox 后，会扫描当前 accepted plan，按 `ChapterPlanSceneLink.sort_order` 只恢复一个可运行场景。
- 前一场景必须同时具备同一 `plan_revision_id` 下的 accepted run 和 `Scene.accepted_scene_revision_id`，后续场景才会入队；重复 tick/outbox replay 不会重复创建。
- 场景 run 的持久化输入和 queued 事件携带 `plan_revision_id`、`base_scene_revision_id`；run snapshot 和 workflow active run 对外暴露这两个字段。
- 场景创建校验目标场景必须属于当前 accepted plan；场景 accept/feedback 决策校验前置场景已接受，防止手动启动绕过队列。
- 场景反馈按 accepted plan 顺序计算当前场景及下游闭包，记录 `affected_scene_ids`/`stale_scene_ids`，并将下游活动旧 run 标记为 `superseded`。
- `chapter_workflow_read` 只读取当前 accepted plan 的场景运行，并返回场景级及章节级 `blocking_reasons`；反馈闭包状态可直接读取。
- `build_scene_feedback_queue` 优先使用 accepted plan link 顺序；无计划场景时保留原有创建时间回退行为，兼容迁移/测试路径。

## 验证

红灯阶段新增 `backend/tests/runtime/test_chapter_workflow_task4.py` 后，首次运行因默认 graph 状态与测试假设不一致失败；调整测试状态集合并完成实现后转绿。

通过：

- `pytest tests/runtime/test_chapter_workflow_task4.py tests/runtime/test_chapter_workflow_worker_task2.py tests/domain/test_chapter_orchestration.py tests/api/test_run_lifecycle.py -q`：34 passed。
- `pytest tests/services tests/api -q`：全量通过（服务/API 测试）。
- `ruff check app/runtime/run_worker.py app/services/generation_runs.py app/domain/chapter_orchestration.py app/domain/chapters.py app/api/schemas.py tests/runtime/test_chapter_workflow_task4.py`：通过。

## 当前不足与风险

| 问题 | 影响 | 验证状态 |
|---|---|---|
| 场景 feedback 仍复用当前 run 的 waiting_feedback 状态，没有在同一命令内创建 RevisionAgent 子 run | 反馈后的补丁生成仍依赖后续现有执行链，产品若要求反馈立即生成子运行需继续接线 | 已确认设计规范要求 RevisionAgent；本任务未改变既有运行生命周期契约 |
| 队列推进由 Worker 下一次 tick 异步完成，accept API 返回不会立即创建下一场运行 | UI 需要通过 workflow/SSE 观察短暂等待 | 已通过 worker recovery 测试验证幂等推进 |
| `GenerationRun` 的 `base_scene_revision_id` 仍保存在不可变 `normalized_input`，未新增独立数据库列 | 查询需读取输入信封，迁移旧数据时需保持兼容 | 已通过 run 创建/worker 测试验证 |

## 主 agent 验收修复

- 新增 `test_manual_run_cannot_skip_unaccepted_previous_scene`，先观察到创建后续场景 run 未抛错的红灯，再将同一队列前置校验接入 `start_generation_run`；修复后该测试通过。
- Worker 采用独立会话提交运行/映射/outbox，原 fixture 只清 outbox 会让旧 accepted plan 污染后续 `tick()`；已在 `backend/tests/conftest.py` 清理相关运行、事件和计划映射，并复跑任务 2/4 组合套件。

主 agent 验证：focused suite `35 passed`；服务/API 全量 `87 passed`；Ruff 和 compileall 通过。未提交 Git commit。
