# 后端启动竞态与章节基线复核报告

## 结论

已修复 E2E Worker 的 schema 就绪竞态，并补上章节反馈/接受路径对前端传入
`base_chapter_revision_id` 的服务端校验。修改只涉及后端和后端回归测试。

## 根因

`frontend/tests/global-setup.ts` 会先通过 `app.db.e2e_bootstrap` 重建 `novel_e2e`；
但 Playwright 随后启动 API 和 E2E Worker 时，`app.e2e_worker` 先启动
`RunWorker.run_forever()`，再无条件开放 `/ready`。若数据库重建尚未完成或复用进程
与重置交叠，Worker 的首个 `tick()` 会查询 `run_outbox_records` 并得到
`UndefinedTable`。该异常会被常驻循环吞掉后重试，而 `/ready` 仍返回 200，形成虚假就绪。

## 修复

- `app.e2e_worker._wait_for_database()` 在启动 Worker 线程前轮询
  `run_outbox_records`，仅在关键表可读后才开放 `/ready`；60 秒内未完成则 fail-closed。
- `submit_run_decision()` 在 `target=chapter` 的 `accept` 或 `feedback` 命令中，消费
  `base_chapter_revision_id`：非空时必须等于章节当前 accepted 指针；并校验可选
  `chapter_revision_id` 属于该章节。保留未发送基线的旧首版接受兼容路径。

## TDD 证据

先新增并观察到以下失败：

1. `test_chapter_feedback_rejects_stale_base_revision` 在修复前返回 200，预期为
   `409 CHAPTER_OUT_OF_SYNC`。
2. `test_e2e_worker_waits_for_required_schema_before_starting` 在实现前无法导入
   `_wait_for_database`。

实现后执行：

```powershell
cd E:\vible-writing-new\backend
.venv\Scripts\python.exe -m pytest tests/api/test_run_lifecycle.py tests/api/test_canon_api.py tests/runtime/test_e2e_worker.py tests/runtime/test_e2e_fixtures.py -q
# 45 passed

.venv\Scripts\python.exe -m ruff check app/e2e_worker.py app/services/generation_runs.py tests/runtime/test_e2e_worker.py tests/api/test_run_lifecycle.py
# All checks passed!
```

## 余项

本次未重跑完整 Playwright；上层验收应在无残留 Worker 的端口上执行完整浏览器回归，确认
进程启动顺序和页面交互都通过。
