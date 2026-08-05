"""add GenerationRun snapshot state and RunOutboxRecord publish lease columns

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-08-04 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd6e7f8a9b0c1'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 GenerationRun 添加运行快照状态列，为 RunOutboxRecord 添加发布租约列。

    GenerationRun 新增 pending_node/pause_reason/clarification_questions/
    last_error_code，使 GET /runs/{id} 返回真实可恢复状态而非固定空值。
    RunOutboxRecord 新增 publisher_owner/publisher_lease_expires_at，使卡在
    publishing 的崩溃记录能按租约超时被其他发布者重新领取（超时恢复）。
    """
    op.add_column(
        'generation_runs',
        sa.Column('pending_node', sa.String(length=120), nullable=True),
    )
    op.add_column(
        'generation_runs',
        sa.Column('pause_reason', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'generation_runs',
        sa.Column('clarification_questions', sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql'), nullable=True),
    )
    op.add_column(
        'generation_runs',
        sa.Column('last_error_code', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'run_outbox_records',
        sa.Column('publisher_owner', sa.String(length=120), nullable=True),
    )
    op.add_column(
        'run_outbox_records',
        sa.Column('publisher_lease_expires_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.alter_column(
        'run_event_consumer_cursors',
        'last_event_id',
        existing_type=sa.String(length=36),
        type_=sa.String(length=120),
        existing_nullable=True,
    )


def downgrade() -> None:
    """移除运行快照状态列与发布租约列。"""
    op.alter_column(
        'run_event_consumer_cursors',
        'last_event_id',
        existing_type=sa.String(length=120),
        type_=sa.String(length=36),
        existing_nullable=True,
    )
    op.drop_column('run_outbox_records', 'publisher_lease_expires_at')
    op.drop_column('run_outbox_records', 'publisher_owner')
    op.drop_column('generation_runs', 'last_error_code')
    op.drop_column('generation_runs', 'clarification_questions')
    op.drop_column('generation_runs', 'pause_reason')
    op.drop_column('generation_runs', 'pending_node')
