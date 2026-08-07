# Chapter Workbench Task 5 实施 Brief

## 目标

落地阶段 3 的章节聚合、章节审校、章节版本接受/回滚和版本历史，使章节工作流在所有计划场景具备有效 accepted SceneRevision 后进入 staged ChapterRevision，并能在存在 stale、out_of_sync 或 handoff 冲突时阻断接受。

## 当前已知底座

- Task 1-4 已完成并通过主 agent 复验：计划接受、accepted-plan outbox、场景顺序队列、场景基线、逐场决策、反馈影响闭包和 workflow read 已存在。
- `ChapterAggregator`、`ChapterReviewAgent`、`chapter_orchestration` 已有部分基础，但聚合边界、章节审校持久化、章节决策路由和 Worker wiring 尚未形成完整闭环。
- 当前 `ChapterGraph` 默认构造没有可靠注入 `ChapterAggregator`；`RunWorker._process_one()` 尚未把章节审校/聚合结果完整写入章节版本工作流。

## 实施范围

1. **先写 RED 回归测试**
   - 未全部接受场景时，聚合不得创建 ChapterRevision。
   - 所有场景 accepted 后，只能按 accepted plan 的固定 scene link 和 accepted SceneRevision 创建 staged ChapterRevision。
   - stale、out_of_sync、handoff 不匹配、场景基线变化时，章节版本接受必须被阻断。
   - ChapterReviewAgent 输出的 review issues 必须持久化到章节版本/workflow 可读取结构。
   - 章节接受必须使用服务端产生的 `chapter_revision_id`，CAS 失败不能改写版本。
   - 章节接受发布 `chapter_revision.accepted` outbox，重复消费幂等。
   - 回滚必须复制目标 accepted 版本的固定 scene revision 列表，产生新的 staged revision，不能直接改写历史版本。
   - 版本历史必须返回来源场景映射、状态、审校摘要和当前 accepted 指针。

2. **最小实现**
   - 修正 `aggregate_chapter_revision()` 的 accepted plan/scene mapping 约束和固定 scene list。
   - 完成 `commit_chapter_version()`、`rollback_chapter_revision()` 的 stale/CAS/版本状态校验和 outbox。
   - 为 `ChapterReviewAgent` 增加可持久化的章节审校结果适配，保留 Fake provider 的确定性行为。
   - 接通章节 review/aggregate 图节点和 Worker 持久化路径；章节审校不能调用 WritingAgent，也不能在场景未完成时运行。
   - 补齐章节 workflow schema/API：章节 revision、review issues、历史、接受和回滚的权威读取与命令入口。

3. **GREEN / 重构**
   - 运行 Task 5 focused tests、Task 1-4 regression suite、services/API suite、Ruff 和 compileall。
   - 只在行为通过后重构重复查询/状态映射，保持旧场景编辑、版本比较、回滚和 Canon API 回归。

## 不在本任务范围

- 不实现完整 Canon/Story Bible 自动衔接和最终 Playwright 旅程，那是 Task 6。
- 不删除旧场景编辑、版本比较、回滚或 Canon 兼容接口。
- 不把章节审校伪装成场景审校，也不让聚合器调用模型。

## 退出标准

- 所有计划场景具备有效 accepted SceneRevision 后才生成 staged ChapterRevision。
- 存在 stale、out_of_sync 或 handoff 冲突时不能接受章节版本。
- 章节接受后 `chapter_sync_status=in_sync`，并发布可重放的 `chapter_revision.accepted` outbox。
- 章节版本历史和回滚结果可通过 workflow/API 读取，旧 accepted 版本不被覆盖。
- 子 agent 提供测试、变更说明和验证证据；主 agent 独立复跑并处理所有 Critical/Important 问题。
