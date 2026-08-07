# Task 8 修复简报

## 目标

修复终审报告中的三个 Important 问题，继续满足章节工作台 v2 计划书第 9.1 至 9.3 节。

## 必须完成

1. 场景工作台统一消费 `GET /api/chapters/{chapter_id}/workflow`：选择场景、渲染章节计划、启动场景运行的前置校验都使用同一 `ChapterWorkflowRead` 快照；移除前端 `chapterPlans` / `getChapterPlan` 的权威语义。后端只读 GET 别名可保留为兼容，但新 UI 不得依赖它。
2. 重写 `frontend/tests/editor.spec.ts` 中仍点击“生成章节计划”的旧用例，改为验证当前支持的章节工作台流程和场景编辑器回归，不恢复旧 POST 入口。
3. Story Bible 候选读取必须绑定当前 accepted revision，并明确返回当前 Canon run（包括 queued/running 且尚无候选的情况）。前端不得从任意历史候选的第一个 `generation_run_id` 反推当前运行；scope 切换、历史候选+新 accepted revision、无候选活动运行都要有回归覆盖。

## TDD 要求

先补能复现上述三个问题的测试，再修改生产实现。测试无法在当前环境启动浏览器时，仍需保留测试并在报告中明确 `spawn EPERM` 阻断；后端/API 测试必须实际运行。

## 验收命令

- `backend/.venv/Scripts/python.exe -m pytest -q` 覆盖新增 API/Canon/章节 workflow 测试
- `backend/.venv/Scripts/ruff.exe check` 覆盖改动的后端文件
- `frontend/npm run typecheck`
- 尝试 Playwright 相关用例；若仍 `spawn EPERM`，如实记录

## 交付

不要创建提交。完成后写报告 `codex-handoff/2026-08-07-chapter-workbench-task8-fix-report.md`，说明改动、TDD 红绿证据、测试命令与输出、未解决问题。
