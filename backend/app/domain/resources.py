"""资源根节点领域：项目（NovelProject）与卷（Volume）的创建。

资源根节点不涉及运行身份或版本基线，仅需资源命令上下文
（actor_id 与幂等键）。创建后须在调用方事务内提交。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import NovelProject, Volume
from .interfaces import ResourceCommandContext


def create_project(
    session: Session,
    name: str,
    genre: str,
    target_reader: str,
    default_style: str,
    ctx: ResourceCommandContext,
) -> NovelProject:
    """创建一个新的项目（NovelProject）。

    参数：session 为会话；name 为项目名；genre 为题材；target_reader 为
    目标读者；default_style 为默认风格；ctx 为资源命令上下文。
    返回：新建的 NovelProject。
    副作用：向会话新增项目并 flush；须在调用方事务内提交。
    """
    project = NovelProject(
        name=name,
        genre=genre,
        target_reader=target_reader,
        default_style=default_style,
    )
    session.add(project)
    session.flush()
    return project


def create_volume(
    session: Session,
    project_id: str,
    name: str,
    goal: str,
    mainline: str,
    time_range: str,
    ctx: ResourceCommandContext,
) -> Volume:
    """在指定项目下创建一个新的卷（Volume）。

    参数：session 为会话；project_id 为所属项目 id；name 为卷名；goal 为
    目标；mainline 为主线；time_range 为时间范围；ctx 为资源命令上下文。
    返回：新建的 Volume。
    副作用：向会话新增卷并 flush；须在调用方事务内提交。
    """
    volume = Volume(
        project_id=project_id,
        name=name,
        goal=goal,
        mainline=mainline,
        time_range=time_range,
    )
    session.add(volume)
    session.flush()
    return volume
