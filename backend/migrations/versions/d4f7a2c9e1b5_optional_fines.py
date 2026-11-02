"""optional overdue fines

Revision ID: d4f7a2c9e1b5
Revises: a7c41e9d2b63
Create Date: 2026-10-21 16:00:00.000000

逾期罚款——**可选功能，默认关闭**。

金额在还书那一刻按当时规则算好写进 loans.fine_amount（快照），之后改费率不追溯；
fine_status 只在 amount > 0 时有意义。关闭开关不会删除历史金额，只是界面与计算都不再出现。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f7a2c9e1b5'
down_revision: str | None = 'a7c41e9d2b63'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column('loans', sa.Column(
        'fine_amount', sa.Numeric(10, 2), server_default='0', nullable=False))
    op.add_column('loans', sa.Column('fine_status', sa.String(length=16),
                                     nullable=True))
    op.add_column('loans', sa.Column('fine_settled_at',
                                     sa.DateTime(timezone=True), nullable=True))
    op.add_column('loans', sa.Column('fine_operator', sa.String(length=64),
                                     nullable=True))
    op.create_index('ix_loans_library_fine_status', 'loans',
                    ['library_id', 'fine_status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_loans_library_fine_status', table_name='loans')
    op.drop_column('loans', 'fine_operator')
    op.drop_column('loans', 'fine_settled_at')
    op.drop_column('loans', 'fine_status')
    op.drop_column('loans', 'fine_amount')
