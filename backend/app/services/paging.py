# -*- coding: utf-8 -*-
"""列表分页的唯一实现（count + offset/limit）。

过去 15+ 处各自写 `total = query.count()` + `offset((page-1)*page_size)`，
口径分散：想改计数方式（例如换成窗口函数一次查询数完）就得改一串，漏一处
就漂移。分页制**不同**的端点不要强套本函数 —— `transfer`（offset/limit 制）、
`labels`（add_columns 元组行）形状都不一样，硬套只会扭曲。
"""
from __future__ import annotations

from sqlalchemy.orm import Query


def paginate(query: Query, page: int, page_size: int) -> tuple[int, list]:
    """`count` + 切片（页码从 1 起），返回 `(total, rows)`。

    排序由调用方先行施加（`apply_sort` 或显式 `order_by`），本函数只管翻页。
    """
    return (query.order_by(None).count(),
            query.offset((page - 1) * page_size).limit(page_size).all())
