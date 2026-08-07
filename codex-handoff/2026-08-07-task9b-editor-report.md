# Task 9B 编辑器回归报告

## 改动

- 仅修改 `frontend/tests/editor.spec.ts`。
- 删除旧的“生成章节计划”按钮、`init-plan` 和旧计划正向断言。
- 新增确定性 `seedAcceptedPlan` 测试 fixture：直接播种 accepted plan，不调用旧 `POST /api/chapters/{chapter_id}/plan`。
- 回归用例先通过 UI 打开场景，断言章节计划面板显示 `accepted` 及 fixture 大纲，监听确认页面没有读取旧 `GET /api/chapters/{chapter_id}/plan`，再在编辑器输入正文并保存。

## TDD 记录

### RED

先在未播种 accepted plan 的场景 fixture 上加入 `chapter-plan-panel` 必须包含 `accepted` 的断言，证明旧测试 setup 不能证明 accepted-plan 工作流。

命令：

```text
npx playwright test tests/editor.spec.ts --grep "scene workspace loads plan state from workflow and keeps the editor usable" --reporter=line
```

原始结果：

```text
Error: spawn EPERM
```

浏览器进程在当前环境启动前即被 `spawn EPERM` 拦截，因此没有得到断言级失败；该环境阻断原样保留。

### GREEN

补充 `seedAcceptedPlan(chapterId)` fixture 后，保留 accepted 状态断言，并完成编辑器输入与保存断言。未修改生产代码。

## 验证

命令：

```text
npx playwright test tests/editor.spec.ts --list
```

原始结果：

```text
Listing tests:
  [chromium] › editor.spec.ts:148:5 › 创建资源并通过 UI 编辑保存首稿
  [chromium] › editor.spec.ts:193:5 › 编辑已有版本并比较两个版本
  [chromium] › editor.spec.ts:221:5 › 过期基线冲突展示并可覆盖提交
  [chromium] › editor.spec.ts:251:5 › 手动回滚到目标版本
  [chromium] › editor.spec.ts:282:5 › 所有命令请求都携带 Idempotency-Key
  [chromium] › editor.spec.ts:299:5 › context menu deletes a project after confirmation
  [chromium] › editor.spec.ts:312:5 › scene workspace loads plan state from workflow and keeps the editor usable
Total: 7 tests in 1 file
```

命令：

```text
npx playwright test tests/editor.spec.ts --grep "scene workspace loads plan state from workflow and keeps the editor usable" --reporter=line
```

原始结果：

```text
Error: spawn EPERM
```

命令：

```text
npm run typecheck
```

原始结果：

```text
> novel-studio-frontend@0.1.0 typecheck
> tsc --noEmit
```

退出码为 0。

命令：

```text
git diff --check -- frontend/tests/editor.spec.ts
```

原始结果：无空白错误（仅提示工作树的 LF/CRLF 转换警告）。

## 未解决事项

- 当前环境无法启动 Playwright 浏览器/其 webServer 子进程，具体错误为 `Error: spawn EPERM`；需在允许创建子进程的环境中重跑聚焦用例和完整 `editor.spec.ts`。
- 本任务未修改生产代码，也未恢复任何旧计划初始化入口。
