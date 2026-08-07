# Chapter Workbench Task 7 修复报告

## 修复范围

本次处理最终全分支审查指出的两项 Important 问题：删除迁移后不应继续存在的章节计划初始化 POST 入口；确保自动章节接受与手动 Story Bible 章节 Canon 提取对同一 accepted 修订只使用一个活动运行。

## 已完成改动

- 删除 `POST /api/chapters/{chapter_id}/plan` 路由及其旧的前端调用、API 客户端导出和章节页面处理器。
- 保留 `GET /api/chapters/{chapter_id}/plan` 只读别名。
- 更新场景工作台计划空状态和缺少 accepted plan 的提示，不再指向不存在的“生成章节计划”按钮。
- 章节 Canon 创建入口按 `(chapter_id, canon_source_revision_id)` 查询活动状态；对于 queued、running、waiting_feedback、pending_clarification、paused 运行直接复用快照。
- 手动章节 Canon 创建与自动 accepted outbox 消费共享事务 advisory lock，避免查询与插入之间的并发重复。
- Story Bible 面板在活动 Canon 运行存在时禁用“提取候选”，并在刷新和作用域切换时清理过期运行状态。
- 将旧计划初始化测试替换为“GET 保留、POST 返回 405”的回归测试；新增自动运行与手动请求复用同一 `run_id` 且数据库仅保留一条记录的回归测试。
- 调整跨运行候选校验测试，使第一个运行明确进入终态后再创建第二个运行，保持测试语义与来源级去重规则一致。

## 验证结果

- `backend/.venv/Scripts/python.exe -m pytest -q tests/api/test_chapter_plan_init.py tests/api/test_canon_api.py tests/runtime/test_chapter_workflow_task6.py`：30 passed。
- `backend/.venv/Scripts/ruff.exe check app/api/chapters.py app/services/canon_runs.py tests/api/test_chapter_plan_init.py tests/api/test_canon_api.py`：All checks passed。
- `frontend/npm run typecheck`：`tsc --noEmit` 通过。
- `npm exec playwright test tests/chapter-workflow.spec.ts --grep "new_chapter"`：环境在启动阶段返回 `Error: spawn EPERM`，未能执行浏览器测试；该阻断已如实保留。

## 当前关注点

未发现本次改动引入的已知功能性失败。前端没有独立的 Story Bible 单元测试脚手架；相关按钮状态通过类型检查和后端回归覆盖，浏览器端验证仍受 `spawn EPERM` 环境限制。
