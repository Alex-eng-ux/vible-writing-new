"""add Chapter.entry_handoff_status

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-08-04 13:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为章节添加显式入口 handoff 状态列。"""
    op.add_column(
        'chapters',
        sa.Column('entry_handoff_status', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """移除章节入口 handoff 状态列。"""
    op.drop_column('chapters', 'entry_handoff_status')
