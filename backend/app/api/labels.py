# -*- coding: utf-8 -*-
"""书标打印的候选查询：按「最近录入」或「连续号段」快速捞出要打书标的册。

**为什么单独一个接口**（不复用 `/holdings`）：书标要的是**每册**的
索书号 / 卷册号 / 种级排架号 / **复本序号**，而 `/holdings` 是条码管理页的视角。
这里一次把书标需要的列拼齐，省掉前端"每册再回头查一次记录"的 N+1。

只返回**在架**册 —— 注销 / 遗失的册已经不属于馆藏，打上书标贴上去就是错的。
"""
from __future__ import annotations

from datetime import date
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import BigInteger, case, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..db import get_db, like_escape
from ..models import STATUS_AVAILABLE, HoldingORM, RecordORM
from ..core.route_meta import api

router = APIRouter(prefix="/labels", tags=["labels"])

# 一次最多返回多少册：书标是"批"操作，但不该让一次查询把整库拉进内存。
MAX_LABEL_CANDIDATES = 2000


@router.get("/candidates")
@api(module="labels")
def label_candidates(
    library_id: int | None = None,
    record_ids: str | None = Query(None, max_length=22000, description="显式选书：记录 ID，逗号分隔，最多 2000 个"),
    created_from: date | None = Query(None, description="最近录入：起始日期 YYYY-MM-DD"),
    created_to: date | None = Query(None, description="最近录入：结束日期 YYYY-MM-DD"),
    barcode_prefix: str | None = Query(None, description="连续号段：条码前缀（可空）"),
    barcode_from: int | None = Query(None, ge=0, description="连续号段：起始号"),
    barcode_to: int | None = Query(None, ge=0, description="连续号段：结束号"),
    keyword: str | None = Query(None, alias="q", description="题名或条码片段"),
    location: str | None = Query(None, description="馆藏地点"),
    class_no: str | None = Query(None, description="分类号前缀"),
    limit: int = Query(500, ge=1, le=MAX_LABEL_CANDIDATES),
    db: Session = Depends(get_db),
):
    """书标候选（在架册）。

    两种筛法可单用也可合用：
      - **最近录入**：`created_from` / `created_to`（按册的入库时间，含当天）
      - **连续号段**：`barcode_prefix` + `barcode_from` / `barcode_to`
        （前缀可空；只匹配「前缀 + 纯数字」的条码，把数字段当整数比区间）

    `truncated=True` 表示命中数超过 `limit`，只返回了前 `limit` 条 —— 前端要如实告诉用户，
    不能静默截断（否则会以为"这批就这么多"）。
    """
    ids = None
    if record_ids is not None:
        parts = record_ids.split(",")
        if len(parts) > MAX_LABEL_CANDIDATES or any(
                not re.fullmatch(r"[0-9]{1,10}", part.strip()) for part in parts):
            raise HTTPException(422, "record_ids 必须是逗号分隔的正整数，最多 2000 个")
        ids = list(dict.fromkeys(int(part.strip()) for part in parts))
        if any(value < 1 or value > 2147483647 for value in ids):
            raise HTTPException(422, "record_ids 超出有效记录 ID 范围")

    q = (db.query(HoldingORM, RecordORM)
         .join(RecordORM, RecordORM.id == HoldingORM.record_id)
         .filter(HoldingORM.status == STATUS_AVAILABLE))
    if ids is not None:
        q = q.filter(HoldingORM.record_id.in_(ids))
    if library_id:
        q = q.filter(HoldingORM.library_id == library_id)
    # 时间口径与 stats.py 的工作量统计一致：显式 +08，日期含当天整天
    if created_from:
        q = q.filter(HoldingORM.created_at >= f"{created_from.isoformat()} 00:00:00+08")
    if created_to:
        q = q.filter(HoldingORM.created_at <= f"{created_to.isoformat()} 23:59:59+08")
    if keyword and keyword.strip():
        pat = f"%{like_escape(keyword.strip())}%"
        q = q.filter(or_(
            RecordORM.title_norm.ilike(pat, escape="\\"),
            HoldingORM.barcode.ilike(pat, escape="\\")))
    if location:
        q = q.filter(HoldingORM.location == location)
    if class_no and class_no.strip():
        q = q.filter(RecordORM.class_no.ilike(
            f"{like_escape(class_no.strip())}%", escape="\\"))

    pref = (barcode_prefix or "").strip()
    if pref or barcode_from is not None or barcode_to is not None:
        digits = func.substr(HoldingORM.barcode, len(pref) + 1)
        # ⚠️ 必须用 CASE 包住 cast：WHERE 各条件的求值顺序不保证，
        #    直接 cast('ABC…') 会让整条查询报错（invalid input syntax for type integer）。
        #    用 BigInteger 而不是 Integer：条码位数上限 16，Integer 会溢出。
        num = case((digits.op("~")(r"^[0-9]+$"), cast(digits, BigInteger)), else_=None)
        if pref:
            q = q.filter(HoldingORM.barcode.like(f"{pref}%"))
        if barcode_from is not None:
            q = q.filter(num >= barcode_from)
        if barcode_to is not None:
            q = q.filter(num <= barcode_to)

    # 复本序号 = 该册在**本记录内在架册**里的顺序（1 起）。
    # 与 `GET /records/{id}/holdings` 的 `order_by(HoldingORM.id)` 同一口径，
    # 保证"从图书库勾选进来"与"从本页快捷选进来"打出的书标一模一样。
    h2 = aliased(HoldingORM)
    copy_index = (select(func.count())
                  .where(h2.record_id == HoldingORM.record_id,
                         h2.status == STATUS_AVAILABLE,
                         h2.id <= HoldingORM.id)
                  .correlate(HoldingORM)
                  .scalar_subquery())

    total = q.count()
    rows = (q.add_columns(copy_index.label("copy_index"))
            .order_by(HoldingORM.library_id, HoldingORM.barcode)
            .limit(limit).all())
    items = [{
        "holding_id": h.id, "record_id": h.record_id, "library_id": h.library_id,
        "barcode": h.barcode, "call_no": h.call_no, "volume": h.volume,
        "shelf_no": h.shelf_no, "title": r.title_norm, "copy_index": ci,
    } for h, r, ci in rows]
    return {"total": total, "returned": len(items), "truncated": total > len(items),
            "items": items}
