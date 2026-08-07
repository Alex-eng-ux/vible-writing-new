# Chapter Workbench Task 9：最终复审阻断项修复 brief

## 目标

依据 `docs/superpowers/specs/2026-08-05-chapter-workbench-v2-design.md` 第 9.1、9.3 和第 11 节，把最终复审中仍影响主流程落地的三项 Important 关闭，保持 ChapterWorkflowRead、版本来源和 Canon 运行状态的一致性。

## 共享约束

- 使用 TDD：先补一个能证明缺陷的失败测试，再改生产代码；报告必须记录 RED/GREEN 命令和结果。
- 不恢复旧的 `POST /api/chapters/{chapter_id}/plan` 初始化入口，不让前端把旧 `/plan` GET 当作章节工作台权威状态源。
- 不删除用户已有改动；只修改任务范围内的文件。
- 所有版本来源、run_id、幂等和 accepted pointer 语义必须与现有后端契约一致。
- 每个 agent 写自己的报告文件，包含改动、测试、未解决事项；不要提交 commit。

## Worker A：工作台统一状态源

所有权：`frontend/src/app/page.tsx`、必要的前端类型/API 只读适配和对应 workflow/编辑器测试。

目标：场景选择、场景运行前置检查、计划摘要均来自同一份 `ChapterWorkflowRead` 快照；场景正文/编辑器详情可以保留场景专属读取，但不得再用 `getChapterPlan` 或独立 `chapterPlans` 作为章节状态源。覆盖 accepted plan 变化、队列阻塞、场景切换等路径。

非目标：不改后端领域事务；不恢复旧计划初始化 API。

验收：至少新增或修正一个失败测试证明场景打开/运行不请求旧 `/plan`，并通过相关前端类型检查和可运行的聚焦测试。

## Worker B：编辑器回归

所有权：`frontend/tests/editor.spec.ts`，必要时只读调整测试 fixture。

目标：删除已经不存在的“生成章节计划”正向断言，改为验证 accepted plan 管理下的场景仍能打开、编辑和保存；不依赖固定 sleep，不调用旧初始化 POST。

非目标：不修改生产页面行为，不绕过 UI 直接调用 API 代替主动作。

验收：用例可被 Playwright `--list` 解析；在浏览器可启动环境中应能运行，环境阻断必须原样记录。

## Worker C：Story Bible Canon 来源绑定

所有权：`backend/app/api/canon.py`、`backend/app/api/schemas.py`、`frontend/src/features/storybible/StoryBiblePanel.tsx`、对应 API/Story Bible 测试。

目标：候选读取明确返回当前 accepted revision 对应的最新 Canon run（含 queued/running 且暂无候选），只返回该 run 的候选；前端按服务端 `run_id`/`source_revision_id` 展示和提交，不能从任意历史候选反推当前运行。覆盖“活动 run 无候选”“历史候选 + 新 accepted revision”“章节/场景 scope 切换”。

非目标：不改变 confirm/reject/defer 的领域语义，不修改场景级 Canon 对全局 Story Bible 的隔离规则。

验收：后端候选来源测试、Story Bible 前端类型检查和相关回归通过；并发/幂等契约保持。

## 合并与复审

主 agent 回收三个报告后，独立检查 diff 和测试证据；每个 worker 都要再经过一个 reviewer。Critical/Important 必须修复并复审，Minor 写入开发日志。最后运行全量后端、Ruff、前端 typecheck、Playwright 列表/执行和 build；不能运行的检查要记录具体环境错误。
