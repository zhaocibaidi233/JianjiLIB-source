"""lost loans and compensation

Revision ID: d9b3f6a1c8e2
Revises: c7a1e5d9b2f4
Create Date: 2026-10-21 18:10:00.000000

丢书处理：loans.status 增加 LOST；赔偿金额与状态独立于逾期罚款，
关闭罚款时赔偿仍可用。赔偿只在金额 > 0 时有状态，收款 / 免除 / 撤销与罚款同构。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9b3f6a1c8e2'
down_revision: str | None = 'c7a1e5d9b2f4'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column('loans', sa.Column('lost_at',
                                     sa.DateTime(timezone=True), nullable=True))
    op.add_column('loans', sa.Column('lost_operator',
                                     sa.String(length=64), nullable=True))
    op.add_column('loans', sa.Column('lost_note',
                                     sa.String(length=256), nullable=True))
    op.add_column('loans', sa.Column(
        'compensation_amount', sa.Numeric(10, 2),
        server_default='0', nullable=False))
    op.add_column('loans', sa.Column('compensation_status',
                                     sa.String(length=16), nullable=True))
    op.add_column('loans', sa.Column('compensation_settled_at',
                                     sa.DateTime(timezone=True), nullable=True))
    op.add_column('loans', sa.Column('compensation_operator',
                                     sa.String(length=64), nullable=True))
    op.create_index('ix_loans_library_compensation_status', 'loans',
                    ['library_id', 'compensation_status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_loans_library_compensation_status', table_name='loans')
    op.drop_column('loans', 'compensation_operator')
    op.drop_column('loans', 'compensation_settled_at')
    op.drop_column('loans', 'compensation_status')
    op.drop_column('loans', 'compensation_amount')
    op.drop_column('loans', 'lost_note')
    op.drop_column('loans', 'lost_operator')
    op.drop_column('loans', 'lost_at')
