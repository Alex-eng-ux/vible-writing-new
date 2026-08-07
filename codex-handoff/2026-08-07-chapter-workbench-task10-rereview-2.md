# Chapter Workbench Task 10 最终复审

## 复审结论

**APPROVED**

当前工作区已满足本轮指定的 I2、I3、I5、M1 验收点；真实 Playwright 三条测试在允许子进程启动的环境中全部通过。

## 逐项核对

### I2：已落地

`frontend/tests/chapter-workflow.spec.ts` 在计划接受前断言 `workflow.scenes === []`、场景队列无条目、没有场景运行 POST，且章节工作区没有审校按钮。计划接受后先选择第二场，在第一场尚未接受时点击审校，断言 UI 显示 `previous_scene_not_accepted`、服务端 blocker 存在，并确认没有发出 `/api/scenes/*/runs` 请求（约 `231-244`）。第一场接受后再断言第二场 blocker 清空并允许运行（约 `266-273`）。

### I3：已落地

Canon 流程先读取章节候选快照，自动 outbox 已有 run 时复用，只有没有 `run_id` 才点击提取按钮。测试断言 `target_type=chapter`、目标章节、列表顶层 `source_revision_id` 等于 accepted `ChapterRevision`、以及初始 run status；播种后逐项断言既有 API 契约中的 `scope=chapter` 和 `scene_id=null`（约 `306-342`）。不再错误断言候选项不存在的 `source_revision_id` 字段。

### I5：已落地

`seed_canon_candidates` 按 `generation_run_id` 找回已有三类候选，并在已有 `run_waiting_feedback` 事件时跳过 fencing/event 推进；重复调用返回同一 candidate id。主旅程显式重复调用该命令并比较返回 id（约 `327-332`）。章节计划候选和章节审校 fixture 也按 run identity 复用 revision/event，不重复推进 fencing。

### M1：已落地

失败 fixture/运行轮询会调用 `diagnose --chapter-id`，输出 phase、run id、pending decision、最后事件序号和 outbox。`diagnose_chapter` 现在只查询 `pending/publishing/published/failed`，排除已消费记录；无活动 run 时只按章节 resource 查询，避免 `generation_run_id IS NULL` 扩大范围（`backend/app/db/e2e_fixtures.py:563-599`）。

### 其他复核项

- 规划主旅程通过章节工作区 UI 启动 `new_chapter`，并捕获请求体断言自然语言意图。
- 章节接受后的 Canon 自动 run 与无 run 手动兜底均有分支处理，不再无条件点击 disabled 按钮。
- `sceneRunId` 对 `data-full-run-id` 做非空 UUID 格式断言。
- 修复报告如实记录了此前行为级 RED、GREEN 和浏览器验证，不再把 `spawn EPERM` 冒充测试 RED。

## 验证证据

- `backend/.venv/Scripts/python.exe -m pytest tests/api/test_chapter_workflow_api.py tests/domain/test_chapter_workflow.py tests/runtime/test_chapter_workflow_task6.py tests/runtime/test_e2e_fixtures.py -q`：13 passed。
- `frontend/npm run typecheck`：通过。
- `backend/.venv/Scripts/python.exe -m py_compile app/db/e2e_fixtures.py`：通过。
- `frontend/node_modules/.bin/playwright.cmd test tests/chapter-workflow.spec.ts --list`：3 tests listed。
- 提权执行 `frontend/node_modules/.bin/playwright.cmd test tests/chapter-workflow.spec.ts --project=chromium`：3 passed（23.7s）。

## 残余基础设施提示

Playwright webServer 日志在 Worker 启动早期出现一次 `UndefinedTable: run_outbox_records`，随后三条测试仍完整通过。该日志更像 E2E 数据库迁移/启动竞态，建议由独立基础设施任务清理；本次测试结果未显示 Task 10 行为阻断。

## Final Verdict

**APPROVED**
