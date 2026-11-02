"""book recommendations

Revision ID: f4a9c2e7b1d6
Revises: e2f4a6b8c1d3
Create Date: 2026-10-21 18:40:00.000000

馆员精选推荐。热门借阅与新到图书是查询结果，不落表。
推荐行随书目删除级联消失，避免推荐墙出现点不进去的书。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a9c2e7b1d6'
down_revision: str | None = 'e2f4a6b8c1d3'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'recommendations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['library_id'], ['book_libraries.id'],
                                name='fk_recommendations_library_id_book_libraries'),
        sa.ForeignKeyConstraint(['record_id'], ['records.id'], ondelete='CASCADE',
                                name='fk_recommendations_record_id_records'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('library_id', 'record_id',
                            name='uq_recommendations_library_record'),
    )
    op.create_index(op.f('ix_recommendations_library_id'), 'recommendations',
                    ['library_id'], unique=False)
    op.create_index(op.f('ix_recommendations_record_id'), 'recommendations',
                    ['record_id'], unique=False)
    op.create_index('ix_recommendations_library_sort', 'recommendations',
                    ['library_id', 'sort_order'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_recommendations_library_sort', table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_record_id'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_library_id'), table_name='recommendations')
    op.drop_table('recommendations')
