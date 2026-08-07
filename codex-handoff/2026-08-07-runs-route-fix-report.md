# runs route mock 修复交接报告

## 结果

DONE（目标 route mock 修复及测试收尾竞态均已完成）。

## 红灯复现

命令：

```powershell
cd E:\vible-writing-new\frontend
npx playwright test tests/runs.spec.ts --reporter=line --workers=1
```

首次沙箱执行因 Playwright 子进程启动受到 `spawn EPERM` 阻断。提升权限后测试实际启动，修复前结果为 `10 passed, 1 failed`；失败曾表现为 workflow route callback 中 `route.fetch: Test ended`，并在后续运行中复现 `apiResponse.json: Response has been disposed`。

## 改动

文件：`frontend/tests/runs.spec.ts`

最初将 4 处在 `await response.json()` 后复用原始 response 的：

```ts
await route.fulfill({ response, json: body });
```

改为显式复制响应元数据并回填 JSON：

```ts
await route.fulfill({
  status: response.status(),
  headers: response.headers(),
  json: body,
});
```

真实提升权限回归进一步发现，页面结束时仍可能有后台 workflow 路由回调在途，导致
`route.fetch` 或独立 `APIRequestContext` 在测试收尾阶段被销毁。最终在 `runs.spec.ts`
增加统一的 `test.afterEach` 路由清理：

```ts
await page.unrouteAll({ behavior: "ignoreErrors" });
```

该清理只处理测试结束时已无法完成的后台 mock 回调，不改变业务请求或断言；4 个 workflow
mock 仍使用显式 `status`、`headers`、`json` 回填。

## 绿灯验证

目标用例单独运行：

```powershell
cd E:\vible-writing-new\frontend
npx playwright test tests/runs.spec.ts -g "场景运行前置检查使用刷新后的 workflow plan 指针" --reporter=line --workers=1
```

结果：`1 passed`。

完整套件：

```powershell
cd E:\vible-writing-new\frontend
npx --no-install playwright test tests/runs.spec.ts --reporter=line --workers=1
```

结果：`11 passed`。

TypeScript：

```powershell
cd E:\vible-writing-new\frontend
npx tsc --noEmit
```

结果：通过（退出码 0）。

差异空白检查：

```powershell
cd E:\vible-writing-new
git diff --check
```

结果：通过；仅报告工作区已有的 LF/CRLF 警告。

## 风险与限制

在加入测试收尾路由清理前，完整 `runs.spec.ts` 在不同轮次出现 10/11 或 8/11 通过，失败点在多个 workflow 用例间变化，错误为 `route.fetch: Test ended`、`apiResponse.json: Response has been disposed` 或 `Request context disposed`。清理后完整套件稳定通过；提升权限环境之外仍可能受 `spawn EPERM` 限制。

未提交 commit。
