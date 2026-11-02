"""Phase 2：Item 的登录/入藏/停流通字段 + Record.volume_key

字段定义见模型与 API 契约。

本次只加列与索引，并做**可重跑的保守回填**：
- holdings.shelf_no ← records.shelf_no（仅空值）
- holdings.volume ← records.volume（仅空值；已有册级卷号不覆盖）
- holdings.accession_no ← barcode（仅空值）
- holdings.acquired_at ← created_at（仅空值）
- holdings.price ← records.price（仅空值；书目级价格只是默认值）
- holdings.source ← records.source（仅空值）
- holdings.accession_batch_no ← 'OPENING'（仅空值）
- records.volume_key ← 200$h/$i 规范化；没有 200$h/$i 时回退 records.volume

不删旧列、不改 905；Phase 3 再切读路径。
"""
from __future__ import annotations

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "a7c9e1f3b5d2"
down_revision = "e2b6c4d8f1a3"
branch_labels = None
depends_on = None


def _normalize_volume_key(number: str | None, title: str | None) -> str | None:
    """与 core.volume.normalize_volume_key 同口径；迁移内复制一份避免依赖未来代码。"""
    def _n(value: str | None) -> str:
        s = unicodedata.normalize("NFKC", str(value or ""))
        return re.sub(r"\s+", "", s).strip().lower()

    n, t = _n(number), _n(title)
    if not n and not t:
        return None
    if n and t:
        return f"{n}|{t}"
    return n or t


def _record_volume_key(fields: list | None, legacy_volume: str | None) -> str | None:
    for field in (fields or []):
        if not isinstance(field, dict) or str(field.get("tag")) != "200":
            continue
        sf = dict(field.get("sf") or [])
        number = sf.get("h")
        title = sf.get("i")
        if number or title:
            return _normalize_volume_key(number, title)
    return _normalize_volume_key(legacy_volume, None)


def upgrade() -> None:
    # 1) 列（IF NOT EXISTS：兼容 create_all 先建表后再补迁移）
    for ddl in (
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS accession_no VARCHAR(32)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS acquired_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS price NUMERIC(12,2)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS source VARCHAR(64)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS accession_batch_no VARCHAR(32)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS shelf_no VARCHAR(64)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS suspended_by VARCHAR(64)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS suspended_reason VARCHAR(256)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS review_batch_no VARCHAR(32)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS approval_ref VARCHAR(128)",
        "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS destination VARCHAR(128)",
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS volume_key VARCHAR(64)",
    ):
        op.execute(ddl)

    # 2) 索引
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_holdings_library_accession "
        "ON holdings (library_id, accession_no)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_records_library_isbn_volume "
        "ON records (library_id, isbn_norm, volume_key)")

    bind = op.get_bind()

    # 3) 册级字段回填（只填空值）
    bind.execute(sa.text("""
        UPDATE holdings h
        SET shelf_no = r.shelf_no
        FROM records r
        WHERE h.record_id = r.id
          AND (h.shelf_no IS NULL OR btrim(h.shelf_no) = '')
          AND r.shelf_no IS NOT NULL AND btrim(r.shelf_no) <> ''
    """))
    bind.execute(sa.text("""
        UPDATE holdings h
        SET volume = r.volume
        FROM records r
        WHERE h.record_id = r.id
          AND (h.volume IS NULL OR btrim(h.volume) = '')
          AND r.volume IS NOT NULL AND btrim(r.volume) <> ''
    """))
    bind.execute(sa.text("""
        UPDATE holdings SET accession_no = barcode
        WHERE (accession_no IS NULL OR btrim(accession_no) = '')
          AND barcode IS NOT NULL AND btrim(barcode) <> ''
    """))
    bind.execute(sa.text("""
        UPDATE holdings SET acquired_at = created_at
        WHERE acquired_at IS NULL
    """))
    bind.execute(sa.text("""
        UPDATE holdings h
        SET price = r.price
        FROM records r
        WHERE h.record_id = r.id
          AND h.price IS NULL
          AND r.price IS NOT NULL
    """))
    bind.execute(sa.text("""
        UPDATE holdings h
        SET source = r.source
        FROM records r
        WHERE h.record_id = r.id
          AND (h.source IS NULL OR btrim(h.source) = '')
          AND r.source IS NOT NULL AND btrim(r.source) <> ''
    """))
    bind.execute(sa.text("""
        UPDATE holdings SET accession_batch_no = 'OPENING'
        WHERE accession_batch_no IS NULL OR btrim(accession_batch_no) = ''
    """))

    # 4) volume_key（200$h/$i 优先，旧 records.volume 兜底）
    rows = bind.execute(sa.text(
        "SELECT id, fields, volume FROM records")).fetchall()
    updated = 0
    skipped = 0
    for record_id, fields, legacy_volume in rows:
        key = _record_volume_key(fields, legacy_volume)
        if key is None:
            continue
        if len(key) > 64:
            skipped += 1
            continue
        bind.execute(
            sa.text("UPDATE records SET volume_key = :key WHERE id = :id"),
            {"key": key, "id": record_id})
        updated += 1
    print(f"[phase2] volume_key 回填 {updated} 条，跳过超长 {skipped} 条")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_records_library_isbn_volume")
    op.execute("DROP INDEX IF EXISTS ix_holdings_library_accession")
    for column in (
        "volume_key",
    ):
        op.execute(f"ALTER TABLE records DROP COLUMN IF EXISTS {column}")
    for column in (
        "destination", "approval_ref", "review_batch_no", "suspended_reason",
        "suspended_by", "suspended_at", "shelf_no", "accession_batch_no",
        "source", "price", "acquired_at", "accession_no",
    ):
        op.execute(f"ALTER TABLE holdings DROP COLUMN IF EXISTS {column}")
