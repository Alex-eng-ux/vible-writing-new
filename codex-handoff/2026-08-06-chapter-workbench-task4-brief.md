# 任务 4 复核 Brief：场景队列与逐场审阅

本任务承接已接受的章节计划，落地计划后的场景队列与逐场决策，不改变章节规划契约和旧场景编辑能力。

## 必须满足

1. Worker 按 `chapter_plan_scene_links.sort_order` 严格推进场景；当前场景未具备作者接受的有效 revision 时，不得跳过、插入或推进后续场景。
2. 每个场景运行必须绑定当前 accepted `plan_revision_id`、对应 `SceneBrief` 映射和 `base_scene_revision_id`；旧计划、非当前计划场景或过期基线必须被服务端拒绝。
3. accepted plan 的 outbox、Worker 重启和重复投递必须幂等：重复消费不创建重复场景运行，且恢复从 accepted pointer 和固定场景映射开始。
4. 场景 accept/feedback/cancel 必须通过既有运行决策命令；场景反馈只影响当前场景及其下游影响闭包，并将受影响旧状态以权威字段暴露为 stale/superseded/blocking。
5. `ChapterWorkflowRead` 必须返回按计划顺序的场景队列、当前运行、accepted revision、阻断原因、affected/stale 状态；前端不得自行按“最新记录”推断队列。
6. 保留旧场景编辑、运行、版本和回滚能力，不新增并行生成。

## 验收重点

- API/领域测试覆盖顺序阻断、计划/场景归属、基线校验、反馈影响闭包和幂等恢复。
- Worker 测试覆盖 accepted plan 后首场景、接受后下一场景、已有 accepted revision 基线和重复 tick/outbox replay。
- 运行 focused pytest、Ruff 和 compileall；浏览器 E2E 若受当前环境 `spawn EPERM` 阻断，必须如实记录。

## 全局约束

- 不修改用户已有的无关工作区改动。
- 不提交 Git commit。
- 非直观的版本/CAS/fencing/幂等逻辑补充中文注释。
- 只把已验证的结果写入报告和开发日志。
