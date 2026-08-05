"""add official TimelineEvent and PlotThread tables

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-04 15:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建正式时间线事件与剧情线表（对应计划书 3.1 节）。"""
    op.create_table(
        'timeline_events',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('novel_projects.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('chapter_id', sa.String(length=36), nullable=True),
        sa.Column('event_text', sa.Text(), nullable=False),
        sa.Column('story_time', JSONB(), nullable=False),
        sa.Column('entities', JSONB(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='active'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index('ix_timeline_events_project_id', 'timeline_events', ['project_id'])
    op.create_table(
        'plot_threads',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('novel_projects.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('chapter_id', sa.String(length=36), nullable=True),
        sa.Column('thread_text', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False, server_default='open'),
        sa.Column('planned_resolution', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='active'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index('ix_plot_threads_project_id', 'plot_threads', ['project_id'])


def downgrade() -> None:
    """移除正式时间线事件与剧情线表。"""
    op.drop_index('ix_plot_threads_project_id', table_name='plot_threads')
    op.drop_table('plot_threads')
    op.drop_index('ix_timeline_events_project_id', table_name='timeline_events')
    op.drop_table('timeline_events')
