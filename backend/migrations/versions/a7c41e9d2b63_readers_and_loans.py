"""readers and loans for circulation

Revision ID: a7c41e9d2b63
Revises: 55afa84888ca
Create Date: 2026-10-21 10:00:00.000000

读者档案与借阅台账。借出状态只由 loans.status='ACTIVE' 表达，
**不写 holdings.status**；一册同时最多一条 ACTIVE 由部分唯一索引保证。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c41e9d2b63'
down_revision: str | None = '55afa84888ca'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'readers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('card_no', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('reader_type', sa.String(length=16), nullable=False),
        sa.Column('grade', sa.String(length=32), nullable=True),
        sa.Column('class_name', sa.String(length=64), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['library_id'], ['book_libraries.id'],
                                name='fk_readers_library_id_book_libraries'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('library_id', 'card_no',
                            name='uq_readers_library_card_no'),
    )
    op.create_index(op.f('ix_readers_library_id'), 'readers', ['library_id'],
                    unique=False)
    op.create_index(op.f('ix_readers_card_no'), 'readers', ['card_no'],
                    unique=False)
    op.create_index(op.f('ix_readers_name'), 'readers', ['name'], unique=False)
    op.create_index(op.f('ix_readers_reader_type'), 'readers', ['reader_type'],
                    unique=False)
    op.create_index(op.f('ix_readers_class_name'), 'readers', ['class_name'],
                    unique=False)
    op.create_index(op.f('ix_readers_status'), 'readers', ['status'], unique=False)
    op.create_index('ix_readers_library_status', 'readers',
                    ['library_id', 'status'], unique=False)

    op.create_table(
        'loans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('holding_id', sa.Integer(), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('barcode', sa.String(length=32), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('reader_id', sa.Integer(), nullable=False),
        sa.Column('reader_card_no', sa.String(length=32), nullable=True),
        sa.Column('reader_name', sa.String(length=64), nullable=True),
        sa.Column('reader_class', sa.String(length=64), nullable=True),
        sa.Column('loaned_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('due_on', sa.Date(), nullable=False),
        sa.Column('returned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('renew_count', sa.Integer(), nullable=False),
        sa.Column('last_renewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('operator_loan', sa.String(length=64), nullable=True),
        sa.Column('operator_return', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_loans_library_id'), 'loans', ['library_id'],
                    unique=False)
    op.create_index(op.f('ix_loans_holding_id'), 'loans', ['holding_id'],
                    unique=False)
    op.create_index(op.f('ix_loans_reader_id'), 'loans', ['reader_id'],
                    unique=False)
    op.create_index(op.f('ix_loans_status'), 'loans', ['status'], unique=False)
    op.create_index('ix_loans_reader_status', 'loans',
                    ['reader_id', 'status'], unique=False)
    op.create_index('ix_loans_library_status', 'loans',
                    ['library_id', 'status'], unique=False)
    op.create_index('ix_loans_due_on', 'loans', ['due_on'], unique=False)
    # 一册同时最多一条 ACTIVE：应用层查重之外的最后一道防线（并发双击）
    op.create_index('uq_loans_active_holding', 'loans', ['holding_id'],
                    unique=True,
                    postgresql_where=sa.text("status = 'ACTIVE'"))


def downgrade() -> None:
    op.drop_index('uq_loans_active_holding', table_name='loans',
                  postgresql_where=sa.text("status = 'ACTIVE'"))
    op.drop_index('ix_loans_due_on', table_name='loans')
    op.drop_index('ix_loans_library_status', table_name='loans')
    op.drop_index('ix_loans_reader_status', table_name='loans')
    op.drop_index(op.f('ix_loans_status'), table_name='loans')
    op.drop_index(op.f('ix_loans_reader_id'), table_name='loans')
    op.drop_index(op.f('ix_loans_holding_id'), table_name='loans')
    op.drop_index(op.f('ix_loans_library_id'), table_name='loans')
    op.drop_table('loans')

    op.drop_index('ix_readers_library_status', table_name='readers')
    op.drop_index(op.f('ix_readers_status'), table_name='readers')
    op.drop_index(op.f('ix_readers_class_name'), table_name='readers')
    op.drop_index(op.f('ix_readers_reader_type'), table_name='readers')
    op.drop_index(op.f('ix_readers_name'), table_name='readers')
    op.drop_index(op.f('ix_readers_card_no'), table_name='readers')
    op.drop_index(op.f('ix_readers_library_id'), table_name='readers')
    op.drop_table('readers')
