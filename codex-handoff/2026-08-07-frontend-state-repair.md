# 前端状态修复交接

日期：2026-08-07

## 范围

修复 Chapter Workbench 场景编辑器的两个状态恢复问题：

- rollback 产生 staged revision 后，刷新和后续保存必须继续使用 accepted revision 作为基线。
- 重新加载或切换回场景时，章节 workflow 返回的 active run 必须恢复到 RunPanel。

## TDD 证据

先增加了 Playwright 回归断言：

- `rollback follow-up save uses the authoritative accepted revision` 断言后续 ChangeSet 的 `base_scene_revision_id` 指向 accepted 版本。
- `reload hydrates a scene active run into RunPanel` 断言 waiting feedback 运行恢复后显示 RunPanel、run id 和接受按钮。

目标 Playwright 命令已尝试运行，但当前环境没有监听 PostgreSQL/前后端 WebServer，等待健康检查 124 秒后退出（exit code 124）；因此未获得断言级 RED/GREEN 结果。Playwright `--list` 成功收集上述测试，证明测试源码可解析。

## 实现

`frontend/src/app/page.tsx` 现在按以下顺序解析基线：

1. 章节 workflow 中当前 scene 的 `accepted_revision_id`；
2. 非 accepted-plan 场景使用 Scene 的 accepted 指针或最近一条 `status=accepted` revision；
3. 不再把 revisions 最后一条 staged 记录作为 accepted detail。

`refreshSceneLatest` 会同时重新读取章节 workflow，确保 rollback 后读取到服务端最新 accepted 指针。`selectScene` 在清理旧运行状态后将 `workflow.active_run` hydrate 到 `activeRun`，从而恢复 SSE 和决策控件。

## 验证

- `npm run typecheck`：通过。
- `playwright test tests/editor.spec.ts --list`：通过，目标测试被收集。
- 目标浏览器测试：被环境 WebServer/数据库健康检查阻塞，未宣称通过。

## 后续验收

在 PostgreSQL `novel_e2e` 与 backend、frontend、Worker 可启动的环境中运行：

```powershell
cd E:\vible-writing-new\frontend
& .\node_modules\.bin\playwright.cmd test tests/editor.spec.ts -g "rollback follow-up save|reload hydrates a scene active run" --reporter=list
```

确认 rollback 后 ChangeSet body 的 `base_scene_revision_id` 为 accepted revision，并确认重开场景后 `btn-run-accept` 可见。
