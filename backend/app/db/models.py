from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

TZ = UTC


def utcnow() -> datetime:
    return datetime.now(TZ)


class UTCDateTime(TypeDecorator):
    """Store timezone-aware UTC timestamps in a TIMESTAMPTZ column."""

    impl = TIMESTAMP(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=TZ)
        return value


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """Generate a compact UUID string suitable for a primary key."""
    return str(uuid.uuid4())


class NovelProject(Base):
    __tablename__ = "novel_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    genre: Mapped[str] = mapped_column(String(120), nullable=False)
    target_reader: Mapped[str] = mapped_column(String(255), nullable=False)
    default_style: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Volume(Base):
    __tablename__ = "volumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("novel_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    mainline: Mapped[str] = mapped_column(Text, nullable=False)
    time_range: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    volume_id: Mapped[str] = mapped_column(ForeignKey("volumes.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    pov: Mapped[str] = mapped_column(String(120), nullable=False)
    chapter_intent: Mapped[dict] = mapped_column(JSONB, nullable=False)
    chapter_sync_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entry_handoff_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accepted_chapter_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "chapter_revisions.id",
            ondelete="SET NULL",
            name="fk_chapters_accepted_revision",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    scene_brief: Mapped[dict] = mapped_column(JSONB, nullable=False)
    accepted_scene_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "scene_revisions.id",
            ondelete="SET NULL",
            name="fk_scenes_accepted_revision",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ChapterPlanRevision(Base):
    __tablename__ = "chapter_plan_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id", ondelete="RESTRICT"), nullable=False, index=True)
    parent_plan_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chapter_contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ChapterPlanRevisionLink(Base):
    """Current-pointer linkage for a chapter's accepted plan revision."""

    __tablename__ = "chapter_plan_revision_links"

    chapter_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_revision_id: Mapped[str] = mapped_column(
        ForeignKey("chapter_plan_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class SceneRevision(Base):
    __tablename__ = "scene_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="RESTRICT"), nullable=False, index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="staged")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_scene_revisions_scene_parent", "scene_id", "parent_revision_id"),
    )


class SceneDraftArtifact(Base):
    __tablename__ = "scene_draft_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="RESTRICT"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    manual_command_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_scene_draft_artifact_run_agent", "generation_run_id", "agent_run_id", "idempotency_key"),
    )


class ChangeSet(Base):
    __tablename__ = "change_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="RESTRICT"), nullable=False, index=True)
    base_scene_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation_format: Mapped[str] = mapped_column(String(32), nullable=False)
    operations: Mapped[list] = mapped_column(JSONB, nullable=False)
    base_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    root_draft_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("scene_draft_artifacts.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ChapterRevision(Base):
    __tablename__ = "chapter_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id", ondelete="RESTRICT"), nullable=False, index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="staged")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ChapterRevisionScene(Base):
    """Fixed scene version list referenced by a chapter revision."""

    __tablename__ = "chapter_revision_scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_revision_id: Mapped[str] = mapped_column(
        ForeignKey("chapter_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scene_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("chapter_revision_id", "scene_id", name="uq_chapter_rev_scene"),
    )


class ChapterHandoff(Base):
    __tablename__ = "chapter_handoffs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_chapter_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entry_handoff_status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_sync")
    chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "name", "kind", name="uq_entity"),)


class CanonFact(Base):
    __tablename__ = "canon_facts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class TimelineEvent(Base):
    """正式时间线事件：作者确认后的故事内事件。

    对应计划书 3.1 节的 `TimelineEvent`：记录故事内时间、叙事出现顺序、
    参与实体、地点与前置/后续关系。只能由章节级 `confirm` 在事务中生成，
    绝不作为 Agent 或普通正文节点的公共写入口。
    """

    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_text: Mapped[str] = mapped_column(Text, nullable=False)
    story_time: Mapped[dict] = mapped_column(JSONB, nullable=False)
    entities: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class PlotThread(Base):
    """正式剧情线：作者确认后的伏笔/冲突线记录。

    对应计划书 3.1 节的 `PlotThread`：记录伏笔、冲突线的开启/推进/回收
    状态与计划回收位置。只能由章节级 `confirm` 在事务中生成，绝不作为
    Agent 或普通正文节点的公共写入口。
    """

    __tablename__ = "plot_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    thread_text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    planned_resolution: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class FactCandidate(Base):
    __tablename__ = "fact_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False, default="fact")
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_draft_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_change_set_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    local_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "(source_revision_id IS NOT NULL)::int + (source_draft_artifact_id IS NOT NULL)::int "
            "+ (source_change_set_id IS NOT NULL)::int = 1",
            name="ck_candidate_source_exactly_one",
        ),
        UniqueConstraint(
            "project_id",
            "chapter_id",
            "scene_id",
            "scope",
            "source_identity",
            "candidate_type",
            "candidate_fingerprint",
            name="uq_candidate_source_fingerprint",
        ),
    )


class TimelineEventCandidate(Base):
    __tablename__ = "timeline_event_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False, default="timeline_event")
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_draft_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_change_set_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    local_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "(source_revision_id IS NOT NULL)::int + (source_draft_artifact_id IS NOT NULL)::int "
            "+ (source_change_set_id IS NOT NULL)::int = 1",
            name="ck_timeline_source_exactly_one",
        ),
        UniqueConstraint(
            "project_id",
            "chapter_id",
            "scene_id",
            "scope",
            "source_identity",
            "candidate_type",
            "candidate_fingerprint",
            name="uq_timeline_source_fingerprint",
        ),
    )


class PlotThreadUpdate(Base):
    __tablename__ = "plot_thread_updates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False, default="plot_thread")
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_draft_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_change_set_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    local_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "(source_revision_id IS NOT NULL)::int + (source_draft_artifact_id IS NOT NULL)::int "
            "+ (source_change_set_id IS NOT NULL)::int = 1",
            name="ck_plot_thread_source_exactly_one",
        ),
        UniqueConstraint(
            "project_id",
            "chapter_id",
            "scene_id",
            "scope",
            "source_identity",
            "candidate_type",
            "candidate_fingerprint",
            name="uq_plot_thread_source_fingerprint",
        ),
    )


class Foreshadowing(Base):
    __tablename__ = "foreshadowings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    supersedes_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_plan_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Task 5B：请求类型与当前决策目标（run_scope 由 chapter_id/scene_id 推断）。
    request_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_target: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    run_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    write_owner_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    write_owner_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    write_fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_durable_node: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Task 5B 复核：运行快照所需的可恢复运行状态（pending_clarification/paused）。
    pending_node: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    clarification_questions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Task 5B 复核：不可变规范化运行输入信封。Worker 仅凭 run_id 即可重建与
    # 首次请求完全一致的输入；重试绝不重新读取客户端输入。
    normalized_input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Task 5C：Canon 运行所消费的 accepted 来源版本（章节或场景）。
    # 供 chapter_revision.accepted outbox 消费者按 (chapter_id, accepted 版本)
    # 幂等去重创建章节 Canon 运行；普通运行该字段为 None。
    canon_source_revision_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    trace_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_run_id: Mapped[str] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class RunDecision(Base):
    __tablename__ = "run_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    request_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("generation_run_id", "target", "idempotency_key", name="uq_run_decision"),
    )


class AuthorFeedback(Base):
    __tablename__ = "author_feedbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    manual_command_id: Mapped[str] = mapped_column(String(36), nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class CanonDecisionRecord(Base):
    __tablename__ = "canon_decision_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    local_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    candidate_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class RunEvent(Base):
    __tablename__ = "run_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Task 9（迁移 v1_rc_observability_metadata）：持久化事件注册表审计字段，
    # 默认值与 Task 5B SSE envelope 一致；不改变既有事件语义。
    payload_schema: Mapped[str] = mapped_column(
        String(64), nullable=False, default="run-event.v1", server_default="run-event.v1"
    )
    redaction_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="redaction.v1", server_default="redaction.v1"
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("generation_run_id", "sequence", name="uq_run_event_sequence"),
    )


class RunEventConsumerCursor(Base):
    __tablename__ = "run_event_consumer_cursors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    consumer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    stream_key: Mapped[str] = mapped_column(String(120), nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("consumer_name", "stream_key", name="uq_consumer_cursor"),
    )


class RunOutboxRecord(Base):
    __tablename__ = "run_outbox_records"

    outbox_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Task 5B 复核：发布租约（publishing 超时恢复）。发布者声明 ownership 并设
    # 到期时间；进程崩溃后租约过期的记录可被其他发布者重新领取。
    publisher_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    publisher_lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    producer_command_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", "producer_command_id", name="uq_outbox_resource"),
    )


class RunLease(Base):
    __tablename__ = "run_leases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(String(120), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[str] = mapped_column(String(120), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class CommandIdempotencyRecord(Base):
    __tablename__ = "command_idempotency_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    resource_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    operation_name: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    claim_lease: Mapped[str | None] = mapped_column(String(120), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    first_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manual_command_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("resource_scope", "operation_name", "idempotency_key", name="uq_idempotency_key"),
    )


class ContextManifest(Base):
    __tablename__ = "context_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_index: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("generation_run_id", name="uq_context_manifest_run"),)


class SceneSnapshot(Base):
    __tablename__ = "scene_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    scene_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ChapterSnapshot(Base):
    __tablename__ = "chapter_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    chapter_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
