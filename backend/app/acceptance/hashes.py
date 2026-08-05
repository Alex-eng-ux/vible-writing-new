"""Task 9 V1 验收：权威与审计状态哈希（authority_hash / audit_hash）。

定义（见 ``docs/acceptance/authority-hash-spec.md``）：
- ``authority_hash``：作品层级、已接受正文版本及指针、正式 Story Bible、
  已接受章节 handoff 与正式 Canon 决策结果（权威结构表）；
- ``audit_hash``：作者决策、候选审计、``RunEvent`` 与运行审计元数据；
- 两者均使用 SHA-256、UTF-8、稳定 JSON（键按字典序、记录按表内排序、
  时间统一 UTC ISO-8601、``null`` 保留）；
- 排除派生/临时数据：``context_manifests``、``scene_snapshots``、
  ``chapter_snapshots``、``run_outbox_records``（临时投递状态）、
  ``run_leases``、``command_idempotency_records``、``run_event_consumer_cursors``
  （checkpoint/向量/ContextPack/LangSmith Trace 均不在库内）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Base

# 权威哈希表（作品结构 + 已接受正文/指针 + 正式 Story Bible + handoff + 结构链接）。
AUTHORITY_TABLES: tuple[str, ...] = (
    "novel_projects",
    "volumes",
    "chapters",
    "scenes",
    "scene_revisions",
    "chapter_plan_revisions",
    "chapter_plan_revision_links",
    "chapter_revisions",
    "chapter_revision_scenes",
    "chapter_handoffs",
    "entities",
    "canon_facts",
    "timeline_events",
    "plot_threads",
    "foreshadowings",
)

# 审计哈希表（作者决策 + 候选审计 + 运行事件 + 运行审计元数据）。
AUDIT_TABLES: tuple[str, ...] = (
    "run_decisions",
    "author_feedbacks",
    "fact_candidates",
    "timeline_event_candidates",
    "plot_thread_updates",
    "canon_decision_records",
    "run_events",
    "generation_runs",
    "agent_runs",
)

# 明确排除的派生/临时表（不属于两种哈希，但记录在案便于核对）。
EXCLUDED_TABLES: tuple[str, ...] = (
    "context_manifests",
    "scene_snapshots",
    "chapter_snapshots",
    "run_outbox_records",
    "run_leases",
    "command_idempotency_records",
    "run_event_consumer_cursors",
)


def _normalize_value(value: Any) -> Any:
    """把行值规范化为稳定可序列化形式（时间统一 UTC ISO-8601）。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict | list):
        return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))
    return value


def table_records(session: Session, table_name: str) -> list[str]:
    """读取一张表的全部行并规范化为稳定 JSON 字符串列表。

    参数：session 为数据库会话；table_name 为表名。
    返回：按行整体排序后的 JSON 字符串列表（键按字典序、时间 UTC）。
    失败条件：表不在模型元数据中抛 KeyError（配置错误，不做静默忽略）。

    列集按库实际 schema 读取（``inspect.get_columns``）而非模型固定列：
    迁移演练在旧版本 schema（如 Task 5C head，run_events 尚无
    payload_schema/redaction_version）上也能计算哈希；同一 schema 状态下
    升级前后数据一致则双哈希一致，降级回旧 schema 哈希回到升级前基线。
    """
    table = Base.metadata.tables[table_name]
    inspector = sa_inspect(session.bind)
    assert inspector is not None  # session.bind 为 Engine/Connection，必然可检查
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    rows = session.execute(select(*[table.c[name] for name in columns])).mappings().all()
    normalized = []
    for row in rows:
        doc = {key: _normalize_value(value) for key, value in row.items()}
        normalized.append(json.dumps(doc, sort_keys=True, ensure_ascii=False))
    normalized.sort()
    return normalized


def compute_hash(session: Session, tables: tuple[str, ...]) -> str:
    """按固定表顺序对规范化记录计算 SHA-256（表名与记录均以 NUL 分隔）。

    参数：session 为数据库会话；tables 为参与哈希的表名列表（顺序固定）。
    返回：十六进制 SHA-256 摘要。
    """
    digest = hashlib.sha256()
    for table_name in tables:
        digest.update(table_name.encode("utf-8"))
        digest.update(b"\x00")
        for record in table_records(session, table_name):
            digest.update(record.encode("utf-8"))
            digest.update(b"\x00")
    return digest.hexdigest()


def compute_authority_hash(session: Session) -> str:
    """计算权威状态哈希（作品结构 + 已接受正文/指针 + 正式 Story Bible + handoff）。"""
    return compute_hash(session, AUTHORITY_TABLES)


def compute_audit_hash(session: Session) -> str:
    """计算审计状态哈希（作者决策 + 候选审计 + RunEvent + 运行审计元数据）。"""
    return compute_hash(session, AUDIT_TABLES)


def snapshot_hashes(session: Session) -> dict[str, str]:
    """返回权威与审计双哈希摘要（备份/恢复比较用）。"""
    return {
        "authority_hash": compute_authority_hash(session),
        "audit_hash": compute_audit_hash(session),
    }


def compute_fixture_hash(fixture: dict) -> str:
    """对固定 fixture 清单计算独立哈希（clean 重置用，不依赖随机正式 ID）。

    参数：fixture 为 ``v1-fixture.json`` 解析后的字典。
    返回：对稳定 JSON（sort_keys、ensure_ascii=False）的 SHA-256。
    """
    return hashlib.sha256(
        json.dumps(fixture, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def hashes_equal(left: dict[str, str], right: dict[str, str]) -> bool:
    """比较两份哈希摘要是否一致（authority 与 audit 同时相等）。"""
    return left.get("authority_hash") == right.get("authority_hash") and left.get(
        "audit_hash"
    ) == right.get("audit_hash")


__all__ = [
    "AUTHORITY_TABLES",
    "AUDIT_TABLES",
    "EXCLUDED_TABLES",
    "table_records",
    "compute_authority_hash",
    "compute_audit_hash",
    "snapshot_hashes",
    "compute_fixture_hash",
    "hashes_equal",
]
