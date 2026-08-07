# Chapter Workbench Task 11 实施 Brief

## 目标

对章节工作台二阶落地后的后端做全量 `mypy` 与静态类型验收；先验证当前失败，再只修复本次工作区变更或其直接依赖暴露的真实类型问题，确保不以放宽配置、忽略错误或删除测试掩盖问题。

## 文件边界

- 主要编辑：`backend/app` 与必要的 `backend/tests` 类型适配文件。
- 不修改前端、数据库业务语义或计划书；不删除既有测试。
- 如果发现与本次章节工作台无关的历史类型债务，保留并在报告中分组说明，不做无关重构。

## 必须完成

1. 在 `backend` 虚拟环境执行全量 `python -m mypy app`，记录原始结果。
2. 对本次章节工作台新增/修改模块的类型错误逐项修复，保持运行时契约、CAS/fencing、幂等和 outbox 语义不变。
3. 运行覆盖修改面的 pytest、全量 Ruff、compileall，并重新执行 `python -m mypy app`；不得通过 `# type: ignore`、改变 mypy 配置或移除检查来“通过”。
4. 报告写入 `codex-handoff/2026-08-07-chapter-workbench-task11-report.md`，包含 RED/修复/GREEN 命令、输出摘要、修改文件和遗留类型债务。
- 不创建 commit。
