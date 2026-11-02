"""inventory sessions and scan logs

Revision ID: c7a1e5d9b2f4
Revises: d4f7a2c9e1b5
Create Date: 2026-10-21 18:00:00.000000

盘点：一次盘点 + 逐次扫描流水。**只记录，不自动改馆藏状态**，
未扫到只进清单，由馆员核对后再决定注销 / 恢复。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1e5d9b2f4'
down_revision: str | None = 'd4f7a2c9e1b5'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'inventory_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('expected_count', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('started_by', sa.String(length=64), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_by', sa.String(length=64), nullable=True),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(['library_id'], ['book_libraries.id'],
                                name='fk_inventory_sessions_library_id_book_libraries'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_inventory_sessions_library_id'),
                    'inventory_sessions', ['library_id'], unique=False)
    op.create_index(op.f('ix_inventory_sessions_status'),
                    'inventory_sessions', ['status'], unique=False)
    op.create_index('ix_inventory_sessions_library_status',
                    'inventory_sessions', ['library_id', 'status'], unique=False)

    op.create_table(
        'inventory_scans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('barcode', sa.String(length=32), nullable=False),
        sa.Column('holding_id', sa.Integer(), nullable=True),
        sa.Column('result', sa.String(length=16), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('call_no', sa.String(length=64), nullable=True),
        sa.Column('location', sa.String(length=64), nullable=True),
        sa.Column('reader_name', sa.String(length=64), nullable=True),
        sa.Column('scanned_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('scanned_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['inventory_sessions.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_inventory_scans_session_id'),
                    'inventory_scans', ['session_id'], unique=False)
    op.create_index(op.f('ix_inventory_scans_library_id'),
                    'inventory_scans', ['library_id'], unique=False)
    op.create_index('ix_inventory_scans_session_holding', 'inventory_scans',
                    ['session_id', 'holding_id'], unique=False)
    op.create_index('ix_inventory_scans_session_barcode', 'inventory_scans',
                    ['session_id', 'barcode'], unique=False)
    op.create_index('ix_inventory_scans_session_result', 'inventory_scans',
                    ['session_id', 'result'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_inventory_scans_session_result', table_name='inventory_scans')
    op.drop_index('ix_inventory_scans_session_barcode', table_name='inventory_scans')
    op.drop_index('ix_inventory_scans_session_holding', table_name='inventory_scans')
    op.drop_index(op.f('ix_inventory_scans_library_id'), table_name='inventory_scans')
    op.drop_index(op.f('ix_inventory_scans_session_id'), table_name='inventory_scans')
    op.drop_table('inventory_scans')
    op.drop_index('ix_inventory_sessions_library_status',
                  table_name='inventory_sessions')
    op.drop_index(op.f('ix_inventory_sessions_status'),
                  table_name='inventory_sessions')
    op.drop_index(op.f('ix_inventory_sessions_library_id'),
                  table_name='inventory_sessions')
    op.drop_table('inventory_sessions')
