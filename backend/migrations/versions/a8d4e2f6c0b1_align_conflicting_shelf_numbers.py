"""align a conflicting record-level shelf number with the physical location code

Revision ID: a8d4e2f6c0b1
Revises: f5c8a2d1e7b4
Create Date: 2026-10-23

旧数据里极少数记录同时有 `shelf_no=G-1`、馆藏地点却全部写着 `... B-08`。
当同一条记录的所有馆藏地点尾缀**一致且不同于**记录排架号时，以实际地点为准
回填 shelf_no 并从地点中移除；只要有一个地点没有架位号或出现多个不同尾缀，
整条记录保持原样，交由人工核对。
"""
from __future__ import annotations

import re
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "a8d4e2f6c0b1"
down_revision = "f5c8a2d1e7b4"
branch_labels = None
depends_on = None

_SHELF_RE = re.compile(
    r"(?<![A-Za-z0-9])\s*(?:[·,，/／]\s*)?([A-Za-z]{1,4}-?\d{1,4})\s*$",
    re.IGNORECASE,
)
_SEPARATORS = " ·,，/／"


def _split_location(location: str) -> tuple[str, str] | None:
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
        SELECT h.id, h.record_id, h.location, r.shelf_no
        FROM holdings h
        JOIN records r ON r.id = h.record_id
        WHERE r.shelf_no IS NOT NULL
          AND btrim(r.shelf_no) <> ''
          AND h.location IS NOT NULL
          AND btrim(h.location) <> ''
        ORDER BY h.record_id, h.id
    """)).fetchall()

    by_record: dict[int, list[tuple[int, str]]] = defaultdict(list)
    current: dict[int, str] = {}
    for holding_id, record_id, location, shelf_no in rows:
        by_record[record_id].append((holding_id, location))
        current[record_id] = shelf_no

    updates: list[tuple[int, str]] = []
    aligns: list[tuple[int, str]] = []
    for record_id, holdings in by_record.items():
        splits = [_split_location(location) for _, location in holdings]
        if any(part is None for part in splits):
            continue
        codes = {part[1] for part in splits if part}
        if len(codes) != 1:
            continue
        code = codes.pop()
        if code.lower() == (current[record_id] or "").strip().lower():
            continue
        for (holding_id, _), part in zip(holdings, splits):
            assert part is not None
            updates.append((holding_id, part[0]))
        aligns.append((record_id, code))

    for holding_id, location in updates:
        bind.execute(
            sa.text("UPDATE holdings SET location = :location WHERE id = :id"),
            {"location": location, "id": holding_id},
        )
    for record_id, code in aligns:
        bind.execute(
            sa.text("UPDATE records SET shelf_no = :shelf WHERE id = :id"),
            {"shelf": code, "id": record_id},
        )
    print(f"[location-clean] 对齐 {len(aligns)} 条冲突排架号，清理 {len(updates)} 册馆藏地点")


def downgrade() -> None:
    # 以实际馆藏地点为准的清洗不可逆（原 shelf_no 与地点不一致，无法安全还原）。
    pass
