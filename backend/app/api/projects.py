"""项目资源 API：创建项目、创建卷、读取项目与卷列表。

创建命令都是幂等命令：缺 Idempotency-Key 返回 COMMAND_CONTEXT_MISMATCH，
同键同指纹重放相同结果，同键异指纹返回 IDEMPOTENCY_KEY_REUSE。
资源命令的成功响应 run_id 恒为 null。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import NovelProject, Volume
from ..domain.commit_guard import CommitGuard
from ..domain.idempotency import fingerprint
from ..domain.resources import create_project, create_volume
from ..errors import AppError
from ..services.deletion import delete_project
from .commands import execute_command
from .deps import get_actor_id, get_db, get_idempotency_key
from .schemas import ProjectCreate, ProjectRead, ResourceCreated, VolumeCreate, VolumeRead

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", status_code=201, response_model=ResourceCreated)
def post_project(
    body: ProjectCreate,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> ResourceCreated:
    """创建项目。先做请求指纹，再经 CommitGuard 校验后由领域服务建库。"""
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        CommitGuard(session).validate(
            "resource_create", actor_id, None, idempotency_key, []
        )
        project = create_project(
            session,
            body.name,
            body.genre,
            body.target_reader,
            body.default_style,
            {"actor_id": actor_id, "idempotency_key": idempotency_key},
        )
        response = {
            "id": project.id,
            "type": "project",
            "parent_id": None,
            "version": 1,
            "created_at": project.created_at.isoformat(),
        }
        return response, project.id

    return ResourceCreated(
        **execute_command(
            session, "project", "project_create", idempotency_key, request_fp, run
        )
    )


@router.post("/{project_id}/volumes", status_code=201, response_model=ResourceCreated)
def post_volume(
    project_id: str,
    body: VolumeCreate,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
    actor_id: str = Depends(get_actor_id),
) -> ResourceCreated:
    """在指定项目下创建卷；父项目不存在时返回 404 错误信封。"""
    if session.get(NovelProject, project_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "project not found")
    request_fp = fingerprint(body.model_dump())

    def run(manual_command_id: str | None) -> tuple[dict, str | None]:
        CommitGuard(session).validate(
            "resource_create", actor_id, None, idempotency_key, []
        )
        volume = create_volume(
            session,
            project_id,
            body.name,
            body.goal,
            body.mainline,
            body.time_range,
            {"actor_id": actor_id, "idempotency_key": idempotency_key},
        )
        response = {
            "id": volume.id,
            "type": "volume",
            "parent_id": project_id,
            "version": 1,
            "created_at": volume.created_at.isoformat(),
        }
        return response, volume.id

    return ResourceCreated(
        **execute_command(
            session,
            f"project:{project_id}",
            "volume_create",
            idempotency_key,
            request_fp,
            run,
        )
    )


@router.get("", response_model=list[ProjectRead])
def list_projects(
    session: Session = Depends(get_db),
) -> list[ProjectRead]:
    """列出全部项目，按创建时间排序（Task 7A 追加，只读）。"""
    rows = (
        session.execute(
            select(NovelProject).order_by(NovelProject.created_at)
        )
        .scalars()
        .all()
    )
    return [
        ProjectRead(
            id=p.id,
            name=p.name,
            genre=p.genre,
            target_reader=p.target_reader,
            default_style=p.default_style,
            created_at=p.created_at.isoformat(),
        )
        for p in rows
    ]


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: str,
    session: Session = Depends(get_db),
) -> ProjectRead:
    """读取项目详情；不存在时返回 404 错误信封。"""
    project = session.get(NovelProject, project_id)
    if project is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "project not found")
    return ProjectRead(
        id=project.id,
        name=project.name,
        genre=project.genre,
        target_reader=project.target_reader,
        default_style=project.default_style,
        created_at=project.created_at.isoformat(),
    )


@router.delete("/{project_id}", status_code=204)
def remove_project(
    project_id: str,
    idempotency_key: str = Depends(get_idempotency_key),
    session: Session = Depends(get_db),
) -> Response:
    """删除项目及其全部后代资源；删除在当前事务提交前保持原子性。"""
    delete_project(session, project_id)
    session.commit()
    return Response(status_code=204)


@router.get("/{project_id}/volumes", response_model=list[VolumeRead])
def list_volumes(
    project_id: str,
    session: Session = Depends(get_db),
) -> list[VolumeRead]:
    """列出项目下的卷，按创建时间排序。"""
    if session.get(NovelProject, project_id) is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "project not found")
    rows = (
        session.execute(
            select(Volume).where(Volume.project_id == project_id).order_by(Volume.created_at)
        )
        .scalars()
        .all()
    )
    return [
        VolumeRead(
            id=v.id,
            project_id=v.project_id,
            name=v.name,
            goal=v.goal,
            mainline=v.mainline,
            time_range=v.time_range,
            created_at=v.created_at.isoformat(),
        )
        for v in rows
    ]
