"""add GenerationRun.normalized_input immutable run input envelope

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-08-04 19:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e7f8a9b0c1d2'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 GenerationRun 添加不可变规范化运行输入信封列。

    normalized_input 持久化首次请求的规范化输入（chapter_intent、
    author_feedback、plan_revision_id、handoff 字段、场景/章节基线、
    scene_base_revision_ids 等）。Worker 仅凭 run_id 即可重建与首次请求完全
    一致的输入；重试绝不重新读取客户端输入。
    """
    op.add_column(
        'generation_runs',
        sa.Column(
            'normalized_input',
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """移除规范化运行输入信封列。"""
    op.drop_column('generation_runs', 'normalized_input')
