# 前端回滚基线回归修复报告

## 任务结论

已修正 `frontend/tests/editor.spec.ts` 中的回归断言。场景从 `rev2` 回滚到
`rev1` 时，回滚记录是 staged；工作流的 accepted 指针仍为 `rev2`。因此，回滚后
再次保存必须以 `rev2` 作为 `base_scene_revision_id`，而不是以回滚目标 `rev1` 为基线。

生产代码未修改。

## 变更

- 在 `rollback follow-up save uses the authoritative accepted revision` 中保存第二次提交的
  `rev2Id`。
- 将最终 ChangeSet 请求断言从 `base_scene_revision_id: rev1Id` 改为
  `base_scene_revision_id: rev2Id`。

## 验证证据

| 目标 | 命令 | 结果 |
| --- | --- | --- |
| 回滚后保存使用 accepted 基线 | `npx playwright test tests/editor.spec.ts -g "rollback follow-up save" --reporter=list` | 通过，`1 passed (15.2s)` |
| 前端类型检查 | `npm run typecheck` | 通过，`tsc --noEmit` exit 0 |

首次把该用例与相邻用例合跑时，环境发生过 API 连接中断，不能作为产品失败结论。

## 已知独立风险

相邻已有用例 `rollback 后仍按 workflow accepted 指针渲染基线并保存` 单独运行失败：首次对
编辑器输入 CJK 文本后，页面显示“没有需要保存的变更”，历史版本仍为 0。页面快照显示
编辑器视觉内容已更新，但保存状态没有更新。该失败发生在任何回滚步骤之前，和本次
`rev1`/`rev2` 基线断言无关；建议作为 Tiptap/Playwright CJK 输入事件的独立问题排查。
