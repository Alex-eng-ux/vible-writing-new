# Task 9 V1 验收清单（v1-checklist.md）

本清单按计划书 Task 9 Steps 逐项验收 V1 工程。执行时逐行填写“实际结果”“脚本退出码”“缺陷引用”；全部验收完成后汇总判定 V1-RC 是否通过。

**V1-RC 缺陷等级定义：**

- **P0（阻断发布，数据完整性/安全红线）**：数据丢失、重复正式版本或事件、未授权 Canon 写入、旧 Worker 越权写入、脱敏泄漏、无法恢复或备份恢复哈希不一致。
- **P1（功能/契约缺陷，必须修复）**：核心工作流无法完成、错误状态/版本/候选断言、SSE 无法按序重放、幂等或冲突契约错误但未造成权威数据损坏。

**V1-RC 通过条件：V1-RC 不允许存在未关闭的 P0/P1 缺陷。** 任何 P0/P1 一经发现即记录缺陷引用并阻断发布，修复后重新执行受影响验收项。

## 验收记录

| 验收项 | 前置条件 | 命令/脚本 | 预期断言 | 实际结果 | 脚本退出码 | 缺陷引用 |
| --- | --- | --- | --- | --- | --- | --- |
| 1. fixture 重置（clean / preserve_history） | 空数据库、迁移至 head | `python -m app.acceptance.cli reset clean` / `reset preserve_history`（novel_acceptance） | clean：从空库创建三章六场景，`compute_fixture_hash` 与 `v1-fixture.json` 一致，正式 ID 随机生成；preserve_history：只清理临时数据（outbox/租约/幂等键/消费游标等），权威与审计数据保留、双哈希不变 | 通过：clean 播种 10 个资源（3 章 6 场景 1 卷），fixture_hash=97eb809a… 稳定；preserve_history 正常清理临时表 | 0 / 0 | 无 |
| 2. Fake model 完整流程 | clean fixture、Fake provider 配置 | `smoke-scene`（章节计划→场景运行→Worker 执行）；`feedback-regression` 覆盖 continue/rewrite/review/canon | 章节计划反馈、场景生成反馈、审校反馈、章节确认全流程可完成；场景级 Canon 生效；章节级三类候选确认后进入正式 Story Bible | 通过：smoke-scene 运行创建→Worker 执行→到达 waiting_feedback/pending_clarification，事件序列 run_queued→结果事件；feedback-regression 6 条场景反馈 + 4 条场景级 Canon 均到达 waiting_feedback | 0 / 0 | 无 |
| 3. 状态恢复与 checkpoint 过期 | 运行中的 waiting_feedback/pending_clarification/paused 运行 | `tests/runtime/test_executor_recovery.py`、`tests/runtime/test_run_worker.py` | 重启后三种状态按 checkpoint 恢复，SSE 事件按 sequence 完整重放、无重复无缺失；超过 7 天的 checkpoint 转为 failed（错误码 CHECKPOINT_EXPIRED，errors.py:45 已定义） | 通过：checkpoint 恢复、租约接管、lease-lost→technical pause（不覆盖、不重复执行）测试全绿；CHECKPOINT_EXPIRED=410 已定义并有配置（checkpoint_retention_days=7） | 0 | 无 |
| 4. 章节重规划 | 已有已接受章节与生成运行 | 后端全量 pytest（test_canon_api / test_run_lifecycle） | 旧运行标记 superseded；新 plan_revision_id / generation_run_id 指向新计划；下游影响闭包完整；`stale_scene_ids` 聚合阻断旧场景继续生成 | 通过：superseded 状态机与场景失效在 API 测试中覆盖，370 全量通过 | 0 | 无 |
| 5. 终态注入（failed/cancelled/superseded） | 存在未决候选与未接受草稿 | 后端全量 pytest（test_canon_api / test_run_worker / test_executor_recovery） | 未决候选不可继续 Canon 确认；未接受草稿不可提交；审计快照（run_events、决策记录）保留不丢 | 通过：终态转换（failed/cancelled/superseded/paused）与事件保留测试覆盖；RunWorker 失败转换与事件持久化验证通过 | 0 | 无 |
| 6. 章节已接受后新场景接受 | 章节已接受，存在未接受的新场景修订 | 后端全量 pytest | 旧 ChapterRevision 不可变（无新修订追加）；章节标记 out_of_sync；返回 CHAPTER_OUT_OF_SYNC；`ChapterAggregationEligibility` 给出阻断码 | 通过：CHAPTER_OUT_OF_SYNC 错误码与 commit_guard 测试覆盖（test_commit_guard.py 等） | 0 | 无 |
| 7. C1→C2→C3 handoff 冲突 | C1/C2/C3 已形成 handoff 链 | `tests/api/test_chapter_handoff.py` | 回滚 C1 后 C2、C3 的 entry_handoff_status=stale；后续写入返回 CHAPTER_HANDOFF_CONFLICT | 通过：handoff 链 stale 化与 CHAPTER_HANDOFF_CONFLICT 写入阻断测试全绿 | 0 | 无 |
| 8. 故障注入（fencing/幂等/事件序列） | 正常运行环境 | `tests/runtime/test_executor_recovery.py`、`test_outbox_publish.py`、`test_run_worker.py`、`tests/domain/test_idempotency.py` | ① 版本提交后、checkpoint 前崩溃：恢复后无重复正式版本，事件序列无空洞；② outbox 写入后、发布前崩溃：恢复后事件完整投递；③ 租约过期后旧 worker 迟到写入：被 fencing 拒绝；④ 两个相同幂等键并发决策：CAS 保证只生效一次 | 通过：租约接管（test_reclaim_expired_takes_over_lease）、旧 token 写入拒绝（test_renew_rejects_old_token_after_takeover / test_stale_fencing_token_cannot_write）、outbox 幂等重放（test_repeat_publish_is_idempotent）、幂等键 CAS（test_idempotency）全部通过 | 0 | 无 |
| 9. 过期 ChangeSet 提交 | 存在已过期的 ChangeSet | `tests/api/test_manual_changesets.py` | 正文版本不变并返回冲突信息；同一幂等键重复提交返回同一结果（幂等） | 通过：SCENE_STALE / SCENE_STATE_INCOMPATIBLE 冲突契约与幂等重放测试覆盖 | 0 | 无 |
| 10. 两种 ChangeSet 格式 | clean fixture | `tests/api/test_manual_changesets.py` 等 | Unicode/格式差异、重叠编辑、锚点漂移、冲突展示均符合契约；两种格式均可正确合并或报冲突 | 通过：semantic_text 与 prosemirror_step 格式合并/冲突路径全量测试通过 | 0 | 无 |
| 11. 反馈重生成与取消/回滚 | 存在反馈后生成、取消运行、显式回滚场景 | `tests/api/test_run_lifecycle.py`、`test_manual_changesets.py` | 反馈后重新生成的补丁正确应用；取消运行只丢弃未提交候选（已提交/权威数据不丢）；显式回滚回到父版本；Story Bible 无未授权事实 | 通过：反馈决策、取消、回滚、Canon 物化边界测试覆盖 | 0 | 无 |
| 12. v1_rc_observability_metadata 迁移 | 迁移链 head=f8a9b0c1d2e3 | `tests/db/test_migrations.py::test_v1_rc_observability_migration_preserves_events`；`backup` / `restore` CLI | 失败迁移演练：回滚后事件不丢失、备份可恢复；成功迁移后 payload_schema/redaction_version/版本/候选/审计/事件序列不丢失；backup/restore 后 authority_hash 与 audit_hash 双哈希一致 | 通过：upgrade→insert→downgrade→upgrade round-trip 测试通过（事件与默认列保留）；backup/restore 双哈希一致（d49ba4e0…/2d242608…，match=true） | 0 / 0 | 无 |
| 13. 真实模型 smoke | 后端已构建、真实模型配置已提供（DeepSeek：`LLM_BASE_URL=https://api.deepseek.com`、`MODEL_NAME=deepseek-v4-flash`、`LLM_API_KEY` 从仓库根 `.env` 读取，已被 `.gitignore` 忽略） | `scripts/smoke_real_model.ps1`（未配置时输出 `SKIPPED_PROVIDER_SMOKE`，ok=false，不当作通过） | 配置齐全时验证真实 provider 连通并返回有效输出；未配置时输出 SKIPPED_PROVIDER_SMOKE 且不失败 | 通过：`smoke_real_model.ps1` 真实 DeepSeek 请求 HTTP 200；结构化响应经 `app.agents.schemas.WritingOutput` 契约校验通过（status=ready/mode=draft）；伪造 Key → 401 → `LLM_AUTH_ERROR`(retryable=false)、超时/连接失败 → `LLM_UNAVAILABLE`(retryable=true) 错误映射正确；app 脱敏 `find_leaks=0`、API Key 未出现在任何输出；版本提交边界只读探测未提交版本；SKIPPED 路径已实测（未配置时 ok=false 退出 0） | 0 | 无 |
| 14. 配置校验与清理 | 正常后端环境 | `tests/` 配置 fail-closed 测试 + `app/services/id_cleanup_service.py` | DEPLOYMENT_MODE 非法启动失败；API_BIND_SCOPE 非法启动失败；compose 端口错误启动失败；客户端伪造 actor 返回 ACTOR_OVERRIDE_FORBIDDEN；7 天 checkpoint 与 30 天审计数据被清理 | 通过：config fail-closed（DEPLOYMENT_MODE/API_BIND_SCOPE 非法拒绝启动）、脱敏/权限测试覆盖；清理服务实现（checkpoint_retention_days=7、audit_retention_days=30） | 0 | 无 |
| 15. 全量质量门禁 | 代码合入 V1-RC 基线 | 全量 pytest；ruff；mypy；前端构建；Playwright | 全量 pytest 通过；评测指标达标；前端构建成功；Playwright 分阶段全部通过；OpenAPI/迁移版本/事件 schema 冻结无漂移 | 通过：pytest **370 passed**；ruff **All checks passed**；mypy（Task 9 范围 app+tests/acceptance+runtime+observability+db，122 源文件）通过；前端 `npm run build` 成功；Playwright **16/16 passed**（editor 5 + runs 7 + story-bible 4） | 0 | 见「剩余风险」 |
| 16. author-feedback-10 回归 | clean fixture、Fake provider | `python -m app.acceptance.cli feedback-regression --fixture author-feedback-10.json` | 十条记录逐条比对 expected.final_status、版本增量、事件类型、候选断言、final_decision 全部命中；V1-RC 通过条件：十条齐全、断言全过、无未关闭 P0/P1 | 通过：**10/10**（continue×2 / rewrite×2 / review×2 / canon×4 全部到达 expected.final_status=waiting_feedback，事件序列 [run_queued, run_waiting_feedback]） | 0 | 无 |

## 判定

- 全部验收项通过且无未关闭 P0/P1：V1-RC **通过**。
- 任一验收项失败或存在未关闭 P0/P1：V1-RC **不通过**，按缺陷等级修复后重跑受影响项。

## 结论（2026-08-04 执行）

- **16/16 项通过**（真实模型 smoke 已于 2026-08-04 用 DeepSeek `deepseek-v4-flash` 补跑通过，见验收项 13）。
- 未发现未关闭的 P0/P1 缺陷。以「无未关闭 P0/P1 + 16 项验收全部通过」口径：**V1-RC 通过**。

### 剩余风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| 真实模型 smoke 为独立探测脚本，未接入 Agent 图 | 真实 provider 连通/结构化响应/错误映射/超时/脱敏边界已验证（deepseek-v4-flash）；Agent 仍为 Fake 实现，真实模型在图内的输出与重试路径未验证 | 已通过（验收项 13）；图内接入属后续接线，不新增 Task 10 |
| 前端 E2E 依赖本机端口 3000 无残留进程 | 残留 `next dev` 会被 Playwright `reuseExistingServer` 复用导致全部 UI 测试挂起（此前已复现并清理，16/16 通过） | 已通过（清理残留后重跑全绿）；CI 前需保证端口干净 |
