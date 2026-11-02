"""backfill missing shelf numbers from the legacy location text

Revision ID: f5c8a2d1e7b4
Revises: e3b7c1d5f9a2
Create Date: 2026-10-23

旧数据的馆藏地点常写成“楼层/区域 + 架位号”（如 `2F 科技区 A-01`），而种级
`shelf_no` 为空。这里把这种**唯一且一致**的尾缀提升为排架号，并从地点中移除；
同一条记录下出现不同尾缀、或地点本来就没有架位号时，整条记录不处理，避免猜测。
"""
from __future__ import annotations

import re
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "f5c8a2d1e7b4"
down_revision = "e3b7c1d5f9a2"
branch_labels = None
depends_on = None

_SHELF_RE = re.compile(
    r"(?<![A-Za-z0-9])\s*(?:[·,，/／]\s*)?([A-Za-z]{1,4}-?\d{1,4})\s*$",
    re.IGNORECASE,
)
_SEPARATORS = " ·,，/／"


def _split_location(location: str) -> tuple[str, str] | None:
    """返回（纯区域, 架位号）；没有唯一尾缀或去掉后为空时返回 None。"""
    loc = location.strip()
    match = _SHELF_RE.search(loc)
    if not match:
        return None
    area = _SHELF_RE.sub("", loc).strip().rstrip(_SEPARATORS).strip()
    if not area or area == loc:
        return None
    return area, match.group(1)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT h.id, h.record_id, h.location
        FROM holdings h
        JOIN records r ON r.id = h.record_id
        WHERE (r.shelf_no IS NULL OR btrim(r.shelf_no) = '')
          AND h.location IS NOT NULL
          AND btrim(h.location) <> ''
        ORDER BY h.record_id, h.id
    """)).fetchall()

    by_record: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for holding_id, record_id, location in rows:
        by_record[record_id].append((holding_id, location))

    updates: list[tuple[int, str]] = []
    backfills: list[tuple[int, str]] = []
    for record_id, holdings in by_record.items():
        splits = [_split_location(location) for _, location in holdings]
        if any(part is None for part in splits):
            continue
        codes = {part[1] for part in splits if part}
        if len(codes) != 1:
            continue
        shelf = codes.pop()
        for (holding_id, _), part in zip(holdings, splits):
            assert part is not None
            updates.append((holding_id, part[0]))
        backfills.append((record_id, shelf))

    for holding_id, location in updates:
        bind.execute(
            sa.text("UPDATE holdings SET location = :location WHERE id = :id"),
            {"location": location, "id": holding_id},
        )
    for record_id, shelf in backfills:
        bind.execute(
            sa.text("UPDATE records SET shelf_no = :shelf WHERE id = :id"),
            {"shelf": shelf, "id": record_id},
        )
    print(f"[location-clean] 从旧地点回填 {len(backfills)} 条排架号，清理 {len(updates)} 册馆藏地点")


def downgrade() -> None:
    # 回填会把区域与架位号拆成两个标准字段；合并不如清洗可靠，保留不回退。
    pass
