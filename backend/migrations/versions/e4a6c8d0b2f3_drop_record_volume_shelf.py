"""Phase 6：删除 records.volume / records.shelf_no

卷册号与排架号已归 Item（holdings.volume / holdings.shelf_no）。本迁移先把仍为空的
册级字段从 record 旧列再兜底一次，然后删除旧列；API 的 volume/shelf_no 入参/返回值
改为册级默认/投影，不再落 record。
"""
from alembic import op

revision = "e4a6c8d0b2f3"
down_revision = "d2f4a6b8c0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE holdings h
        SET shelf_no = r.shelf_no
        FROM records r
        WHERE h.record_id = r.id
          AND (h.shelf_no IS NULL OR btrim(h.shelf_no) = '')
          AND r.shelf_no IS NOT NULL AND btrim(r.shelf_no) <> ''
    """)
    op.execute("""
        UPDATE holdings h
        SET volume = r.volume
        FROM records r
        WHERE h.record_id = r.id
          AND (h.volume IS NULL OR btrim(h.volume) = '')
          AND r.volume IS NOT NULL AND btrim(r.volume) <> ''
    """)
    op.execute("ALTER TABLE records DROP COLUMN IF EXISTS volume")
    op.execute("ALTER TABLE records DROP COLUMN IF EXISTS shelf_no")


def downgrade() -> None:
    op.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS volume VARCHAR(32)")
    op.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS shelf_no VARCHAR(64)")
