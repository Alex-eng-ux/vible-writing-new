"""Context Manifest 的创建、复用与回放校验。

本模块负责一次生成运行对应的来源清单（manifest）的持久化与一致性
校验：为同一运行创建 manifest，或在来源集合/版本映射/交接链引用完全
一致时复用已有 manifest；同时在回放时校验 manifest 归属与指纹匹配。

核心约束：
- 同一运行只能有一个 manifest，绝不静默覆盖：若已存在则必须通过
  _validate_reuse 校验请求指纹、来源集合、版本映射与交接链引用，否则
  抛 CONTEXT_MANIFEST_MISMATCH；
- version_mapping 中保留一个保留键 _HANDOFF_REF_KEY 承载跨章节交接
  引用，用于校验顺序交接链；
- 本模块只做 manifest 的登记与校验，不写任何正文/版本/候选数据。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ContextManifest as ContextManifestModel
from ..errors import AppError
from .models import ContextManifest, ContextManifestEntry

# Reserved key inside version_mapping that carries the cross-chapter handoff
# reference used to validate sequential handoff handoff.
_HANDOFF_REF_KEY = "__handoff_ref__"


def _handoff_ref(
    entry_handoff_id: str | None,
    entry_source_chapter_revision_id: str | None,
    entry_handoff_chain_hash: str | None,
) -> str:
    """将交接链引用序列化为稳定 JSON 字符串。

    参数：
        entry_handoff_id: 跨章节交接条目 ID。
        entry_source_chapter_revision_id: 交接来源章节版本 ID。
        entry_handoff_chain_hash: 交接链哈希。

    返回：
        按键排序、紧凑分隔的 JSON 字符串，用于存入 version_mapping。
    """
    return json.dumps(
        {
            "entry_handoff_id": entry_handoff_id,
            "entry_source_chapter_revision_id": entry_source_chapter_revision_id,
            "entry_handoff_chain_hash": entry_handoff_chain_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def create_or_reuse(
    session: Session,
    generation_run_id: str,
    request_fingerprint: str,
    entries: list[ContextManifestEntry],
    entry_handoff_id: str | None,
    entry_source_chapter_revision_id: str | None,
    entry_handoff_chain_hash: str | None,
) -> ContextManifest:
    """创建该运行的 manifest，或复核后复用已存在的同名 manifest。

    参数：
        session: 数据库会话。
        generation_run_id: 所属生成运行 ID。
        request_fingerprint: 请求指纹（SHA-256）。
        entries: 来源条目列表。
        entry_handoff_id: 跨章节交接条目 ID，可为 None。
        entry_source_chapter_revision_id: 交接来源章节版本 ID，可为 None。
        entry_handoff_chain_hash: 交接链哈希，可为 None。

    返回：
        返回新建或复用的 ContextManifest。

    失败条件：
        已存在 manifest 但来源集合/版本映射/请求指纹/交接链引用不一致时
        抛 CONTEXT_MANIFEST_MISMATCH；绝不静默覆盖。
    """
    existing = session.execute(
        select(ContextManifestModel).where(
            ContextManifestModel.generation_run_id == generation_run_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        _validate_reuse(
            existing,
            request_fingerprint,
            entries,
            entry_handoff_id,
            entry_source_chapter_revision_id,
            entry_handoff_chain_hash,
        )
        return _to_manifest(existing)

    version_mapping = {
        e["source_id"]: e["source_revision_id"] for e in entries if e["source_revision_id"] is not None
    }
    version_mapping[_HANDOFF_REF_KEY] = _handoff_ref(
        entry_handoff_id,
        entry_source_chapter_revision_id,
        entry_handoff_chain_hash,
    )
    row = ContextManifestModel(
        generation_run_id=generation_run_id,
        source_index={e["source_id"]: e["source_type"] for e in entries},
        version_mapping=version_mapping,
        request_fingerprint=request_fingerprint,
    )
    session.add(row)
    session.flush()
    return _to_manifest(row)


def _validate_reuse(
    existing: ContextManifestModel,
    request_fingerprint: str,
    entries: list[ContextManifestEntry],
    entry_handoff_id: str | None,
    entry_source_chapter_revision_id: str | None,
    entry_handoff_chain_hash: str | None,
) -> None:
    """校验已存在 manifest 是否与本次请求一致，决定是否允许复用。

    参数：
        existing: 已持久化的 manifest 行。
        request_fingerprint: 本次请求指纹。
        entries: 本次来源条目。
        entry_handoff_id: 本次交接条目 ID。
        entry_source_chapter_revision_id: 本次交接来源章节版本 ID。
        entry_handoff_chain_hash: 本次交接链哈希。

    失败条件：
        请求指纹、来源集合、版本映射或交接链引用任一不一致时抛
        CONTEXT_MANIFEST_MISMATCH，防止同一运行被静默覆盖。
    """
    if existing.request_fingerprint != request_fingerprint:
        raise AppError("CONTEXT_MANIFEST_MISMATCH", "request fingerprint changed for the same run")
    stored_sources = set(existing.source_index.items())
    desired_sources = set((e["source_id"], e["source_type"]) for e in entries)
    if stored_sources != desired_sources:
        raise AppError("CONTEXT_MANIFEST_MISMATCH", "source set changed for the same run")
    stored_versions = {
        k: v for k, v in existing.version_mapping.items() if k != _HANDOFF_REF_KEY
    }
    desired_versions = {
        e["source_id"]: e["source_revision_id"] for e in entries if e["source_revision_id"] is not None
    }
    if stored_versions != desired_versions:
        raise AppError("CONTEXT_MANIFEST_MISMATCH", "version mapping changed for the same run")
    stored_handoff = json.loads(existing.version_mapping.get(_HANDOFF_REF_KEY, "{}"))
    if (
        stored_handoff.get("entry_handoff_id") != entry_handoff_id
        or stored_handoff.get("entry_source_chapter_revision_id") != entry_source_chapter_revision_id
        or stored_handoff.get("entry_handoff_chain_hash") != entry_handoff_chain_hash
    ):
        raise AppError("CONTEXT_MANIFEST_MISMATCH", "handoff chain reference changed for the same run")


def get_manifest(
    session: Session,
    generation_run_id: str,
) -> ContextManifest | None:
    """按生成运行 ID 读取持久化 manifest。

    参数：
        session: 数据库会话。
        generation_run_id: 生成运行 ID。

    返回：
        对应的 ContextManifest；不存在时返回 None。
    """
    row = session.execute(
        select(ContextManifestModel).where(
            ContextManifestModel.generation_run_id == generation_run_id
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return _to_manifest(row)


def validate_replay(
    session: Session,
    generation_run_id: str,
    manifest: ContextManifest,
    request_fingerprint: str,
) -> None:
    """校验回放时提交的 manifest 属于当前运行且指纹匹配。

    参数：
        session: 数据库会话。
        generation_run_id: 当前生成运行 ID。
        manifest: 待回放校验的 manifest。
        request_fingerprint: 当前请求指纹。

    失败条件：
        manifest 属于其他运行、指纹不匹配，或与持久化 manifest 不一致时
        抛 CONTEXT_MANIFEST_MISMATCH。
    """
    if manifest["generation_run_id"] != generation_run_id:
        raise AppError("CONTEXT_MANIFEST_MISMATCH", "manifest belongs to a different run")
    if manifest["request_fingerprint"] != request_fingerprint:
        raise AppError("CONTEXT_MANIFEST_MISMATCH", "request fingerprint changed for the same run")
    persisted = get_manifest(session, generation_run_id)
    if persisted is None or persisted["manifest_id"] != manifest["manifest_id"]:
        raise AppError("CONTEXT_MANIFEST_MISMATCH", "manifest does not match the persisted manifest")


def _to_manifest(row: ContextManifestModel) -> ContextManifest:
    """将持久化 manifest 行转换为不可变 ContextManifest 数据。

    参数：
        row: 数据库中的 manifest 行。

    返回：
        按 source_id/source_type 排序条目、还原交接链引用后的
        ContextManifest。
    """
    entries: list[ContextManifestEntry] = []
    for source_id, source_type in row.source_index.items():
        entries.append(
            ContextManifestEntry(
                source_id=source_id,
                source_type=source_type,
                source_revision_id=row.version_mapping.get(source_id),
                resolved_at=row.created_at,
            )
        )
    entries.sort(key=lambda e: (e["source_id"], e["source_type"]))
    handoff = json.loads(row.version_mapping.get(_HANDOFF_REF_KEY, "{}"))
    return ContextManifest(
        manifest_id=row.id,
        generation_run_id=row.generation_run_id,
        request_fingerprint=row.request_fingerprint,
        entries=entries,
        entry_handoff_id=handoff.get("entry_handoff_id"),
        entry_source_chapter_revision_id=handoff.get("entry_source_chapter_revision_id"),
        entry_handoff_chain_hash=handoff.get("entry_handoff_chain_hash"),
    )
