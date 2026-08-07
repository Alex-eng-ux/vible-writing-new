"""Add chapter planning workflow persistence.

Revision ID: a1b2c3d4e5f6
Revises: v1_rc_observability_metadata
Create Date: 2026-08-06 21:45:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.models import UTCDateTime

revision = "a1b2c3d4e5f6"
down_revision = "v1_rc_observability_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为章节规划候选、讨论记录和固定场景映射创建持久化结构。"""
    op.add_column(
        "chapter_plan_revisions",
        sa.Column("candidate_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "chapter_plan_revisions",
        sa.Column("planning_lineage_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "chapter_plan_revisions",
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "chapter_plan_revisions",
        sa.Column(
            "contract_field_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "chapter_plan_revisions",
        sa.Column(
            "scene_briefs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "chapter_plan_revisions",
        sa.Column(
            "unresolved_assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "chapter_plan_revisions",
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
    )
    op.create_index(
        "ix_chapter_plan_revisions_planning_lineage_id",
        "chapter_plan_revisions",
        ["planning_lineage_id"],
        unique=False,
    )
    op.create_index(
        "ix_chapter_plan_revisions_source_run_id",
        "chapter_plan_revisions",
        ["source_run_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_chapter_plan_source_run", "chapter_plan_revisions", ["source_run_id"]
    )

    op.create_table(
        "chapter_plan_discussion_messages",
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("planning_lineage_id", sa.String(length=36), nullable=False),
        sa.Column("message_sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("supersedes_run_id", sa.String(length=36), nullable=True),
        sa.Column("checkpoint_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("message_id"),
        sa.UniqueConstraint(
            "planning_lineage_id",
            "message_sequence",
            name="uq_plan_message_sequence",
        ),
    )
    op.create_index(
        "ix_chapter_plan_discussion_messages_chapter_id",
        "chapter_plan_discussion_messages",
        ["chapter_id"],
        unique=False,
    )
    op.create_index(
        "ix_chapter_plan_discussion_messages_planning_lineage_id",
        "chapter_plan_discussion_messages",
        ["planning_lineage_id"],
        unique=False,
    )

    op.create_table(
        "chapter_plan_questions",
        sa.Column("question_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("planning_lineage_id", sa.String(length=36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("impact", sa.String(length=255), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("question_id"),
    )
    op.create_index(
        "ix_chapter_plan_questions_chapter_id",
        "chapter_plan_questions",
        ["chapter_id"],
        unique=False,
    )
    op.create_index(
        "ix_chapter_plan_questions_planning_lineage_id",
        "chapter_plan_questions",
        ["planning_lineage_id"],
        unique=False,
    )

    op.create_table(
        "chapter_plan_proposals",
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("planning_lineage_id", sa.String(length=36), nullable=False),
        sa.Column("field_path", sa.String(length=255), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=sa.text("'ai'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("proposal_id"),
        sa.UniqueConstraint(
            "planning_lineage_id",
            "field_path",
            "source_run_id",
            name="uq_plan_proposal_source",
        ),
    )
    op.create_index(
        "ix_chapter_plan_proposals_chapter_id",
        "chapter_plan_proposals",
        ["chapter_id"],
        unique=False,
    )
    op.create_index(
        "ix_chapter_plan_proposals_planning_lineage_id",
        "chapter_plan_proposals",
        ["planning_lineage_id"],
        unique=False,
    )

    op.create_table(
        "chapter_plan_scene_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("plan_revision_id", sa.String(length=36), nullable=False),
        sa.Column("client_key", sa.String(length=120), nullable=False),
        sa.Column("scene_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["plan_revision_id"], ["chapter_plan_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_revision_id", "client_key", name="uq_plan_scene_client_key"
        ),
        sa.UniqueConstraint(
            "plan_revision_id", "sort_order", name="uq_plan_scene_order"
        ),
    )
    op.create_index(
        "ix_chapter_plan_scene_links_chapter_id",
        "chapter_plan_scene_links",
        ["chapter_id"],
        unique=False,
    )
    op.create_index(
        "ix_chapter_plan_scene_links_plan_revision_id",
        "chapter_plan_scene_links",
        ["plan_revision_id"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系删除章节规划工作流持久化结构。"""
    op.drop_index(
        "ix_chapter_plan_scene_links_plan_revision_id",
        table_name="chapter_plan_scene_links",
    )
    op.drop_index(
        "ix_chapter_plan_scene_links_chapter_id",
        table_name="chapter_plan_scene_links",
    )
    op.drop_table("chapter_plan_scene_links")

    op.drop_index(
        "ix_chapter_plan_proposals_planning_lineage_id",
        table_name="chapter_plan_proposals",
    )
    op.drop_index(
        "ix_chapter_plan_proposals_chapter_id",
        table_name="chapter_plan_proposals",
    )
    op.drop_table("chapter_plan_proposals")

    op.drop_index(
        "ix_chapter_plan_questions_planning_lineage_id",
        table_name="chapter_plan_questions",
    )
    op.drop_index(
        "ix_chapter_plan_questions_chapter_id",
        table_name="chapter_plan_questions",
    )
    op.drop_table("chapter_plan_questions")

    op.drop_index(
        "ix_chapter_plan_discussion_messages_planning_lineage_id",
        table_name="chapter_plan_discussion_messages",
    )
    op.drop_index(
        "ix_chapter_plan_discussion_messages_chapter_id",
        table_name="chapter_plan_discussion_messages",
    )
    op.drop_table("chapter_plan_discussion_messages")

    op.drop_constraint(
        "uq_chapter_plan_source_run", "chapter_plan_revisions", type_="unique"
    )
    op.drop_index(
        "ix_chapter_plan_revisions_source_run_id",
        table_name="chapter_plan_revisions",
    )
    op.drop_index(
        "ix_chapter_plan_revisions_planning_lineage_id",
        table_name="chapter_plan_revisions",
    )
    for column in (
        "idempotency_key",
        "unresolved_assumptions",
        "scene_briefs",
        "contract_field_provenance",
        "source_run_id",
        "planning_lineage_id",
        "candidate_version",
    ):
        op.drop_column("chapter_plan_revisions", column)
