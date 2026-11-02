"""Phase 6 收尾：删除 records.status

该列自 v1.0 起只写不读（所有记录统一 `final`），契约 §5 已声明它不再表示业务状态。
继续保留只会让新代码误以为书目有 draft/final 工作流。读者状态、馆藏处置状态各有
自己的列，不受影响。
"""
from alembic import op

revision = "f5a7c9e1d3b4"
down_revision = "e4a6c8d0b2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE records DROP COLUMN IF EXISTS status")


def downgrade() -> None:
    op.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'final'")
