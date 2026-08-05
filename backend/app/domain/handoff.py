"""章节交接（handoff）领域：读取有效交接与创建交接。

交接表示“已接受章节修订可作为后续任务的入口”。读取时只接受 active 且
in_sync 并且链哈希匹配的交接，并额外校验来源章节修订必须为 accepted 且
匹配该章节当前 accepted 指针，绝不回退到最新修订或接受过期/不匹配的
交接；创建交接要求源章节修订处于 accepted 状态。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import Chapter as ChapterModel
from ..db.models import ChapterHandoff as ChapterHandoffModel
from ..db.models import ChapterRevision
from ..errors import AppError
from .interfaces import ChapterHandoff, CommandContext


def _current_accepted_revision_id(session: Session, chapter_id: str) -> str | None:
    """返回章节当前 accepted 修订 id（只用显式指针，不按最新 accepted 行推断）。

    使用 `Chapter.accepted_chapter_revision_id`，由 `commit_chapter_version`
    写入；绝不按 created_at 取最新 accepted 行。
    """
    chapter = session.get(ChapterModel, chapter_id)
    if chapter is None:
        return None
    return chapter.accepted_chapter_revision_id


def get_valid_entry(
    session: Session,
    chapter_id: str,
    handoff_id: str | None,
    source_chapter_revision_id: str | None,
    expected_chain_hash: str | None,
) -> ChapterHandoff | None:
    """只读取已接受（accepted）且 in_sync 且链哈希匹配的交接。

    参数：chapter_id 为章节 id；handoff_id 为交接 id；source_chapter_revision_id
    为期望的源章节修订 id；expected_chain_hash 为期望的链哈希。
    返回：当不存在有效交接时返回 None；绝不回退到最新修订，也绝不接受过期
    或不匹配的交接。

    校验顺序：
        1. 交接必须存在且属于该章节。
        2. 交接必须 status=active 且 entry_handoff_status=in_sync。
        3. 来源章节修订必须存在且 status=accepted。
        4. 来源章节修订必须等于该章节当前 accepted 指针（不匹配视为过期）。
        5. 若提供了 source_chapter_revision_id 则必须与交接来源一致。
        6. 若提供了 expected_chain_hash 则必须与交接链哈希一致。

    失败条件：不抛异常，仅返回 None。
    副作用：只读，不写库。
    """
    if handoff_id is None:
        return None
    handoff = session.get(ChapterHandoffModel, handoff_id)
    if handoff is None or handoff.chapter_id != chapter_id:
        return None
    if handoff.status != "active" or handoff.entry_handoff_status != "in_sync":
        return None
    source_rev = session.get(ChapterRevision, handoff.source_chapter_revision_id)
    if source_rev is None or source_rev.status != "accepted":
        return None
    # 跨章节继承：handoff 的来源修订属于来源（前驱）章节，必须用来源章节的
    # accepted 指针校验，绝不与目标章节的指针比较。
    if _current_accepted_revision_id(session, source_rev.chapter_id) != handoff.source_chapter_revision_id:
        return None
    if source_chapter_revision_id is not None and handoff.source_chapter_revision_id != source_chapter_revision_id:
        return None
    if expected_chain_hash is not None and handoff.chain_hash != expected_chain_hash:
        return None
    return ChapterHandoff(
        id=handoff.id,
        chapter_id=handoff.chapter_id,
        source_chapter_revision_id=handoff.source_chapter_revision_id,
        entry_handoff_status=handoff.entry_handoff_status,
        chain_hash=handoff.chain_hash,
        status=handoff.status,
    )


def get_chapter_handoff(
    session: Session,
    chapter_id: str,
    handoff_id: str | None,
    source_chapter_revision_id: str | None,
    expected_chain_hash: str | None,
    ctx: CommandContext,
) -> ChapterHandoff | None:
    """读取章节交接的便捷入口，委托给 get_valid_entry。

    参数：与 get_valid_entry 相同，另含 ctx 命令上下文（本函数不直接使用）。
    返回：有效交接或 None（无有效交接时）。
    副作用：只读，不写库。
    """
    return get_valid_entry(session, chapter_id, handoff_id, source_chapter_revision_id, expected_chain_hash)


def create_chapter_handoff(
    session: Session,
    chapter_revision_id: str,
    chain_hash: str,
    ctx: CommandContext,
) -> ChapterHandoff:
    """为已接受的章节修订创建交接记录。

    参数：chapter_revision_id 为源章节修订 id；chain_hash 为链哈希；ctx 为
    命令上下文。
    返回：新建的 ChapterHandoff 投影。

    失败条件：源章节修订不存在或状态非 accepted 时抛
    CHAPTER_OUT_OF_SYNC。

    副作用：新增交接并 flush；须在已通过 CommitGuard 的事务内调用。
    """
    from ..db.models import ChapterRevision

    rev = session.get(ChapterRevision, chapter_revision_id)
    if rev is None or rev.status != "accepted":
        raise AppError("CHAPTER_OUT_OF_SYNC", "handoff requires an accepted chapter revision")
    handoff = ChapterHandoffModel(
        chapter_id=rev.chapter_id,
        source_chapter_revision_id=rev.id,
        entry_handoff_status="in_sync",
        chain_hash=chain_hash,
        status="active",
    )
    session.add(handoff)
    session.flush()
    return ChapterHandoff(
        id=handoff.id,
        chapter_id=handoff.chapter_id,
        source_chapter_revision_id=handoff.source_chapter_revision_id,
        entry_handoff_status=handoff.entry_handoff_status,
        chain_hash=handoff.chain_hash,
        status=handoff.status,
    )
