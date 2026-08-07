# CJK 编辑器保存回归验收

## 根因判断

独立运行和重复运行均表明产品保存逻辑没有稳定复现“没有需要保存的变更”。问题位于 Playwright 测试辅助：`keyboard.type` 对 CJK/IME 依赖浏览器键盘布局，并可能在紧接保存点击时让 DOM 输入与 Tiptap `onUpdate` 上报出现时序窗口。编辑器生产代码不需要修改。

## 已完成

- `frontend/tests/editor.spec.ts` 的 `typeInEditor` 改用 `page.keyboard.insertText(text)`，直接发送最终 `input` 事件，避免 CJK 键盘合成状态差异。
- 保留现有回滚基线断言修复，不改变产品 accepted 基线行为。

## 验证

- `npx playwright test tests/editor.spec.ts -g "rollback 后仍按 workflow accepted 指针渲染基线并保存" --reporter=list`：1 passed。
- 同一用例 `--repeat-each=12`：前 10 次 passed；后 2 次分别为 `Failed to fetch` 和 `ECONNREFUSED 127.0.0.1:3000`，属于重复启动环境的服务生命周期/基础设施失败，不是 CJK 输入断言失败。
- 完整 `editor.spec.ts`：6 passed，4 个与本任务无关的既有冲突/导航断言失败；未观察到“没有需要保存的变更”失败。

## 未完成/风险

当前仍需父任务在干净服务进程下完成全量 Playwright；重复执行期间 Next/API 服务提前退出会造成 `Failed to fetch`，不能视为编辑器功能通过。
