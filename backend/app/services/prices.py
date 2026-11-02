# -*- coding: utf-8 -*-
"""定价冗余列的运维动作（存量回填）。

解析规则在 `core/pricing.py`（唯一真源），这里只负责"把存量记录算一遍写进去"。
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..core.pricing import price_columns

_SELECT = text("""
    SELECT id, fields::text AS fields
    FROM records
    WHERE id > :last
    ORDER BY id
    LIMIT :lim
""")

_UPDATE = text("UPDATE records SET price = :p, price_currency = :c WHERE id = :id")


def backfill_prices(conn, batch_size: int = 2000) -> int:
    """把**存量**记录的 `price` / `price_currency` 按 `fields` 算出来写入，返回处理行数。

    谁调用：迁移 `*_record_price_columns.py`。**必须随迁移跑**，不做成"另跑一个脚本"——
    列加好却不回填时，历史记录的码洋会静默变成 0（新写的记录却是对的，最难查），
    而这是账目数字，不能靠"记得跑脚本"。

    分批按 id 前进（默认 2000 条/批）：十万级也不把整表读进内存，也不会长事务锁表。
    直接 `fields::text` 再 `json.loads`：这里要写的就是"算出来的值"本身，
    绕过 ORM 与事件钩子是**故意**的（否则设置 fields 反而会触发重算，绕远路），
    也避开 JSONB 适配器在不同驱动下的差异。单条 JSONB 解析失败按"无定价"处理，不中断回填。

    幂等：可重复跑，结果一致（解析是纯函数）。
    """
    total = 0
    last_id = 0
    while True:
        rows = conn.execute(_SELECT, {"last": last_id, "lim": batch_size}).fetchall()
        if not rows:
            return total
        params = []
        for rid, raw in rows:
            last_id = rid
            try:
                fields = json.loads(raw) if raw else []
            except (TypeError, ValueError):
                fields = []
            cols = price_columns(fields if isinstance(fields, list) else [])
            params.append({"id": rid, "p": cols["price"], "c": cols["price_currency"]})
        conn.execute(_UPDATE, params)
        total += len(params)
