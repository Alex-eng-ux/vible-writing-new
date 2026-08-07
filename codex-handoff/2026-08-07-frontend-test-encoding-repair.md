# 前端测试编码修复交接（2026-08-07）

## 根因

`frontend/tests/editor.spec.ts` 和 `frontend/tests/runs.spec.ts` 中新增的 Playwright 场景使用了 UTF-8 中文被错误解码后的乱码字符串。导航夹具实际使用的标题是“场景”，版本正文/状态分别应为“第一版本”“第二版本”“连续修改”“等待反馈”“已保存并提交版本”。乱码导致 `openScene` 无法定位场景，或状态断言无法匹配。

## 修改范围

- `frontend/tests/editor.spec.ts`
  - 将回滚、场景运行重载用例中的乱码场景标题改为“场景”。
  - 将版本正文和保存状态断言改为正确 UTF-8 中文。
- `frontend/tests/runs.spec.ts`
  - 将活动运行恢复夹具标题、场景导航标题和运行状态断言改为正确 UTF-8 中文。

未修改生产代码，也未增加超时或改变测试流程。

## 验证

- `npx playwright test tests/editor.spec.ts -g "rollback follow-up save uses the authoritative accepted revision" --reporter=list`
  - 失败（3.3s）：编码问题已消失，但后续保存请求的 `base_scene_revision_id` 为 `e29aafff-d7bd-41b4-8c97-626512e25fda`，测试期望回滚目标 `de94fd77-2ba6-4fae-b894-908741081fd3`。这是独立的 accepted 基线状态契约问题，超出本编码修复范围。
- `npx playwright test tests/editor.spec.ts -g "reload hydrates a scene active run into RunPanel" --reporter=list`
  - 通过：`1 passed (15.3s)`。
- `npx playwright test tests/runs.spec.ts -g "reopening a scene hydrates its active run and decision controls" --reporter=list`
  - 通过：`1 passed (14.9s)`。

`rg` 检查确认上述乱码字串已不再出现在两个测试文件中，`git diff --check` 无错误。
