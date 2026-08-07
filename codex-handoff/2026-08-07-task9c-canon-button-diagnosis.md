# Task 9C：章节级 Canon 提取按钮诊断

## 现象

章节级 Story Bible 测试在接受章节版本后，等待 btn-canon-start 可用；按钮仍保持 disabled。

## 证据与状态链

- backend/app/db/e2e_fixtures.py:158 的 seed_chapter_accepted 通过 commit_chapter_version 接受章节版本。该领域事务会设置 Chapter.accepted_chapter_revision_id，并写入 chapter_revision.accepted outbox。
- Playwright 配置启动 E2E Worker；Worker 消费该 outbox 后，按当前 accepted chapter revision 自动创建章节 Canon run。
- backend/app/api/canon.py 的章节候选读取按当前 accepted revision 查询最新 Canon run，即使该 run 仍是 queued/running 且尚无候选，也会返回 source_revision_id、run_id 和 run_status。
- frontend/src/features/storybible/StoryBiblePanel.tsx 的 refresh 根据服务端 run_id 调用 getRun；runInFlight 将 queued、running、waiting_feedback、pending_clarification、paused 视为活动状态；btn-canon-start 在 runInFlight 为真时禁用。

因此，测试失败的实际状态不是 accepted revision 没有写入，而是章节 accepted 后已经存在自动 Canon run。测试仍按旧契约假设可以再手动创建第二个 run，和当前来源级幂等/并发语义冲突。

## 最小修复建议

修复应落在 E2E fixture/测试流程，不应放开 UI 或放宽 API：

1. 章节 accepted 后先读取 /api/chapters/{chapter_id}/canon-candidates，取得当前 run_id。
2. 使用 seed-canon-candidates --run-id <当前 run_id> 播种候选，再刷新面板。
3. 只有在确实没有活动 run 的场景，才点击 btn-canon-start 创建手动 run；章节级自动 run 不应重复创建。

保留 btn-canon-start 在活动 run 期间 disabled，才能阻止自动/手动 Canon 重复创建。
