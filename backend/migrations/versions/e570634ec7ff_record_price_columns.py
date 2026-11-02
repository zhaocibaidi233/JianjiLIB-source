"""record price columns

定价冗余列：`records.price` / `records.price_currency` = `010$d` 的解析结果
（金额与币种成对存，解析见 core/pricing.py）。统计与财产登记表因此不再把全库
fields JSONB 拉进内存逐条正则，改成一句 SQL 聚合。

**加列与回填在同一次迁移里做完**：列加好却不回填时，历史记录的码洋会静默变成 0
（而新写的记录是对的，最难查）—— 这是账目数字，不能靠"记得另跑一个脚本"。

Revision ID: e570634ec7ff
Revises: 295fa9821cb7
Create Date: 2026-10-08 15:41:48.253804

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e570634ec7ff'
down_revision: str | None = '295fa9821cb7'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column('records', sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('records', sa.Column('price_currency', sa.String(length=3), nullable=True))
    # 存量回填：与写入路径共用同一个解析器（core/pricing.py），分批 2000 条。
    # 之后的一致性由 models.RecordORM 的 before_insert / before_update 钩子保证。
    from app.services.prices import backfill_prices

    backfill_prices(op.get_bind())


def downgrade() -> None:
    op.drop_column('records', 'price_currency')
    op.drop_column('records', 'price')
