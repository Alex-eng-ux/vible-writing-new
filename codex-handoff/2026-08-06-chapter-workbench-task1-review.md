# 阶段 0 后端章节工作流任务 1 主验收报告

状态：REJECTED_FOR_REPAIR

## 审查范围

- backend/app/agents/schemas.py
- backend/app/agents/chapter_planner.py
- backend/app/api/schemas.py
- backend/app/db/models.py
- backend/app/domain/chapters.py
- backend/app/api/chapters.py
- backend/app/services/generation_runs.py
- backend/tests/api/test_chapter_workflow_api.py
- backend/tests/domain/test_chapter_workflow.py
- 计划书：docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md

## 验收命令

- backend\.venv\Scripts\python.exe -m pytest -q backend/tests/domain/test_chapter_workflow.py
  - 结果：2 passed。
- backend\.venv\Scripts\python.exe -m pytest -q backend/tests/api/test_run_lifecycle.py
  - 结果：9 failed, 3 passed。主要失败来自新增 chapter_intent.text 契约后旧 helper 未更新，违反阶段 0 “现有 pytest 不回归”的进入要求。

## 阻断问题

### 1. 数据库迁移没有覆盖新增业务表和列

严重级别：High

证据：backend/app/db/models.py 新增/扩展了 ChapterPlanRevision 字段以及 chapter_plan_discussion_messages、chapter_plan_questions、chapter_plan_proposals、chapter_plan_scene_links。现有 backend/app/db/migrations/versions/1c1dccd138fb_initial_schema.py 只包含旧版 chapter_plan_revisions 和 chapter_plan_revision_links，没有新增列、索引、唯一约束和四张新业务表。

影响：任何从迁移创建或升级来的数据库都会缺列/缺表，workflow、Planner 持久化、计划接受和场景映射都会在运行时失败。计划书 3.5 明确要求这些对象必须通过迁移创建，不能只改 ORM 模型。

### 2. 首次通过运行决策接受计划时会错误拒绝

严重级别：High

证据：backend/app/services/generation_runs.py 的 _apply_accept_action() 调用 accept_chapter_plan_revision() 时传入 body.expected_current_plan_revision_id or ""。首次接受计划时当前 accepted pointer 应为空，客户端通常传 null 或不传；这里会被转换成空字符串。accept_chapter_plan_revision() 对 link is None 的合法分支要求 expected_current_plan_revision_id is None，因此会抛 PLAN_REVISION_CONFLICT。

影响：主流程的第一次“作者接受 Planner 候选计划”无法从公共 /api/runs/{run_id}/decisions 入口成功执行。现有领域测试绕过了运行决策入口，未覆盖真实 API 路径。

### 3. Planner 输出重试不会保持讨论和问题幂等

严重级别：High

证据：persist_planner_output() 只用 kind == proposal 查询已有消息；当输出是 needs_clarification 时实际写入 kind == question，同一 run/node 重试会重复追加问题消息。upsert_plan_questions() 在 Planner 未提供 question_id 时每次都会创建新问题，没有按同一 source_run_id/文本/血缘复用稳定 ID。

影响：同一 Planner 节点重试会产生多个语义相同的问题和消息，违反计划书 3.5/6.1.5 对 message_id、message_sequence、question_id 稳定和反馈重放不得生成新语义 ID 的要求。后续 UI 会看到重复问题，作者回答也可能绑定到错误的问题记录。

### 4. workflow 读取没有阻断 accepted pointer 悬空和 accepted plan 映射缺失

严重级别：High

证据：chapter_workflow_read() 在 accepted link 存在但目标 ChapterPlanRevision 不存在时把 accepted 当作 None 继续读，不加入 blocking reason。accepted plan 存在但没有 chapter_plan_scene_links 时也不会阻断，可能直接进入 chapter_review。计划书要求 pointer 悬空、候选映射缺失或 accepted 指针悬空时返回 blocked，不得静默选择或继续推进。

影响：数据库处于“计划已接受但没有可恢复场景队列”的孤儿状态时，workflow 会给前端错误阶段，后续 Worker 不能可靠恢复第一个场景运行。

### 5. 新契约导致既有运行生命周期测试回归

严重级别：High

证据：独立执行 backend/tests/api/test_run_lifecycle.py 结果为 9 failed, 3 passed，失败集中在 _create_run() 仍未传 chapter_intent.text，接口返回 COMMAND_CONTEXT_MISMATCH。

影响：阶段 0 要求现有 pytest 不回归；这些测试覆盖运行幂等、CAS、API command fence、暂停恢复、SSE 和跨章节入口，不能在进入下一阶段前保持失败。正确修复方向是更新测试 fixture 传入非空 intent，并保留新增空 intent 拒绝测试。

## 重要非阻断问题

### 6. 计划接受事务被重复物化，语义边界不清

严重级别：Medium

证据：accept_chapter_plan_revision() 已在接受事务中调用 materialize_chapter_plan()；_apply_accept_action() 随后又再次调用 materialize_chapter_plan()。当前 outbox 有去重，scene link 也会复用，但同一命令内双重调用让事务边界难以审计。

影响：后续接 Worker/outbox 重放时容易误判“接受事务发布一次事件”的来源，也增加迁移到真实消费者时的重复副作用风险。

### 7. feedback 子运行血缘字段写法容易造成反向关系歧义

严重级别：Medium

证据：feedback 子运行设置 parent_generation_run_id = parent.id 且 supersedes_run_id = parent.id，随后父运行又被写成 run.supersedes_run_id = child_id。同一个字段同时表示“我取代了谁”和“谁取代了我”。

影响：后续恢复、讨论血缘和审计读取可能无法判断真正的 supersedes 方向。建议保留 child 指向 parent 的 supersedes 语义，父运行仅通过 status=superseded 或新增显式字段表达被取代关系。

## 结论

任务 1 的局部 schema、领域函数和 API 骨架有价值，但不能验收为阶段 0 完成，也不建议在此基础上直接进入 Worker/E2E。建议先派修复子任务，补齐迁移、API accept 首次路径、Planner 幂等、workflow blocked 规则和回归测试，再重新跑后端局部与 test_run_lifecycle.py。
