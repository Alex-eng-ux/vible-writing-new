"""add GenerationRun.canon_source_revision_id

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-08-04 20:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f8a9b0c1d2e3'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 GenerationRun 添加 Canon 来源版本列。

    canon_source_revision_id 记录 Canon 运行所消费的 accepted 来源版本（章节
    或场景）。chapter_revision.accepted outbox 消费者按
    (chapter_id, accepted_chapter_revision_id) 幂等去重创建章节 Canon 运行；
    普通运行该字段为 None。
    """
    op.add_column(
        'generation_runs',
        sa.Column('canon_source_revision_id', sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    """移除 Canon 来源版本列。"""
    op.drop_column('generation_runs', 'canon_source_revision_id')
