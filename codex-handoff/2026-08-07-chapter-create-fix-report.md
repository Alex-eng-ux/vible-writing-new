# 章节创建故障修复日志

日期：2026-08-07

## 故障范围

验证 Chapter Workbench 的“项目 -> 卷 -> 章节 -> 场景 -> 首稿保存”用户链路，处理章节创建按钮显示 `INTERNAL_ERROR` 的问题。

## 根因

- 章节 POST 本身能够成功写入 `chapters` 表。
- 前端创建成功后立即调用章节列表 GET；运行数据库 `novel` 停在 Alembic 版本 `v1_rc_observability_metadata`，而当前 ORM 查询需要 `chapter_revisions.review_issues`、`review_summary` 和 `review_run_id`。
- 因旧 schema 缺少字段，章节列表 GET 返回 500，前端 catch 后把已成功的创建显示成失败。
- 首稿保存还存在输入刚完成时 React `localText` 尚未刷新的一拍延迟；保存逻辑只比较 state，导致编辑器已有文本时误报“没有需要保存的变更”。

## 已完成

- 对 `novel` 数据库执行 `alembic upgrade head`，版本推进到 `b2c3d4e5f6a7`，补齐章节审校字段。
- 在 `frontend/src/app/page.tsx` 增加从编辑器实例读取当前文本的保存兜底，并同步覆盖冲突提交路径。
- 保留现有 accepted 基线和冲突状态行为，不直接改写服务端 accepted 版本。

## 验证

- `npm run typecheck`：通过。
- Playwright：`tests/editor.spec.ts` 中“创建资源并通过 UI 编辑保存首稿”：1 passed。
- 直接 API 验证：创建章节返回 201，随后章节列表返回 200。
- 数据库验证：`alembic_version=b2c3d4e5f6a7`，章节审校字段存在。

## 当前不足与风险

- Playwright 启动 E2E Worker 时仍偶发输出 `run_outbox_records` 不存在的启动竞态堆栈；本次用例最终通过，但该日志污染说明服务启动与 E2E schema 初始化仍需进一步串行化。
- 同一幂等键并发 claim 的 `UniqueViolation` 映射风险仍由后端审计发现，尚未在本次 UI 故障修复中处理。

## 后续

- 将数据库迁移作为开发服务启动前的明确门禁，避免旧库在 `/ready` 返回成功后才暴露业务查询错误。
- 补充同一幂等键并发请求的 API 回归测试和错误映射。
