# Task 12 Story Bible E2E 修复报告

日期：2026-08-07

## 任务范围

修复 `frontend/tests/story-bible.spec.ts` 四个 Playwright 旅程在导航树中使用模糊卷名 `hasText: "V"` 导致的 strict-mode 定位失败。保持 Story Bible、Canon 候选、逐条决策和幂等重试的业务断言不变，不修改业务实现。

## TDD RED

为验证失败确实由定位器造成，临时让测试项目名前缀包含大写 `V`，运行完整 spec 后第 2 至第 4 个测试失败：

```text
locator('.tree-label').filter({ hasText: 'V' }).getByRole('button', { name: '展开' }) resolved to 2/3/4 elements
```

匹配项包括历史项目的 `project-toggle-*` 和当前卷的 `volume-toggle-*`。第 1 个旅程因当前项目已展开、其按钮文本已变化而未暴露该问题。RED 复现后已移除临时项目名前缀改动。

## 修复

- `openScene` 现在接收 `projectId`、`volumeId`、`chapterId`、`sceneId`，使用既有稳定 testid：
  - `project-toggle-${projectId}`
  - `volume-toggle-${volumeId}`
  - `chapter-item-${chapterId}` 的父节点内“展开”按钮
  - `scene-item-${sceneId}`
- 四个测试同步传递 `createHierarchy` 返回的卷、章、场景 ID。
- 未修改生产页面、API、数据库迁移或业务断言。

## 验证

命令：

```text
frontend\\node_modules\\.bin\\playwright.cmd test tests/story-bible.spec.ts --project=chromium
```

结果：`4 passed (34.8s)`，四个 Story Bible 旅程全部真实启动 API、E2E worker、Next.js 和 Chromium 后通过。

命令：

```text
frontend\\npm run typecheck
```

结果：退出码 0，`tsc --noEmit` 通过。

## 基础设施观察

一次 RED 重跑期间，Playwright webServer 日志出现 `sqlalchemy.exc.ProgrammingError: relation "run_outbox_records" does not exist`，同时 API 请求收到 `ECONNRESET/ECONNREFUSED`。该现象与 `globalSetup` 重置 E2E 数据库时复用旧 API/worker 进程的启动竞态一致；`backend/app/db/e2e_bootstrap.py` 已通过 `Base.metadata.create_all` 创建该表。随后隔离重跑完整 spec 未再出现缺表告警并通过 4/4。本任务未修改服务复用策略或迁移，避免把一次性测试基础设施竞态混入 Story Bible 选择器修复。

## 遗留

无 Story Bible 测试失败。若后续持续出现缺表告警，应单独处理 Playwright `reuseExistingServer` 与 E2E globalSetup 的进程生命周期，而不是在业务代码中吞掉数据库错误。
