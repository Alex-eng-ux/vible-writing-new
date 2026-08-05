"""Context 元数据与向量检索器的实现。

本模块提供两种检索器：SqlMetadataRetriever 从数据库读取项目/场景/
版本范围内的可信来源，SqlVectorRetriever 作为向量补充检索的占位实现。
两者都只读取、不写任何数据，并严格遵守传入的来源版本白名单，绝不
越出项目/场景/版本作用域。

核心约束：
- 检索范围被限定在 scene -> chapter -> project 的所属链上，防止跨项目
  或跨版本泄漏；
- 只接受状态为 accepted 的版本与 active 的设定事实（canon）；
- 向量检索不可用或白名单为空时降级为空列表，属于降级而非硬失败；
- 本模块不直接写库，也不负责预算分配（见 composer）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    CanonFact,
    Chapter,
    Entity,
    Scene,
    SceneRevision,
    Volume,
)
from ..errors import AppError
from .models import ContextItem, MetadataRetriever, SceneRequest, SourceType, VectorRetriever


def _estimator(text: str) -> int:
    """确定性 token 估算器（约 4 字符记 1 token）。

    返回至少为 1 的整数，保证空文本也占用最小预算，避免除零或负值。
    """
    return max(1, (len(text) + 3) // 4)


def _item(
    source_id: str,
    source_type: SourceType,
    content: str,
    priority: int,
    source_revision_id: str | None = None,
    metadata: dict | None = None,
) -> ContextItem:
    """构造一个 ContextItem 并自动计算 token 预估值。

    参数：
        source_id: 来源唯一标识。
        source_type: 来源类型。
        content: 上下文内容文本。
        priority: 优先级（0 为强制项，>0 为可选项）。
        source_revision_id: 来源版本 ID，可为 None。
        metadata: 附加元数据，None 时记为空字典。

    返回：
        填好 token_estimate 与 truncation_reason=None 的 ContextItem。
    """
    return ContextItem(
        source_id=source_id,
        source_type=source_type,
        source_revision_id=source_revision_id,
        priority=priority,
        content=content,
        token_estimate=_estimator(content),
        truncation_reason=None,
        metadata=metadata or {},
    )


def _project_id_of(session: Session, chapter: Chapter) -> str:
    """依据章节所属章节卷反查项目 ID。

    失败条件：章节卷不存在时抛出 CONTEXT_SOURCE_UNAVAILABLE。
    """
    volume = session.get(Volume, chapter.volume_id)
    if volume is None:
        raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "volume does not exist")
    return volume.project_id


class SqlMetadataRetriever:
    """元数据检索实现，限定在项目/场景/版本作用域内。

    只读取已接受来源，绝不越过传入的来源版本白名单；不写任何数据。

    失败条件：场景或章节不存在时抛出 CONTEXT_SOURCE_UNAVAILABLE。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def retrieve(self, request: SceneRequest, source_revision_ids: list[str]) -> list[ContextItem]:
        """按请求与版本白名单检索该项目下的上下文来源。

        参数：
            request: 场景生成请求（取其 scene_id）。
            source_revision_ids: 允许纳入的来源版本 ID 白名单。

        返回：
            按优先级组装的 ContextItem 列表：P0 当前场景骨架、P2 项目
            active 设定事实、P3 白名单内 accepted 版本与项目实体。

        失败条件：
            场景或章节不存在时抛 CONTEXT_SOURCE_UNAVAILABLE；白名单内
            版本若不属于该场景或未 accepted 会被过滤掉。
        """
        scene_id = request["scene_id"]
        scene = self._session.get(Scene, scene_id)
        if scene is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "scene does not exist")
        chapter = self._session.get(Chapter, scene.chapter_id)
        if chapter is None:
            raise AppError("CONTEXT_SOURCE_UNAVAILABLE", "chapter does not exist")
        project_id = _project_id_of(self._session, chapter)

        items: list[ContextItem] = []

        # P0: current scene contract / accepted baseline.
        items.append(
            _item(
                scene_id,
                "scene",
                f"Scene: {scene.title}\n{scene.scene_brief}",
                priority=0,
                source_revision_id=None,
                metadata={"chapter_id": scene.chapter_id},
            )
        )

        # P3: accepted scene revisions whose ids are in the whitelist.
        if source_revision_ids:
            revs = self._session.execute(
                select(SceneRevision).where(
                    SceneRevision.id.in_(source_revision_ids),
                    SceneRevision.scene_id == scene_id,
                    SceneRevision.status == "accepted",
                )
            ).scalars().all()
            for rev in revs:
                items.append(
                    _item(
                        rev.id,
                        "revision",
                        rev.content,
                        priority=3,
                        source_revision_id=rev.id,
                        metadata={"scene_id": scene_id},
                    )
                )

        # P2: accepted canon facts for the project.
        canon_facts = self._session.execute(
            select(CanonFact).where(
                CanonFact.project_id == project_id,
                CanonFact.status == "active",
            )
        ).scalars().all()
        for fact in canon_facts:
            items.append(
                _item(
                    fact.id,
                    "canon",
                    fact.fact_text,
                    priority=2,
                    source_revision_id=None,
                    metadata={"entity_id": fact.entity_id},
                )
            )

        # P3: project entities.
        entities = self._session.execute(
            select(Entity).where(Entity.project_id == project_id)
        ).scalars().all()
        for ent in entities:
            items.append(
                _item(
                    ent.id,
                    "entity",
                    f"{ent.name} ({ent.kind})",
                    priority=3,
                    source_revision_id=None,
                    metadata={},
                )
            )

        return items


class SqlVectorRetriever:
    """向量补充检索实现，保持在允许的来源白名单内。

    向量服务不可用或白名单为空时返回空列表（降级而非硬失败）。
    """

    def __init__(self, session: Session, available: bool = True) -> None:
        self._session = session
        self._available = available

    def retrieve(self, query: str, allowed_source_ids: list[str], limit: int) -> list[ContextItem]:
        """按允许来源白名单执行向量补充检索。

        参数：
            query: 检索查询文本。
            allowed_source_ids: 允许作为检索结果的来源 ID 白名单。
            limit: 返回条数上限。

        返回：
            命中条目列表；当前 Task 3 未生成向量，恒返回空列表。真实向量
            库实现必须把结果限制在白名单内，不可用时降级为空。
        """
        if not self._available:
            return []
        if not allowed_source_ids:
            return []
        # Task 3 does not generate embeddings; a real vector store would be
        # restricted to the allowed_source_ids whitelist. When unavailable,
        # degrade to an empty result.
        return []


def metadata_retriever(session: Session) -> MetadataRetriever:
    """构造元数据检索器工厂。

    参数：
        session: 数据库会话。

    返回：
        一个 SqlMetadataRetriever 实例，作为 MetadataRetriever 端口注入。
    """
    return SqlMetadataRetriever(session)


def vector_retriever(session: Session, available: bool = True) -> VectorRetriever:
    """构造向量检索器工厂。

    参数：
        session: 数据库会话。
        available: 向量服务是否可用，False 时检索降级为空。

    返回：
        一个 SqlVectorRetriever 实例，作为 VectorRetriever 端口注入。
    """
    return SqlVectorRetriever(session, available)
