"""性能索引：loans 时段列 + holdings (library_id, status)

LoanORM 此前只有 status / due_on / (reader,status) / (library,status) 等索引，
但 dashboard「近 14 天借还曲线」、「今日借还」和流通统计的时段过滤全落在
`loaned_at` / `returned_at` 上（api/dashboard.py、api/circulation.py）——这两列
没有索引，台账变厚之后就是顺表扫描。holdings 侧盘点「应盘」、财产登记、统计
大量按 (图书库, 状态) 联合过滤，单列 library_id 索引命中后仍要逐行回表看 status。

这三个索引与模型 __table_args__（2026-10-24 补的 Index 声明）同名同列，
create_all 与迁移的产物继续保持逐字节一致。
"""
from __future__ import annotations

from alembic import op

revision = "d6b8f0a2c4e6"
down_revision = "b2e6f4a9c7d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_loans_loaned_at", "loans", ["loaned_at"])
    op.create_index("ix_loans_returned_at", "loans", ["returned_at"])
    op.create_index("ix_holdings_library_status", "holdings",
                    ["library_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_holdings_library_status", table_name="holdings")
    op.drop_index("ix_loans_returned_at", table_name="loans")
    op.drop_index("ix_loans_loaned_at", table_name="loans")
