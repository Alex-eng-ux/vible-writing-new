# V1 收尾加固交接文档（done-2026-08-04-task9-hardening.md）

- 任务：V1 收尾加固（不需要真实模型 API）——修复全量 `mypy app tests` 既有错误、为 Task 7C 的 3 个 Canon GET 接口补独立 API 单测、隔离备份库大容量迁移演练、全量质量门禁重跑、交接文档
- 状态：已完成（mypy 全量 0 错误、新增 7 条单测、迁移演练通过、pytest 377 passed、ruff/mypy/前端构建/Playwright 16/16 全绿）
- 前置：Task 9（V1 最终验收，`codex-handoff/done-2026-08-04-task9.md`）

## 目标

1. 修复全量 `mypy app tests` 的 48 个既有错误，不用无依据的 `type: ignore`；
2. 为 Task 7C 的 3 个 Canon GET 接口补充独立 API 单测；
3. 在隔离的备份数据库上进行较大数据量迁移演练，验证升级、回滚、数据和双哈希不丢失；
4. 不修改正文、Canon、版本、运行状态机和 API 业务契约；
5. 重新运行全量 pytest、ruff、mypy、前端构建和 Playwright；
6. 更新新的交接文档，写明修复数量、测试结果和剩余事项。

## 一、mypy 全量修复（48 个错误，0 剩余，无 type: ignore）

`mypy app tests` 此前 48 个错误全部位于 6 个旧测试文件，逐类修复（全部为有依据的类型修复）：

| 文件 | 错误数 | 根因 | 修复 |
| --- | --- | --- | --- |
| `tests/agents/test_chapter_agents.py` | 11 | `AgentInputEnvelope(**base)` 中 `base` 被推断为 `dict[str, RuntimeContext]`，解包类型与 pydantic 字段不匹配 | `base: dict[str, Any]` + `**overrides: object`（辅助函数用动态字典构造输入信封，运行时由 pydantic 校验） |
| `tests/agents/test_chapter_graph.py` | 12 | 同上（`dict[str, object]`） | 同上 |
| `tests/agents/test_chapter_scene_graph.py` | 12 | 同上 | 同上 |
| `tests/api/test_run_lifecycle.py` | 4 | ① generator fixture 返回注解 `-> str`；② `_seed_command_ctx()` 返回 `dict` 传给 `CommandContext` 参数 | ① 改为 `-> Iterator[str]`；② 返回注解 `-> CommandContext` + `cast(CommandContext, {...})`（沿用 `e2e_fixtures.py` 既有约定） |
| `tests/api/test_chapter_handoff.py` | 8 | `_command_ctx()` 返回 `dict` 传给 `CommandContext` 参数 | 同上 cast 约定 |
| `tests/api/test_manual_changesets.py` | 1 | `payload = {...}` 缺类型注解 | `payload: dict[str, object]` |

结果：`mypy app tests` → **Success: no issues found in 174 source files**（退出码 0）。全程未新增任何 `type: ignore`（`app/api/canon.py` 中既有的 3 处 `type: ignore[dict-item]` 属候选模型异构映射，非本次范围，且不在 48 个错误内）。

## 二、Canon GET 只读端点独立 API 单测（新增 7 条）

新增 `tests/api/test_canon_read_endpoints.py`（7 条），此前 3 个只读端点仅在 Playwright E2E 覆盖，现补独立后端单测：

- `GET /api/projects/{project_id}/canon`（3 条）：空快照、三类 active 正式条目完整字段（type/text/status/story_time/entities/state/planned_resolution）、非 active 与其他项目条目隔离；
- `GET /api/scenes/{scene_id}/canon-candidates`（2 条）：三类候选按 `candidate_type` 字典序返回 + target 元数据正确、空集与跨场景隔离；
- `GET /api/chapters/{chapter_id}/canon-candidates`（2 条）：三类候选返回且场景级候选（scene_id 非空）不混入章节端点、空集。

测试复用 `tests/api/test_canon_api.py` 的既有播种 helper（`_setup_chapter`/`_setup_scene`/`_candidate_payload`）与 autouse 清理 fixture；正式 Canon 直接 ORM 播种。全部通过（7/7）。

## 三、隔离备份库大容量迁移演练（升级 / 回滚 / 数据 / 双哈希）

新增 `scripts/migration_rehearsal.py`（可复现，`python scripts/migration_rehearsal.py`），在专用隔离库 `novel_migration_bulk` 上演练：

1. **数据规模**：3000 条 run_events（200 条 generation_runs × 15 条事件）、1000 条 scene_revisions、200 条 canon_facts、300 条 fact_candidates、200 条 run_decisions，外加项目/卷/章/场景层级；
2. **升级 f8a9b0c1d2e3 → head**（应用 `v1_rc_observability_metadata`）：数据行数、事件序列指纹（run_id+sequence+event_type+payload 全序 SHA-256）、payload 内容全程不变；新列 payload_schema/redaction_version 按默认值回填；
3. **降级回 f8a9b0c1d2e3**：数据行数与事件序列不变，双哈希**完全回到升级前基线**；
4. **再升级 head**：双哈希回到升级后状态（往返一致）；
5. **backup/restore**：head 状态下双哈希 match=true。

**演练结果（关键）**：
- authority_hash 升级前后不变（`4a5cca34…`，权威表不含 run_events 且数据未变）；
- audit_hash：升级后为 `0eae6ce5…`（run_events 多两列默认值），降级回 `f6167f98…`，再升级回 `0eae6ce5…`；
- `downgrade_returns_to_baseline=true`、`reupgrade_returns_to_h2=true`、`events_sequence_preserved=true`、`backup_restore_match=true`。

**配套加固**：演练暴露 `hashes.py::table_records` 用 head 模型固定列集在旧 schema 上 SELECT 失败——改为按库实际 schema 列读取（`inspect.get_columns`）。head schema 下行为不变（读取全部列），旧 schema 下可正常计算哈希，使"降级后哈希回到基线"成为可验证属性；`tests/acceptance/test_hashes.py` 6 条全通过无回归。

**灌数实现要点**：run_events 用 autoload Core table 插入（旧 schema 无新列，ORM 模型会引用不存在的列）；先 flush 生成主键再取 id（`default=new_id` 在 flush 时求值）。

## 四、范围边界（未改动业务契约）

- 未修改任何正文、Canon、版本、运行状态机与 API 业务契约代码；改动仅限：6 个测试文件的类型注解、1 个新测试文件、`app/acceptance/hashes.py`（验收工具，schema 感知读取，head 行为不变）、新增 `scripts/migration_rehearsal.py`（验收脚本）。
- `app/api/canon.py` 既有的 `type: ignore[dict-item]`（候选模型异构映射）不在 48 个错误内，未改动。

## 五、全量质量门禁（重跑结果）

| 门禁 | 结果 |
| --- | --- |
| 后端 pytest 全量 | **377 passed**（Task 9 的 370 + 新增 7 条 Canon GET 单测），退出码 0 |
| ruff | `ruff check app tests scripts` **All checks passed** |
| mypy | `mypy app tests` **Success: no issues found in 174 source files** |
| 前端构建 | `npm run build` 成功（Next.js 15） |
| Playwright | **16/16 passed**（editor 5 + runs 7 + story-bible 4，58.9s） |
| 迁移演练 | `scripts/migration_rehearsal.py` 退出码 0，全部断言通过 |

## 六、剩余事项与风险

| 问题 | 影响 | 验证状态 |
| --- | --- | --- |
| Playwright `editor.spec.ts` 过期基线冲突测试偶发 flaky | 全量首跑 15/16（该测试 1 失败：编辑器异步加载 vs 输入存在时序竞态，状态显示"没有需要保存的变更"）；单独重跑与全量重跑均通过 | 16/16 最终通过；建议后续在 `openScene` 后显式等待编辑器基线内容就绪以消除竞态 |
| 真实模型 smoke 仍未执行（SKIPPED_PROVIDER_SMOKE） | 承接 Task 9 未决项：需用户配置 LLM_BASE_URL/LLM_API_KEY/模型名后补跑 | 不在本次范围（用户明确不需要真实模型 API） |
| `hashes.py` schema 感知读取依赖 `inspect.get_columns` | 每次计算多一次 introspection 开销（验收工具，可接受）；同 schema 下哈希与迁移前行为一致 | 已验证无回归 |

## 当前未完成事项与下一步

1. V1 收尾加固已完成，交接文档：`codex-handoff/done-2026-08-04-task9-hardening.md`。
2. 可选项：修复 Playwright 冲突测试的时序竞态（等待编辑器基线就绪）；配置真实模型后补跑 smoke 并更新 v1-checklist 第 13 项。
