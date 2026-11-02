"""Phase 3：退休 ON_LOAN 馆藏状态

契约 §15.3：在借只由 `loans.status = ACTIVE` 派生，不写 `holdings.status`。
历史 ON_LOAN 统一改为 AVAILABLE；已有 ACTIVE Loan 原样保留，借阅事实不丢。

降级不做：无法从 AVAILABLE 反推出哪些册历史上是 ON_LOAN，且新写入已禁止 ON_LOAN。
"""
import sqlalchemy as sa
from alembic import op

revision = "b8d0f2e4c6a1"
down_revision = "a7c9e1f3b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT count(*) FROM holdings WHERE status = 'ON_LOAN'")).scalar() or 0
    op.execute("UPDATE holdings SET status = 'AVAILABLE' WHERE status = 'ON_LOAN'")
    print(f"[phase3] ON_LOAN -> AVAILABLE：{count} 册（ACTIVE Loan 保留）")


def downgrade() -> None:
    pass
