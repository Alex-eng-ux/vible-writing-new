"""Context Pack 的组装逻辑。

本模块负责一次生成运行内上下文来源的读取、过滤、预算分配、排序与
manifest 登记，最终产出 ContextPack。它只负责“组装”，绝不创建生成
运行、生成向量、写正文/版本或写候选。

核心约束：
- 强制项（priority == 0）必须全部纳入，若其总 token 已超预算则抛
  CONTEXT_BUDGET_EXCEEDED；可选项（priority > 0）按优先级顺序在预算
  内择优纳入，超出被截断并记录 omitt 来源；
- 跨章节交接（handoff）条目须先经 Task 2 的 get_valid_entry 校验有效，
  否则抛 CONTEXT_MANIFEST_MISMATCH；
- 请求指纹用于 manifest 一致性与回放校验（见 manifest 模块）；
- 本模块不直接写库，通过 manifest 端口登记 manifest。
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from ..domain.handoff import get_valid_entry
from ..errors import AppError
from . import manifest as manifest_mod
from .models import (
    ContextItem,
    ContextManifest,
    ContextManifestEntry,
    ContextPack,
    MetadataRetriever,
    SceneRequest,
    VectorRetriever,
)
from .retrievers import metadata_retriever, vector_retriever


def _request_fingerprint(request: SceneRequest) -> str:
    """对请求做规范序列化并计算 SHA-256 指纹。

    参数：
        request: 场景生成请求。

    返回：
        请求指纹十六进制字符串，用于 manifest 一致性与回放校验。
    """
    canonical = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sort_key(item: ContextItem) -> tuple[int, str, str, str | None]:
    """定义 ContextItem 的稳定排序键。

    返回：
        (priority, source_type, source_id, source_revision_id)，保证同
        优先级下排序确定。
    """
    return (
        item["priority"],
        item["source_type"],
        item["source_id"],
        item["source_revision_id"],
    )


def _compose_entries(items: list[ContextItem]) -> list[ContextManifestEntry]:
    """将入选的 ContextItem 组装为 manifest 登记条目。

    参数：
        items: 入选的上下文条目。

    返回：
        带统一 resolved_at 的 ContextManifestEntry 列表。
    """
    from ..db.models import utcnow

    resolved_at = utcnow()
    entries: list[ContextManifestEntry] = []
    for item in items:
        entries.append(
            ContextManifestEntry(
                source_id=item["source_id"],
                source_type=item["source_type"],
                source_revision_id=item["source_revision_id"],
                resolved_at=resolved_at,
            )
        )
    return entries


def compose_context(
    session: Session,
    project_id: str,
    scene_id: str,
    request: SceneRequest,
    token_budget: int,
    generation_run_id: str,
    manifest: ContextManifest | None,
    entry_handoff_id: str | None,
    entry_source_chapter_revision_id: str | None,
    entry_handoff_chain_hash: str | None,
    base_scene_revision_id: str | None,
    base_chapter_revision_id: str | None,
    *,
    metadata_port: MetadataRetriever | None = None,
    vector_port: VectorRetriever | None = None,
    vector_available: bool = True,
) -> ContextPack:
    """读取、过滤、组装并登记一次运行的上下文来源，返回 ContextPack。

    参数：
        session: 数据库会话。
        project_id: 项目 ID。
        scene_id: 目标场景 ID。
        request: 场景生成请求。
        token_budget: 上下文 token 预算，必须为正数。
        generation_run_id: 所属生成运行 ID。
        manifest: 复用候选 manifest，可为 None。
        entry_handoff_id: 跨章节交接条目 ID，可为 None。
        entry_source_chapter_revision_id: 交接来源章节版本 ID，可为 None。
        entry_handoff_chain_hash: 交接链哈希，可为 None。
        base_scene_revision_id: 基准场景版本 ID（作为版本白名单），可为 None。
        base_chapter_revision_id: 基准章节版本 ID，可为 None。
        metadata_port: 元数据检索端口，缺省时使用默认实现。
        vector_port: 向量检索端口，缺省时使用默认实现。
        vector_available: 向量服务是否可用。

    返回：
        仅返回 ContextPack；绝不创建运行、生成向量、写正文/版本或写候选。

    失败条件：
        token_budget <= 0 抛 VALIDATION_ERROR；回放校验失败或交接无效抛
        CONTEXT_MANIFEST_MISMATCH；强制项总 token 超预算抛
        CONTEXT_BUDGET_EXCEEDED。
    """
    if token_budget <= 0:
        raise AppError("VALIDATION_ERROR", "token_budget must be positive")

    fingerprint = _request_fingerprint(request)

    if manifest is not None:
        manifest_mod.validate_replay(session, generation_run_id, manifest, fingerprint)

    # Resolve the valid cross-chapter handoff entry through Task 2's port.
    if entry_handoff_id is not None:
        from ..db.models import Scene

        scene = session.get(Scene, scene_id)
        if scene is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene does not exist")
        handoff = get_valid_entry(
            session,
            scene.chapter_id,
            entry_handoff_id,
            entry_source_chapter_revision_id,
            entry_handoff_chain_hash,
        )
        if handoff is None:
            raise AppError("CONTEXT_MANIFEST_MISMATCH", "entry handoff is not valid for this chapter")

    meta = metadata_port or metadata_retriever(session)
    vec = vector_port or vector_retriever(session, available=vector_available)

    source_revision_ids = [rid for rid in (base_scene_revision_id,) if rid]
    items = meta.retrieve(request, source_revision_ids)

    # P1 handoff item (after validation above).
    if entry_handoff_id is not None:
        items.append(
            ContextItem(
                source_id=entry_handoff_id,
                source_type="handoff",
                source_revision_id=entry_source_chapter_revision_id,
                priority=1,
                content="chapter handoff",
                token_estimate=1,
                truncation_reason=None,
                metadata={"chain_hash": entry_handoff_chain_hash},
            )
        )

    # P4 vector supplement restricted to the allowed source whitelist.
    allowed_source_ids = [i["source_id"] for i in items]
    vector_items = vec.retrieve("", allowed_source_ids, limit=10)
    items.extend(vector_items)

    items.sort(key=_sort_key)

    p0_items = [i for i in items if i["priority"] == 0]
    optional_items = [i for i in items if i["priority"] > 0]

    p0_tokens = sum(i["token_estimate"] for i in p0_items)
    if p0_tokens > token_budget:
        raise AppError("CONTEXT_BUDGET_EXCEEDED", "mandatory context exceeds the token budget")

    selected: list[ContextItem] = list(p0_items)
    remaining = token_budget - p0_tokens
    omitted: list[str] = []
    for item in optional_items:
        if item["token_estimate"] <= remaining:
            selected.append(item)
            remaining -= item["token_estimate"]
        else:
            item["truncation_reason"] = "budget_truncated"
            omitted.append(item["source_id"])

    entries = _compose_entries(selected)
    created = manifest_mod.create_or_reuse(
        session,
        generation_run_id,
        fingerprint,
        entries,
        entry_handoff_id,
        entry_source_chapter_revision_id,
        entry_handoff_chain_hash,
    )

    total = sum(i["token_estimate"] for i in selected)
    return ContextPack(
        generation_run_id=generation_run_id,
        scene_id=scene_id,
        items=selected,
        total_token_estimate=total,
        omitted_source_ids=omitted,
        manifest_id=created["manifest_id"],
    )
