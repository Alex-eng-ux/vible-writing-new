"""Task 4B 章节编排领域：聚合资格、场景队列、handoff 与重规划。

本模块集中实现章节级编排的领域规则，供章节图节点与 Worker 复用：

- 聚合资格：不再接受布尔 `entry_handoff_valid`，而是校验真实 handoff、来源
  章节修订与当前 accepted 指针。区分“可以生成 staged 章节版本”与“可以提交
  accepted 章节版本”。新章节（尚无 accepted 章节版本）允许首轮聚合，无需
  预先存在 accepted 章节版本；入口 handoff 校验按章节是否已有 accepted 指针
  区分（首轮无入口，自然放行；后续必须匹配 accepted 指针）。
- 影响闭包：当上游章节版本变化或反馈要求重做时，计算需要重新生成/校验的
  场景集合，避免误跳过受影响场景。
- handoff 创建与失效：只有 in_sync 且 entry_handoff_status=in_sync 的已接受
  章节修订才能生成 handoff；上游版本变化/回滚后一次调用递归失效整条下游链，
  并把受影响章节标记为 out_of_sync。
- 重规划继承：新计划场景通过 inheritance_map 显式继承满足新约束的旧已接受
  版本，新增场景使用 null 基线；旧运行与旧 staged 章节版本不得继续提交。
- 反馈恢复：章节反馈先生成受影响场景补丁队列，再按队列逐一校验并提交。

本模块只做领域判断与持久化写入，不碰 Agent；所有写入需在已通过 CommitGuard
的事务内调用。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Chapter,
    ChapterHandoff,
    ChapterPlanRevision,
    ChapterPlanRevisionLink,
    ChapterPlanSceneLink,
    ChapterRevision,
    Scene,
)
from ..errors import AppError
from .interfaces import CommandContext

# 聚合资格常量
AGGREGATION_OK = "aggregation_ok"
STALE_ENTRY = "stale_entry"
SCENE_NOT_ACCEPTED = "scene_not_accepted"
CHAPTER_NOT_IN_SYNC = "chapter_not_in_sync"
HANDOFF_CONFLICT = "CHAPTER_HANDOFF_CONFLICT"
FIRST_ROUND_CAPABLE = "first_round_capable"


@dataclass(frozen=True)
class ChapterAggregationEligibility:
    """章节聚合资格判定结果。

    字段：
        eligible: 是否允许生成 staged 章节版本。
        committable: 是否允许提交 accepted 章节版本（须入口/出口均满足）。
        status: 稳定阻断码（aggregation_ok 或具体失败码）。
        reason: 人类可读的原因。
        scene_ids: 参与聚合的场景 id 列表。
    """

    eligible: bool
    committable: bool
    status: str
    reason: str
    scene_ids: list[str]


def current_accepted_chapter_revision_id(session: Session, chapter_id: str) -> str | None:
    """返回章节显式 accepted 章节修订指针，无则返回 None。

    绝不按“最新行”推断 accepted；只用 `Chapter.accepted_chapter_revision_id`。
    该指针由 `commit_chapter_version` 写入。
    """
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        return None
    return chapter.accepted_chapter_revision_id


def current_accepted_scene_revision_id(session: Session, scene_id: str) -> str | None:
    """返回场景显式 accepted 场景修订指针，无则返回 None。

    绝不按“最新行”推断 accepted；只用 `Scene.accepted_scene_revision_id`。
    该指针由 `commit_scene_draft` / `commit_scene_change_set` 写入。
    """
    scene = session.get(Scene, scene_id)
    if scene is None:
        return None
    return scene.accepted_scene_revision_id


def valid_entry_handoff(
    session: Session,
    chapter_id: str,
    entry_handoff_id: str | None,
    entry_source_chapter_revision_id: str | None,
    entry_handoff_chain_hash: str | None,
) -> ChapterHandoff | None:
    """只返回满足以下条件的有效入口 handoff：真实存在、active、in_sync、
    来源修订为 accepted 且等于该章节当前 accepted 指针、链哈希匹配（若提供）。

    绝不接受伪造的 handoff：handoff 不存在、来源不存在/非 accepted、或来源
    不等于当前 accepted 指针时均返回 None。
    """
    if entry_handoff_id is None:
        return None
    handoff = session.get(ChapterHandoff, entry_handoff_id)
    if handoff is None or handoff.chapter_id != chapter_id:
        return None
    if handoff.status != "active" or handoff.entry_handoff_status != "in_sync":
        return None
    source_rev = session.get(ChapterRevision, handoff.source_chapter_revision_id)
    if source_rev is None or source_rev.status != "accepted":
        return None
    # 跨章节继承：handoff 的来源修订属于来源（前驱）章节，必须用来源章节的
    # accepted 指针校验，绝不与目标章节的指针比较。
    if handoff.source_chapter_revision_id != current_accepted_chapter_revision_id(
        session, source_rev.chapter_id
    ):
        return None
    if entry_source_chapter_revision_id is not None and (
        handoff.source_chapter_revision_id != entry_source_chapter_revision_id
    ):
        return None
    if entry_handoff_chain_hash is not None and handoff.chain_hash != entry_handoff_chain_hash:
        return None
    return handoff


def _scene_ids(session: Session, chapter_id: str) -> list[str]:
    accepted_link = session.execute(
        select(ChapterPlanRevisionLink)
        .join(ChapterPlanRevision, ChapterPlanRevision.id == ChapterPlanRevisionLink.plan_revision_id)
        .where(ChapterPlanRevisionLink.chapter_id == chapter_id, ChapterPlanRevision.status == "accepted")
    ).scalar_one_or_none()
    if accepted_link is not None:
        planned_scene_ids = [
            row[0]
            for row in session.execute(
                select(ChapterPlanSceneLink.scene_id)
                .where(ChapterPlanSceneLink.plan_revision_id == accepted_link.plan_revision_id)
                .order_by(ChapterPlanSceneLink.sort_order)
            ).all()
        ]
        if planned_scene_ids:
            return planned_scene_ids
    return [
        row[0]
        for row in session.execute(
            select(Scene.id).where(Scene.chapter_id == chapter_id).order_by(Scene.created_at)
        ).all()
    ]


def compute_aggregation_eligibility(
    session: Session,
    chapter_id: str,
    entry_handoff_id: str | None = None,
    entry_source_chapter_revision_id: str | None = None,
    entry_handoff_chain_hash: str | None = None,
) -> ChapterAggregationEligibility:
    """计算章节聚合资格（不再接受布尔 entry_handoff_valid）。

    参数：chapter_id 为章节 id；entry_handoff_id / entry_source_chapter_revision_id
    / entry_handoff_chain_hash 为入口承接的凭据。

    返回：`ChapterAggregationEligibility`。

    判定规则：
        - 章节必须存在且 chapter_sync_status=in_sync，否则 CHAPTER_NOT_IN_SYNC。
        - 若章节已有 accepted 指针：入口 handoff 必须有效（真实存在、active、
          in_sync、来源 accepted 且等于当前 accepted 指针），否则 STALE_ENTRY。
        - 若章节尚无 accepted 指针（新章节首轮）：允许无入口 handoff，直接进入
          场景校验（首轮聚合）。
        - 章节内每个场景都必须有 accepted 版本，否则 SCENE_NOT_ACCEPTED。
        - 全部满足时 eligible=True；仅当入口有效且出口状态满足时 committable=True。
    """
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        return ChapterAggregationEligibility(
            False, False, CHAPTER_NOT_IN_SYNC, "chapter does not exist", []
        )
    if chapter.chapter_sync_status != "in_sync":
        return ChapterAggregationEligibility(
            False,
            False,
            CHAPTER_NOT_IN_SYNC,
            f"chapter sync status is {chapter.chapter_sync_status}",
            [],
        )
    has_accepted = current_accepted_chapter_revision_id(session, chapter_id) is not None
    if has_accepted:
        handoff = valid_entry_handoff(
            session,
            chapter_id,
            entry_handoff_id,
            entry_source_chapter_revision_id,
            entry_handoff_chain_hash,
        )
        if handoff is None:
            return ChapterAggregationEligibility(
                False, False, STALE_ENTRY, "entry handoff is stale or invalid", []
            )
    scene_ids = _scene_ids(session, chapter_id)
    accepted = _scene_accepted_revisions(session, chapter_id)
    missing = [sid for sid in scene_ids if sid not in accepted]
    if missing:
        return ChapterAggregationEligibility(
            False,
            False,
            SCENE_NOT_ACCEPTED,
            f"scenes missing accepted revisions: {missing}",
            scene_ids,
        )
    if not has_accepted:
        return ChapterAggregationEligibility(
            True,
            True,
            FIRST_ROUND_CAPABLE,
            "first round aggregation: no accepted chapter revision yet",
            scene_ids,
        )
    return ChapterAggregationEligibility(
        True, True, AGGREGATION_OK, "all scenes accepted and entry in sync", scene_ids
    )


def _scene_accepted_revisions(session: Session, chapter_id: str) -> dict[str, str]:
    """返回章节内每个场景的当前 accepted 修订 id（用显式指针，无则不在结果中）。"""
    scene_ids = _scene_ids(session, chapter_id)
    result: dict[str, str] = {}
    for sid in scene_ids:
        pointer = current_accepted_scene_revision_id(session, sid)
        if pointer is not None:
            result[sid] = pointer
    return result


def compute_scene_impact_closure(
    session: Session,
    chapter_id: str,
    affected_scene_ids: list[str],
) -> list[str]:
    """计算受影响场景闭包：受影响场景 + 其下游同章场景。

    参数：chapter_id 为章节 id；affected_scene_ids 为直接受影响场景 id 列表。
    返回：按章节内顺序排序的受影响场景 id 闭包。
    """
    all_scenes = _scene_ids(session, chapter_id)
    affected = set(affected_scene_ids)
    closure: list[str] = []
    active = False
    for sid in all_scenes:
        if sid in affected:
            active = True
        if active:
            closure.append(sid)
    return closure


def create_handoff_for_chapter_revision(
    session: Session,
    source_chapter_revision_id: str,
    target_chapter_id: str,
    chain_hash: str,
    ctx: CommandContext,
) -> ChapterHandoff:
    """为下游章节创建入口 handoff。

    C1 的 accepted 版本创建的是 C2 的入口 handoff：`source_chapter_revision_id`
    指向 C1 的 accepted 修订，`chapter_id` 指向下游章节 C2。

    参数：source_chapter_revision_id 为来源（C1）已接受修订 id；target_chapter_id
    为下游（C2）章节 id；chain_hash 为链哈希；ctx 为命令上下文。
    返回：新建的 ChapterHandoff 模型。

    失败条件：来源修订不存在或非 accepted 抛 CHAPTER_OUT_OF_SYNC；来源章节未
    in_sync 抛 CHAPTER_NOT_IN_SYNC。

    副作用：将下游章节同章其他 active handoff 置为失效，并新增一个 in_sync 的
    handoff。
    """
    rev = session.get(ChapterRevision, source_chapter_revision_id)
    if rev is None or rev.status != "accepted":
        raise AppError("CHAPTER_OUT_OF_SYNC", "handoff requires an accepted chapter revision")
    source_chapter = session.get(Chapter, rev.chapter_id)
    if source_chapter is None or source_chapter.chapter_sync_status != "in_sync":
        raise AppError("CHAPTER_NOT_IN_SYNC", "source chapter is not in sync")

    old_handoffs = session.execute(
        select(ChapterHandoff).where(
            ChapterHandoff.chapter_id == target_chapter_id, ChapterHandoff.status == "active"
        )
    ).scalars().all()
    for old in old_handoffs:
        old.status = "inactive"
    target_chapter = session.get(Chapter, target_chapter_id)
    if target_chapter is not None:
        target_chapter.entry_handoff_status = "in_sync"
    handoff = ChapterHandoff(
        chapter_id=target_chapter_id,
        source_chapter_revision_id=rev.id,
        entry_handoff_status="in_sync",
        chain_hash=chain_hash,
        status="active",
    )
    session.add(handoff)
    session.flush()
    return handoff


def invalidate_downstream_handoffs(
    session: Session,
    chapter_id: str,
    volumes_order: list[str] | None = None,
    chapters_order: dict[str, int] | None = None,
) -> list[str]:
    """上游章节版本变化后，一次调用递归失效整条下游链并更新章节状态。

    参数：chapter_id 为发生变化的章节 id；volumes_order / chapters_order 可选
    顺序参数（本实现通过 handoff 来源链递归，不依赖顺序参数）。
    返回：被标记为 stale/out_of_sync 的章节 id 列表（含传递下游）。

    副作用：沿入口祖先链把下游章节的 active handoff 置为
    entry_handoff_status=stale，并把受影响下游章节的 chapter_sync_status 置为
    out_of_sync，使其无法再被读取/聚合/作为新运行入口。递归一次完成，不经
    多级调用。
    """
    affected: list[str] = []
    visited: set[str] = set()
    pending = [chapter_id]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        # 找到直接依赖当前章节的下游：其入口 handoff 的 source 属于当前章节的
        # accepted 修订。
        downstream = session.execute(
            select(ChapterHandoff.chapter_id).where(
                ChapterHandoff.source_chapter_revision_id.in_(
                    select(ChapterRevision.id).where(ChapterRevision.chapter_id == current)
                )
            )
        ).scalars().all()
        for downstream_id in downstream:
            handoffs = session.execute(
                select(ChapterHandoff).where(
                    ChapterHandoff.chapter_id == downstream_id,
                    ChapterHandoff.status == "active",
                )
            ).scalars().all()
            for h in handoffs:
                h.entry_handoff_status = "stale"
            chapter = session.get(Chapter, downstream_id)
            if chapter is not None:
                chapter.chapter_sync_status = "out_of_sync"
            affected.append(downstream_id)
            pending.append(downstream_id)
    return affected


def build_inheritance_map(
    session: Session,
    chapter_id: str,
    new_scene_keys: list[str],
    previous_scene_keys: list[str],
    previous_accepted: dict[str, str],
) -> dict[str, str]:
    """为重规划构建显式场景继承映射。

    参数：chapter_id 为章节 id；new_scene_keys 为新计划场景的 client_key 列表；
    previous_scene_keys 为旧计划场景的 client_key 列表；previous_accepted 为旧
    场景 key -> accepted 修订 id 的映射。
    返回：dict client_key -> accepted 修订 id（或空串表示新增场景需 null 基线）。

    规则：只继承仍满足新计划约束的旧已接受版本（此处按 key 配对继承）；新增
    场景使用空串表示 null 基线；旧运行与旧 staged 章节版本不得继续提交。
    """
    inheritance: dict[str, str] = {}
    for key in new_scene_keys:
        if key in previous_scene_keys and key in previous_accepted:
            inheritance[key] = previous_accepted[key]
        else:
            inheritance[key] = ""
    return inheritance


def build_scene_feedback_queue(
    session: Session,
    chapter_id: str,
    feedback_scene_ids: list[str],
) -> list[str]:
    """按章节反馈生成需重做的场景补丁队列。

    参数：chapter_id 为章节 id；feedback_scene_ids 为直接受反馈影响的场景 id。
    返回：按章节顺序排列的场景 id 队列（含影响闭包）。

    副作用：只读，不写库。
    """
    return compute_scene_impact_closure(session, chapter_id, feedback_scene_ids)
