"""add GenerationRun request_type, decision_target and updated_at columns

Revision ID: c5d6e7f8a9b0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-04 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c5d6e7f8a9b0'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 GenerationRun 添加请求类型、决策目标与更新时间列（Task 5B 运行 API 用）。"""
    op.add_column(
        'generation_runs',
        sa.Column('request_type', sa.String(length=32), nullable=True),
    )
    op.add_column(
        'generation_runs',
        sa.Column('decision_target', sa.String(length=32), nullable=True),
    )
    op.add_column(
        'generation_runs',
        sa.Column(
            'updated_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text('now()'),
        ),
    )


def downgrade() -> None:
    """移除 GenerationRun 的请求类型、决策目标与更新时间列。"""
    op.drop_column('generation_runs', 'updated_at')
    op.drop_column('generation_runs', 'decision_target')
    op.drop_column('generation_runs', 'request_type')
