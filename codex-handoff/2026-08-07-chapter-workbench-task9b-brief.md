# Chapter Workbench Task 9B：编辑器回归 brief

## 目标

删除已经不存在的“生成章节计划”正向断言，改为验证 accepted plan 管理下的场景仍能打开、编辑和保存。

## 共享约束

- 使用 TDD：先补一个能证明缺陷的失败测试，再改生产代码；报告必须记录 RED/GREEN 命令和结果。
- 不恢复旧的 POST /api/chapters/{chapter_id}/plan 初始化入口，不让前端把旧 /plan GET 当作章节工作台权威状态源。
- 不删除用户已有改动；只修改任务范围内的文件。
- 所有版本来源、run_id、幂等和 accepted pointer 语义必须与现有后端契约一致。
- 每个 agent 写自己的报告文件，包含改动、测试、未解决事项；不要提交 commit。

## 范围

所有权：frontend/tests/editor.spec.ts，必要时只读调整测试 fixture。

目标：删除已经不存在的“生成章节计划”正向断言，改为验证 accepted plan 管理下的场景仍能打开、编辑和保存；不依赖固定 sleep，不调用旧初始化 POST。

非目标：不修改生产页面行为，不绕过 UI 直接调用 API 代替主动作。

验收：用例可被 Playwright --list 解析；在浏览器可启动环境中应能运行，环境阻断必须原样记录。
