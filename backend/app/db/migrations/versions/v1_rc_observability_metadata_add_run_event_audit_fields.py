"""add RunEvent payload_schema and redaction_version

Revision ID: v1_rc_observability_metadata
Revises: f8a9b0c1d2e3
Create Date: 2026-08-04 21:00:00.000000

Task 9 V1-RC 观测元数据迁移：为 ``run_events`` 增加两个非空审计字段，
默认值分别为 ``run-event.v1`` 与 ``redaction.v1``（与 Task 5B SSE envelope
``RunEventEnvelope`` 的事件注册表默认值一致）。迁移完成后由持久化字段作为
审计来源；不改变既有事件的 ``event_id``、``sequence`` 或 payload 语义。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'v1_rc_observability_metadata'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 run_events 增加 payload_schema 与 redaction_version 审计列。

    非空 + 服务端默认值：既有行自动回填，新增行由模型/默认值写入；列内容
    仅作审计来源，不影响事件序列与 payload 语义。
    """
    op.add_column(
        'run_events',
        sa.Column('payload_schema', sa.String(length=64), nullable=False, server_default='run-event.v1'),
    )
    op.add_column(
        'run_events',
        sa.Column('redaction_version', sa.String(length=64), nullable=False, server_default='redaction.v1'),
    )


def downgrade() -> None:
    """移除两个审计列（先删后加的顺序无关，仅需保证可回滚）。"""
    op.drop_column('run_events', 'redaction_version')
    op.drop_column('run_events', 'payload_schema')
