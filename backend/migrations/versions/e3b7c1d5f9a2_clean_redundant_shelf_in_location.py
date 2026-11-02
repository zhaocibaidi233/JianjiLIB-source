"""clean redundant shelf numbers embedded in holding locations

Revision ID: e3b7c1d5f9a2
Revises: c9e2f4a7b6d3
Create Date: 2026-10-23

旧数据把种级排架号写进了馆藏地点（如 `3F 文学区 A-12`，同时 records.shelf_no
也是 `A-12`）。展示层现在统一组合“地点 + 排架号”，因此这里只清理**与记录排架号
完全相同**的尾缀；没有排架号、或地点里的架位号与排架号冲突的数据保持原样，
避免猜测或丢失信息。
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "e3b7c1d5f9a2"
down_revision = "c9e2f4a7b6d3"
branch_labels = None
depends_on = None

_SEPARATORS = " ·,，/／"


def _strip_shelf(location: str, shelf_no: str) -> str | None:
    loc = location.strip()
    shelf = shelf_no.strip()
    if not loc or not shelf:
        return None
    pattern = re.compile(
        rf"(?<![A-Za-z0-9])\s*(?:[·,，/／]\s*)?{re.escape(shelf)}\s*$",
        re.IGNORECASE,
    )
    cleaned = pattern.sub("", loc).strip().rstrip(_SEPARATORS).strip()
    if not cleaned or cleaned == loc:
        return None
    return cleaned


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT h.id, h.location, r.shelf_no
        FROM holdings h
        JOIN records r ON r.id = h.record_id
        WHERE r.shelf_no IS NOT NULL
          AND btrim(r.shelf_no) <> ''
          AND h.location IS NOT NULL
          AND btrim(h.location) <> ''
    """)).fetchall()

    updates = [(hid, cleaned) for hid, location, shelf in rows
               if (cleaned := _strip_shelf(location, shelf))]
    for holding_id, location in updates:
        bind.execute(
            sa.text("UPDATE holdings SET location = :location WHERE id = :id"),
            {"location": location, "id": holding_id},
        )
    print(f"[location-clean] 清理 {len(updates)} 册馆藏的重复排架号尾缀")


def downgrade() -> None:
    # 数据清洗不可逆：原值只包含与 shelf_no 重复的信息，展示层仍会补回。
    pass
