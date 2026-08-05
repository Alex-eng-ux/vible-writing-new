"""add explicit accepted pointers

Revision ID: a2b3c4d5e6f7
Revises: 1c1dccd138fb
Create Date: 2026-08-04 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a2b3c4d5e6f7'
down_revision = '1c1dccd138fb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为章节和场景添加显式 accepted 修订指针，并向后兼容回填。"""
    op.add_column(
        'chapters',
        sa.Column('accepted_chapter_revision_id', sa.String(length=36), nullable=True),
    )
    op.add_column(
        'scenes',
        sa.Column('accepted_scene_revision_id', sa.String(length=36), nullable=True),
    )
    # 回填：对每个章节，把最新 accepted 章节修订 id 写入显式指针。
    op.execute(
        """
        UPDATE chapters c
        SET accepted_chapter_revision_id = sub.id
        FROM (
            SELECT DISTINCT ON (chapter_id) chapter_id, id
            FROM chapter_revisions
            WHERE status = 'accepted'
            ORDER BY chapter_id, created_at DESC
        ) sub
        WHERE c.id = sub.chapter_id
        """
    )
    # 回填：对每个场景，把最新 accepted 场景修订 id 写入显式指针。
    op.execute(
        """
        UPDATE scenes s
        SET accepted_scene_revision_id = sub.id
        FROM (
            SELECT DISTINCT ON (scene_id) scene_id, id
            FROM scene_revisions
            WHERE status = 'accepted'
            ORDER BY scene_id, created_at DESC
        ) sub
        WHERE s.id = sub.scene_id
        """
    )
    op.create_foreign_key(
        'fk_chapters_accepted_revision',
        'chapters',
        'chapter_revisions',
        ['accepted_chapter_revision_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_scenes_accepted_revision',
        'scenes',
        'scene_revisions',
        ['accepted_scene_revision_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """移除显式指针列。"""
    op.drop_constraint('fk_chapters_accepted_revision', 'chapters', type_='foreignkey')
    op.drop_constraint('fk_scenes_accepted_revision', 'scenes', type_='foreignkey')
    op.drop_column('scenes', 'accepted_scene_revision_id')
    op.drop_column('chapters', 'accepted_chapter_revision_id')
