"""卷资源 API：在卷下创建章、列出卷下的章。

创建章为幂等命令；列出章时只返回 accepted 章节版本指针，不返回最新行。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Chapter, Volume
from ..domain.chapters import create_chapter
from ..domain.commit_guard import CommitGuard
from ..domain.idempotency import fingerprint
from ..errors import AppError
from ..services.deletion import delete_volume
from .commands import execute_command
from .deps import get_actor_id, get_db, get_idempotency_key
from .resources_common import get_accepted_chapter_revision
from .schemas import ChapterCreate, ChapterRead, ResourceCreated

router = APIRouter(prefix="/api/volumes", tags=["volumes"])


@router.delete("/{volume_id}", status_code=204)
def remove_volume(
    volume_id: str,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
) -> Response:
    """删除卷及其章节、场景和关联记录；失败时由请求事务回滚。"""
    delete_volume(session, volume_id)
    session.commit()
    return Response(status_code=204)


@router.post("/{volume_id}/chapters", status_code=201, response_model=ResourceCreated)
def post_chapter(
    volume_id: str,
    body: ChapterCreate,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> ResourceCreated:
    """在指定卷下创建章；父卷不存在时返回 404 错误信封。"""
    if session.get(Volume, volume_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "volume not found")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        CommitGuard(session).validate(
            "resource_create", actor_id, None, idempotency_key, []
        )
        chapter = create_chapter(
            session,
            volume_id,
            body.title,
            body.pov,
            body.chapter_intent.model_dump(),
            {"actor_id": actor_id, "idempotency_key": idempotency_key},
        )
        response = {
            "id": chapter.id,
            "type": "chapter",
            "parent_id": volume_id,
            "version": 1,
            "created_at": chapter.created_at.isoformat(),
        }
        return response, chapter.id

    return ResourceCreated(
        **execute_command(
            session,
            f"volume:{volume_id}",
            "chapter_create",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.get("/{volume_id}/chapters", response_model=list[ChapterRead])
def list_chapters(
    volume_id: str,
    session: Session = Depends(get_db),
) -> list[ChapterRead]:
    """列出卷下的章；每章只显示 accepted 章节版本指针。"""
    if session.get(Volume, volume_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "volume not found")
    rows = (
        session.execute(
            select(Chapter).where(Chapter.volume_id == volume_id).order_by(Chapter.created_at)
        )
        .scalars()
        .all()
    )
    result: list[ChapterRead] = []
    for chapter in rows:
        accepted = get_accepted_chapter_revision(session, chapter.id)
        result.append(
            ChapterRead(
                id=chapter.id,
                volume_id=chapter.volume_id,
                title=chapter.title,
                pov=chapter.pov,
                accepted_chapter_revision_id=accepted.id if accepted else None,
                entry_handoff_id=None,
                created_at=chapter.created_at.isoformat(),
            )
        )
    return result
