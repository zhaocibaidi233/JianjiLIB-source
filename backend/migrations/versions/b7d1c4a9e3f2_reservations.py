"""reservations

Revision ID: b7d1c4a9e3f2
Revises: f4a9c2e7b1d6
Create Date: 2026-10-22 17:05:00.000000

图书预约。部分唯一索引保证同一读者对同一书目最多一条进行中
预约；`READY` 不绑定具体条码，归还时按队列顺位自动提升。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d1c4a9e3f2'
down_revision: str | None = 'f4a9c2e7b1d6'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'reservations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('reader_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('reserved_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fulfilled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_reason', sa.String(length=128), nullable=True),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['library_id'], ['book_libraries.id'],
                                name='fk_reservations_library_id_book_libraries'),
        sa.ForeignKeyConstraint(['record_id'], ['records.id'], ondelete='CASCADE',
                                name='fk_reservations_record_id_records'),
        sa.ForeignKeyConstraint(['reader_id'], ['readers.id'],
                                name='fk_reservations_reader_id_readers'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_reservations_library_id'), 'reservations',
                    ['library_id'], unique=False)
    op.create_index(op.f('ix_reservations_record_id'), 'reservations',
                    ['record_id'], unique=False)
    op.create_index(op.f('ix_reservations_reader_id'), 'reservations',
                    ['reader_id'], unique=False)
    op.create_index(op.f('ix_reservations_status'), 'reservations',
                    ['status'], unique=False)
    op.create_index('ix_reservations_library_status', 'reservations',
                    ['library_id', 'status', 'reserved_at'], unique=False)
    op.create_index('ix_reservations_reader_status', 'reservations',
                    ['reader_id', 'status'], unique=False)
    op.create_index('ix_reservations_record_status', 'reservations',
                    ['record_id', 'status'], unique=False)
    op.create_index('uq_reservations_active', 'reservations',
                    ['library_id', 'record_id', 'reader_id'], unique=True,
                    postgresql_where=sa.text("status IN ('PENDING', 'READY')"))


def downgrade() -> None:
    op.drop_index('uq_reservations_active', table_name='reservations',
                  postgresql_where=sa.text("status IN ('PENDING', 'READY')"))
    op.drop_index('ix_reservations_record_status', table_name='reservations')
    op.drop_index('ix_reservations_reader_status', table_name='reservations')
    op.drop_index('ix_reservations_library_status', table_name='reservations')
    op.drop_index(op.f('ix_reservations_status'), table_name='reservations')
    op.drop_index(op.f('ix_reservations_reader_id'), table_name='reservations')
    op.drop_index(op.f('ix_reservations_record_id'), table_name='reservations')
    op.drop_index(op.f('ix_reservations_library_id'), table_name='reservations')
    op.drop_table('reservations')
