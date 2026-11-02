"""Phase 6：清理已退休字段

- `records.extra_905` → `marc_extras`（外部 905 扩展码的持久列）
- 删除休眠列 `holdings.circulation_type`（全仓从未写入/读取）
- `ON_LOAN` 已由 b8d0f2e4c6a1 迁为 AVAILABLE；本次只清模型枚举/分支，无数据变更。
"""
from alembic import op

revision = "d2f4a6b8c0e1"
down_revision = "c1e3f5a7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='records' AND column_name='extra_905')
               AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='records' AND column_name='marc_extras')
            THEN
                ALTER TABLE records RENAME COLUMN extra_905 TO marc_extras;
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE holdings DROP COLUMN IF EXISTS circulation_type")


def downgrade() -> None:
    op.execute("ALTER TABLE holdings ADD COLUMN IF NOT EXISTS circulation_type VARCHAR(32)")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='records' AND column_name='marc_extras')
               AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='records' AND column_name='extra_905')
            THEN
                ALTER TABLE records RENAME COLUMN marc_extras TO extra_905;
            END IF;
        END $$;
    """)
