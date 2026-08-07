# 冲突保存修复交接报告

## 根因

过期基线保存失败后，enterConflict() 会调用 refreshSceneLatest()。刷新 accepted 版本时，页面把新的 acceptedDetail.content 直接作为 ManuscriptEditor 的 doc，Tiptap 随后执行 setContent，将作者尚未保存的 localText 覆盖为服务器文本。此时本地草稿与新 baseline 相同，后续覆盖提交无法再使用作者原始文本。

同时，场景基线选择优先使用 workflow.scenes[].accepted_revision_id。该指针在场景版本刚推进时可能短暂滞后，导致冲突覆盖请求仍携带旧 revision，服务端返回 SCENE_STALE。

## 修改文件

- frontend/src/app/page.tsx
  - 新增独立的 editorDoc 状态，编辑器内容不再直接受 acceptedDetail 刷新驱动。
  - refreshSceneLatest 支持在冲突刷新 accepted 基线时保留本地编辑器草稿。
  - enterConflict 使用非同步模式；丢弃冲突时再恢复到 accepted 文档。
  - 场景编辑基线优先取 revisions 中最后一条 accepted 记录，避免 workflow accepted 指针滞后。
- frontend/tests/editor.spec.ts
  - 在冲突面板出现后增加断言，确保编辑器仍保留“我的本地文本”。

## TDD 验证

RED:
npx playwright test tests/editor.spec.ts -g "过期基线冲突展示并可覆盖提交" --reporter=list

失败证据：
Expected substring: "覆盖提交"
Received string: "SCENE_STALE: change set baseline is stale"

加入本地草稿保留断言后，进一步定位为：
Expected substring: "我的本地文本"
Received string: "服务器端新文本"

GREEN:
npm run typecheck
结果：通过。

npx playwright test tests/editor.spec.ts -g "过期基线冲突展示并可覆盖提交" --reporter=list
结果：1 passed。

npx playwright test tests/editor.spec.ts --reporter=list
结果：10 passed，包含冲突覆盖、手动回滚及回滚后继续保存用例。
