"""inventory result snapshot

Revision ID: e2f4a6b8c1d3
Revises: d9b3f6a1c8e2
Create Date: 2026-10-21 16:30:00.000000

盘点结束时的清单快照。没有它，结束后再借书/注销会让历史盘点的
「未扫到 / 外借」跟着变化，与「结束即定格」的提示自相矛盾。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e2f4a6b8c1d3'
down_revision: str | None = 'd9b3f6a1c8e2'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column('inventory_sessions', sa.Column(
        'result_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('inventory_sessions', 'result_snapshot')
