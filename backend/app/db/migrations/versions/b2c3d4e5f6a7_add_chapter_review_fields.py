"""Add durable chapter review result fields.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为章节版本增加审校问题、摘要和来源运行的持久化列。"""
    op.add_column(
        "chapter_revisions",
        sa.Column(
            "review_issues",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "chapter_revisions",
        sa.Column(
            "review_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "chapter_revisions",
        sa.Column("review_run_id", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    """移除章节版本审校结果列。"""
    op.drop_column("chapter_revisions", "review_run_id")
    op.drop_column("chapter_revisions", "review_summary")
    op.drop_column("chapter_revisions", "review_issues")
