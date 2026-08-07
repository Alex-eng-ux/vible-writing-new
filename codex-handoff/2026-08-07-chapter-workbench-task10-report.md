# Chapter Workbench Task 10 实施报告

## 改动文件

- `frontend/tests/chapter-workflow.spec.ts`
  - 新增章节工作台主旅程 Playwright 测试：章节意图 -> `new_chapter` 规划 -> 计划候选接受 -> 两个场景按序运行/接受 -> 章节审校 staged revision/问题展示/接受 -> 章节级 Canon 候选 confirm/reject/defer 可见状态。
  - 新增确定性 fixture 调用、workflow/run 轮询辅助函数；没有在测试中使用固定 `sleep`。
- `backend/app/db/e2e_fixtures.py`
  - 新增 `seed-plan-candidate --run-id`：创建待接受章节计划候选并推进规划运行到 `waiting_feedback`。
  - 新增 `seed-chapter-review --run-id`：聚合 staged chapter revision，写入审校问题/摘要并推进章节审校运行到 `waiting_feedback`。
  - 仅用于 E2E 确定性数据，不修改生产业务流程。

## TDD 执行状态

先加入主旅程测试后尝试运行真实 Playwright，但环境未允许进入行为断言：

```text
命令：
frontend\\node_modules\\.bin\\playwright.cmd test tests/chapter-workflow.spec.ts -g "章节工作台主旅程" --project=chromium

输出：
Error: spawn EPERM
```

命令在 Playwright 启动浏览器/webServer 前返回 `Error: spawn EPERM`，未取得行为级 RED/GREEN 证据；该环境错误不作为 TDD RED。

## GREEN/静态验证

```text
命令：
frontend\\npm run typecheck

输出：
> novel-studio-frontend@0.1.0 typecheck
> tsc --noEmit
```

```text
命令：
backend\\.venv\\Scripts\\python.exe -m py_compile app/db/e2e_fixtures.py

输出：
(无输出，退出码 0)
```

```text
命令：
frontend\\node_modules\\.bin\\playwright.cmd test tests/chapter-workflow.spec.ts --list

输出：
Listing tests:
  [chromium] chapter-workflow.spec.ts:64:5 new_chapter 规划读取意图并刷新 workflow
  [chromium] chapter-workflow.spec.ts:103:5 章节工作台从章节入口展示 workflow 状态
  [chromium] chapter-workflow.spec.ts:135:5 章节工作台主旅程：计划接受、场景队列、章节审校与 Canon 来源
Total: 3 tests in 1 file
```

```text
命令：
backend\\.venv\\Scripts\\python.exe -m app.db.e2e_fixtures --help

输出（关键部分）：
{seed-plan,seed-scene-accepted,seed-chapter-accepted,seed-plan-candidate,seed-chapter-review,seed-canon-candidates,seed-canon-entries,advance}
```

## Playwright 阻断与遗留问题

- `npm exec playwright ...` 与直接调用 `node_modules/.bin/playwright.cmd ...` 的真实执行均在启动阶段返回 `Error: spawn EPERM`；无法声称浏览器主旅程通过。
- 因上述环境阻断，未能实测 SSE 更新、UI 接受决策后的服务端 revision 指针和 Canon 三种状态；这些路径已由测试动作与确定性 fixture 编排，但需要在允许子进程/浏览器启动的环境重新运行。
- 未修改 `frontend/src/app/page.tsx`、后端领域服务或 Canon 语义；未创建 commit。

## Task 10 Review 修复追加

- 规划测试改为章节工作区 UI 填写意图并点击 `btn-start-chapter-planning`，监听请求体确认 `request_type=new_chapter`；主旅程不再直接 POST 规划运行。
- 计划候选未接受前断言 workflow `scenes=[]`、场景运行 POST 数为 0、场景队列仍提示先接受计划且 UI 无审校按钮；接受后再读取有序场景队列。
- 章节接受后先读取章节级 Canon candidates，复用 outbox/Worker 已创建的 run；仅在明确无 run 且按钮可用时点击。断言 accepted `ChapterRevision` 对应 `source_revision_id`、`target_type=chapter`、候选 `scope=chapter`、`scene_id=null`、run 状态和来源版本。
- `seed-plan-candidate` 与 `seed-chapter-review` 按 `run_id` get-or-create，重复调用复用 revision；已有 waiting event 时不增加 fencing token 或追加事件。
- 增加 `diagnose --chapter-id`，fixture 失败包装会输出 phase、run id、pending decision、最后事件序号和未消费 outbox。
- `sceneRunId` 对 `data-full-run-id` 增加非空 UUID 格式断言。

### 修复验证

```text
frontend\\npm run typecheck
通过（tsc --noEmit）

backend\\.venv\\Scripts\\python.exe -m py_compile app/db/e2e_fixtures.py
通过（退出码 0）

backend\\.venv\\Scripts\\python.exe -m pytest tests/runtime/test_e2e_fixtures.py -q
通过：2 passed（仅既有 fixture 回归；含若干依赖弃用警告）

frontend\\node_modules\\.bin\\playwright.cmd test tests/chapter-workflow.spec.ts --list
通过：3 tests listed

frontend\\node_modules\\.bin\\playwright.cmd test tests/chapter-workflow.spec.ts -g "章节工作台主旅程" --project=chromium
Error: spawn EPERM
```

本轮没有取得浏览器行为级 RED/GREEN 证据；`spawn EPERM` 仅记录为环境阻断，不作为 TDD RED。未创建 commit。
