# Chapter Workbench Task 10 Review 修复报告

## 修复内容

- 规划主旅程完全由 UI 启动 `new_chapter`，并验证请求体和自然语言意图。
- 计划接受前验证 `scenes=[]`、无场景运行请求、阻断 UI；接受后验证有序场景队列。
- 章节接受后的 Canon 流程复用自动 outbox 已有 run，仅无 run 时点击提取；断言章节 `ChapterRevision` 来源、chapter scope、`scene_id=null`、run 状态及候选来源版本。
- fixture 候选/章节审校改为按 run identity get-or-create，重复调用不新增 revision、event 或 fencing token；新增 `diagnose` 诊断命令。
- 主旅程对 `seed-plan-candidate` 与 `seed-chapter-review` 各重复调用一次并断言返回 revision id 不变，作为幂等回归覆盖。
- 场景运行 id 读取增加非空格式断言。

## 验证

- `frontend/npm run typecheck`：通过。
- `backend/.venv/Scripts/python.exe -m py_compile app/db/e2e_fixtures.py`：通过。
- `backend/.venv/Scripts/python.exe -m pytest tests/runtime/test_e2e_fixtures.py -q`：`2 passed`。
- `frontend/node_modules/.bin/playwright.cmd test tests/chapter-workflow.spec.ts --list`：3 tests listed。
- 真实 Playwright 执行：`Error: spawn EPERM`，未取得行为级 RED/GREEN 证据，未宣称浏览器通过。

此前轮次未修改生产业务代码；本轮按 reviewer 要求仅修改了 intent 回读和场景 blocker 相关生产语义，未创建 commit。

## Round 2 修复追加

### RED

- 新增后端 API 回归断言后，`tests/api/test_chapter_workflow_api.py` 首次运行失败：workflow `intent.text` 为章节初始值 `intent`，而不是本次 `new_chapter` 请求中的自然语言意图。
- 真实 Playwright 首次重跑已进入浏览器并复现主旅程失败：首场景被后续场景的 `previous_scene_not_accepted` 全局阻断；修复后又暴露 Canon confirm 的 `CHAPTER_HANDOFF_CONFLICT` fixture 前置条件。两者均已作为行为级 RED 处理。

### 实现

- `start_generation_run` 在 `new_chapter` 校验通过后持久化 `chapter.chapter_intent`；`chapter_workflow_read` 活动运行优先从 `GenerationRun.normalized_input.chapter_intent` 回读，保证重载一致。
- 工作流只把当前选中场景的 blocker 用于运行前置检查；后续场景仍保留 `blocking_reasons=["previous_scene_not_accepted"]`，但不再把该 blocker 提升为全局章节阻断。
- `seed_canon_candidates` 按 run 查询既有三类候选和 `run_waiting_feedback` 事件；重复调用返回同一 candidate id，不增加 fencing token 或事件序号。
- `diagnose_chapter` 只查询 `pending/publishing/published/failed` 状态；没有 run id 时仅按章节 `resource_id` 查询，避免 `generation_run_id IS NULL` 扩大范围。
- 测试改为断言列表顶层 `source_revision_id`；候选逐项仅断言既有 `scope` 和 `scene_id` 契约，没有扩展 CanonCandidate API。
- `seed_chapter_review` 补齐 `entry_handoff_status=in_sync`，满足章节级 Canon confirm 的既有前置契约。

本轮对 `backend/app/services/generation_runs.py` 与 `backend/app/domain/chapters.py` 有必要的生产语义修复，属于 reviewer 指定的 scope exception；未改动无关服务或 Canon API 契约。

### 验证结果

```text
RED：
backend\\.venv\\Scripts\\python.exe -m pytest tests/api/test_chapter_workflow_api.py -q
1 failed（intent.text 为 intent，而非请求意图）

GREEN：
backend\\.venv\\Scripts\\python.exe -m pytest tests/api/test_chapter_workflow_api.py tests/domain/test_chapter_workflow.py tests/runtime/test_chapter_workflow_task6.py tests/runtime/test_e2e_fixtures.py -q
13 passed

frontend\\npm run typecheck
通过

backend\\.venv\\Scripts\\python.exe -m py_compile app/db/e2e_fixtures.py
通过

frontend\\node_modules\\.bin\\playwright.cmd test tests/chapter-workflow.spec.ts --list
3 tests listed

frontend\\node_modules\\.bin\\playwright.cmd test tests/chapter-workflow.spec.ts --project=chromium
3 passed (26.2s)
```

Playwright webServer 日志出现既有 E2E 数据库 `run_outbox_records` 缺表告警，但本轮 3 个测试均通过；该环境/迁移问题仍应在独立基础设施任务中处理。未创建 commit。
