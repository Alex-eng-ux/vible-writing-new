# Chapter Workbench Task 10 修复复审

## 复审范围

复核了修复后的 `frontend/tests/chapter-workflow.spec.ts`、`backend/app/db/e2e_fixtures.py`、Task 10 review/fix report，并对 Canon API/schema 做了静态契约核对。

## 逐项结论

### C1：已修复

测试先读取 `/api/chapters/{chapter_id}/canon-candidates`；只有没有 `run_id` 时才点击 `btn-canon-start`，因此章节接受 outbox 自动创建的 Canon run 会被复用，不再无条件点击可能已禁用的按钮（测试约 `302-309`）。

### I1：已修复

规划测试现在通过章节工作区填写意图并点击 `btn-start-chapter-planning`，并监听 POST 请求断言 `new_chapter`、`decision_target=plan` 和意图正文（约 `94-140`）。项目/卷/章节的 API 创建仍属于 fixture 建立。

### I2：部分修复，仍缺下游阻断证据

计划接受前已断言候选队列无 `li`、workflow `scenes=[]`、无场景 run POST，且章节工作区没有 `btn-review`（约 `201-219`），满足“未接受计划不创建场景”的主要证据。

但计划接受后、第一场接受前，测试没有选中第二场并验证其 `blocking_reasons` 为 `previous_scene_not_accepted`，也没有断言点击第二场审校不会发出 `/api/scenes/*/runs`。现有断言只覆盖第一场接受后的解阻结果 `afterFirst.scenes[1].blocking_reasons === []`（约 `254-256`），所以 brief 要求的“阻断状态必须阻止 UI 发起场景运行”仍未被直接证明。

### I3：阻断性回归，来源断言与 API 投影不匹配

测试正确断言了章节 Canon 快照的 `target_type`、`target_id`、顶层 `source_revision_id` 和初始 `run_status`（约 `310-314`），并断言候选 `scope=chapter`、`scene_id=null`（约 `320-327`）。

但是它还对每个 `canonAfterSeed.items` 断言 `item.source_revision_id` 等于 accepted `ChapterRevision`（约 `324-327`）。当前后端 `CanonCandidateRead` 只有列表顶层 `source_revision_id`；`_candidate_projection` 也只返回 `source_identity`，不返回候选项 `source_revision_id`。Pydantic 会忽略该额外字段，因此运行时 `item.source_revision_id` 为 `undefined`，该断言必然失败。需要改为断言顶层 source revision，或先修改 API 契约后再断言候选项字段；本 Task 禁止修改生产 API，因此当前测试不能通过。

此外，提交三种决策后没有重新读取并断言 `canon.run_status === "accepted"`，只断言候选 CSS 状态和正式 Canon 文本，未完整证明面板显示的当前 Canon run 终态。

### I4：已修复为诚实记录

修复报告明确把真实 Playwright 的 `Error: spawn EPERM` 记录为环境阻断，并说明没有行为级 RED/GREEN 证据，未声称浏览器通过。该项不再存在“把进程启动失败冒充测试 RED”的报告问题；但真实行为仍需在可启动子进程的环境验证。

### I5：主要新增 fixture 已修复，Canon fixture 仍不幂等

`seed_plan_candidate` 现在按 `source_run_id` 找回候选，并仅在不存在 `run_waiting_feedback` 事件时推进 fencing/event；`seed_chapter_review` 按 `review_run_id` 找回 staged revision，并同样避免重复事件。主旅程重复调用两者并比较 revision id，覆盖了这两条路径。

但 `seed_canon_candidates` 仍每次调用 `upsert_canon_candidates` 后无条件递增 `write_fencing_token` 并追加 `run_waiting_feedback` 事件（约 `304-357`），没有按 run identity 检查已有 Canon waiting 事件。重复调用虽可能复用候选行，却会增加 fencing token 和事件序号，违反 fix report 所述“重复调用不增加 event/fencing”的完整要求。应为 Canon fixture 增加同样的 get-or-create/event guard 与重复调用测试。

### M1：部分修复，诊断内容存在语义错误

fixture 失败和 active run 轮询失败现在会调用 `diagnose --chapter-id`，输出 phase、run id、pending decision、last event sequence 和 outbox 列表；这解决了原先完全没有诊断上下文的问题。

不过 `diagnose_chapter` 使用 `RunOutboxRecord.delivery_status != "delivered"`（约 `552-560`）。当前 outbox 消费者使用的是 `consumed`/`published` 等状态，并不存在统一的 `delivered` 终态；因此已消费记录也会被列入 `unconsumed_outbox`，诊断字段存在但语义不准确。应按实际消费状态排除已消费记录，并覆盖 `run_id is None` 的查询条件。

### M2：已修复

`sceneRunId` 对 `data-full-run-id` 增加了 UUID 格式非空断言（约 `88-91`），不会再把空 run id 传给 fixture CLI。

## 静态验证

- `frontend/npm run typecheck`：通过。
- `backend/.venv/Scripts/python.exe -m py_compile app/db/e2e_fixtures.py`：通过。
- `frontend/node_modules/.bin/playwright.cmd test tests/chapter-workflow.spec.ts --list`：列出 3 个测试。
- 真实 Playwright 未执行到浏览器/测试断言，仍因环境 `Error: spawn EPERM` 阻断；不能据此宣称主旅程通过。

## Verdict

**REQUEST_CHANGES**

当前不能批准：I3 的候选来源断言与现有 API 返回契约冲突，会使主旅程确定性失败；I2 仍缺第二场阻断期间的 UI 不发起运行证据；I5 的 Canon fixture 重试仍会重复 fencing/event；M1 诊断的 outbox “未消费”分类不准确。修复这些问题并在允许浏览器启动的环境取得真实 Playwright 结果后再验收。
