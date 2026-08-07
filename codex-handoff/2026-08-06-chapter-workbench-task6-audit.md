# 任务 6：Canon 闭环与完整旅程审计

审计范围：设计文档阶段 4、主流程第 7-10 步；现有 Canon/Story Bible API、Worker、前端和相关测试。仅做只读审计，未修改业务代码。

## 目标契约

设计文档 `docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md:713-717` 要求：章节接受后自动进入章节 Canon 运行，作者逐条处理候选，确认项写入 Story Bible，并由完整 Playwright 旅程验证。

主流程 `:737-740` 要求：

1. 所有场景接受后创建章节审校运行并读取 staged ChapterRevision。
2. 作者接受章节版本，workflow 阶段和 accepted 指针保持一致。
3. accepted 章节版本事件幂等消费后自动创建章节 Canon 运行。
4. 对事实/事件/剧情线分别 confirm、reject、defer，只有 confirm 进入 Story Bible。

## 已具备能力

- `backend/app/api/canon.py:72-268` 已提供项目 Story Bible 读取、章节/场景 Canon 候选读取、专用 Canon run 创建和逐批决策入口。
- `backend/app/services/canon_runs.py:224-362` 校验 Canon 运行必须消费当前 accepted 章节/场景版本；`backend/app/domain/story_bible.py:500-560` 再次校验候选来源、作用域和当前 accepted 指针。
- `backend/app/domain/story_bible.py:394-485` 的正式决策事务支持 `accepted|rejected|deferred`，仅章节级 accepted 候选物化 `CanonFact`/`TimelineEvent`/`PlotThread`；场景级确认不写全局 Story Bible。
- `backend/tests/api/test_canon_api.py` 已覆盖三类候选决策、幂等重放、错误来源/作用域、场景不写全局 Canon、取消保留 pending、并发消费只创建一个 Canon run 等底层契约。
- `backend/app/domain/chapters.py:789-836` 的 `commit_chapter_version()` 会在章节接受事务内写入 `chapter_revision.accepted` outbox。
- `backend/app/services/canon_runs.py:612-651` 已有按 `(chapter_id, accepted_revision_id)` 去重的 `handle_chapter_accepted_outbox()`。
- `backend/app/runtime/run_worker.py:274-335` 能在场景队列完成后幂等创建章节审校运行；`:852-870` 能按 `decision_target="canon"` 构建 CanonGraph。
- `frontend/src/features/storybible/StoryBiblePanel.tsx` 已能读取正式 Story Bible、读取候选、创建场景/章节 Canon run，并提交候选决策。

## 具体缺口

### P0：accepted 章节版本到 Canon run 未接入 Worker

`RunWorker.tick()` (`backend/app/runtime/run_worker.py:102-122`) 只调用 `_consume_plan_outbox()` 和 `_recover_accepted_plan_scene_queues()`；未消费 `resource_type="chapter_revision"` 或调用 `handle_chapter_accepted_outbox()`。因此 `commit_chapter_version()` 写出的 `chapter_revision.accepted` 事件不会自动创建 Canon run，除非测试/外部代码手动调用消费者。

影响：设计主流程第 9 步无法自动推进，阶段 4 的“章节接受后自动进入 Canon”不成立。

### P0：章节工作区没有章节审校/版本接受动作

`frontend/src/app/page.tsx:1225-1247` 的章节工作区只展示意图、Planner 讨论、候选计划和场景队列，没有 staged ChapterRevision、审校问题、影响范围、章节接受按钮，也没有调用 `target="chapter"` 的决策命令。后端虽在 `generation_runs.py:673-680` 支持章节接受，但前端没有入口。

影响：主流程第 7-8 步不能通过章节工作区完成，Canon 也没有稳定的 UI 前置。

### P0：Canon 面板不在章节上下文中可用

`frontend/src/app/page.tsx:1324-1423` 仅在 `selectedScene` 存在时渲染 `StoryBiblePanel`。章节工作区选中章节但未选场景时，作者看不到章节 Canon、无法启动或决策章节 Canon；这与“全程不离开章节工作区”的验收要求冲突。

### P1：ChapterWorkflowRead 没有 Canon 权威状态

`backend/app/domain/chapters.py:666-679` 返回的 `canon_run_id` 固定为 `None`，未读取当前章节 Canon run 的状态、来源版本或待决候选。虽有独立候选 API，前端必须自行拼接多个接口，无法按 workflow 单一权威视图恢复 Canon 阶段。

### P1：完整旅程测试缺失

`frontend/tests/chapter-workflow.spec.ts` 当前只验证规划运行和章节工作区初始展示，没有覆盖章节场景全部接受、章节审校、章节版本接受、自动 Canon run、confirm/reject/defer 和 Story Bible 最终条目。现有 `backend/tests/api/test_canon_api.py` 是底层 API 测试，不能替代设计文档要求的 UI 旅程。

### P1：自动消费者缺少 Worker 集成证据

虽然 `handle_chapter_accepted_outbox()` 有独立幂等逻辑和 API 测试手动调用，但没有验证真实 `RunWorker.tick()` 从 accepted outbox 消费到 Canon run 入队、再执行 CanonGraph、暂停等待作者的链路。

## 建议拆分

1. **Task 6A：自动 Canon 事件接线**
   - 在 Worker 的 outbox 消费路径接入 `chapter_revision.accepted`，沿用 `handle_chapter_accepted_outbox()` 的 advisory lock/幂等键。
   - 明确 pending/published/consumed/failed 状态转换和重启重放行为。

2. **Task 6B：章节审校与接受工作区**
   - 在 workflow 视图展示 staged revision、review issues、影响范围和历史。
   - 增加章节接受/反馈/取消 UI，调用 `target="chapter"` 并传递 run/version/base CAS。
   - 接受成功后刷新 workflow，确认 accepted 指针和阻断状态。

3. **Task 6C：章节 Canon 工作区接线**
   - 将 Canon 状态和候选纳入章节工作区上下文；至少展示 `canon_run_id`、run status、source revision 和 pending candidates。
   - 允许在无 selected scene 时启动章节 Canon，并逐条提交 confirm/reject/defer。
   - 保证 scene scope 的确认继续只更新场景候选，不写全局 Story Bible。

4. **Task 6D：完整 Playwright 旅程与恢复**
   - 使用 API、前端、Worker 和 deterministic Fake provider 的干净 fixture。
   - 按 workflow/SSE sequence 等待，不使用固定 sleep；失败时打印 phase、run_id、pending_decision、event sequence 和未消费 outbox。

## 可验证测试路径

### 后端单元/API

- 复用并扩展 `backend/tests/api/test_canon_api.py`：
  - accepted chapter revision 约束；
  - confirm/reject/defer 三类候选；
  - 只有 confirm 写入正式 Story Bible；
  - 场景确认不写全局；
  - 同一 accepted outbox 重放只创建一个 Canon run。
- 新增 Worker 集成测试：提交 `commit_chapter_version()` 后调用真实 `RunWorker.tick()`，断言 `chapter_revision.accepted` 被消费、只生成一个 Canon run，重复 tick/重放不重复创建。
- 新增 workflow read 测试：Canon run queued/waiting_feedback/accepted 时返回对应 `canon_run_id`、来源版本和候选状态。

### Playwright

扩展 `frontend/tests/chapter-workflow.spec.ts` 为单条完整旅程：章节意图 → Planner 澄清/反馈 → 接受计划 → 顺序场景生成与接受 → 章节审校 → 接受 ChapterRevision → 自动 Canon run → 分别 confirm/reject/defer → Story Bible 仅出现 confirmed 条目。

额外覆盖：Worker 重启后从 accepted outbox 恢复；旧 accepted 版本 Canon 被拒；stale/handoff 阻断在章节工作区可见；重复决策使用同一幂等键返回同一结果。

## 结论

Canon 领域和专用 API 已具备较完整的底层契约，不能据此判定任务 6 落地。当前至少存在三个 P0 缺口：accepted chapter revision 事件未接入 Worker、章节审校/接受未接入章节工作区、章节 Canon 面板仅挂在 selected scene 下；另有 workflow Canon 状态和完整 Playwright 证据缺失。建议按 6A→6B→6C→6D 顺序实施后再进行阶段 4 验收。
