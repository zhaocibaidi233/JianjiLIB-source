# -*- coding: utf-8 -*-
"""财产登记表：按册展开的进书清册，上级检查要用的账目。

## 为什么是「按册」而不是「按种」

财产登记表（财产账）记的是**每一册实物**：一册一行、一个登记号一行，末尾合计
册数与金额。按种汇总那是"总括登记账"（统计页在做），两本账不能混 —— 上级检查
时是拿账本去架上一册一册对的。

## 登记号用条码

传统账本的第一列是「登记号 / 财产号」，按到馆顺序流水编号。我们**不新增一列**：
条码就是本系统的财产标识（图书库以条码为核心，README 规则 5），再设一套财产号
等于两本编号要人肉对齐。账面上「登记号」直接取条码，排序也按它（同位数补零，
字符串序即数字序）。

## 已注销的册要不要出现

要 —— 但**默认不出现**。财产账在账期间只列在架册；勾上「只看已注销」才能拉出
剔旧清单（注销号与原因是留账凭据）。两者都带注销号列，注销册在账面上看得出来。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.pricing import record_price
from ..db import get_db, like_escape
from ..models import (INACTIVE_STATUSES, STATUS_AVAILABLE, STATUS_SUSPENDED,
                      BookLibrary, HoldingORM, RecordORM)
from ..services import dictionaries as dicts
from ..core.route_meta import api
from .common import xlsx_response

router = APIRouter(prefix="/property-register", tags=["property-register"])

# 一次最多出多少行。财产账要给纸质账本用，一次几百册是常态；
# 整库十几万册不可能一页打完，必须按条码区间/时间分批出账。
MAX_ROWS = 5000

SORTS = ("barcode", "call_no")


class PropertyRegisterIn(BaseModel):
    """财产登记表筛选条件。

    用 POST + body 而不是 GET：条件有十来个（条码区间、日期区间、地点、
    分类号、状态、排序），拼 query string 既容易超长也不好校验。
    """
    library_id: int | None = None            # 缺省 = 默认图书库
    barcode_from: str | None = None
    barcode_to: str | None = None
    date_from: str | None = None             # 按**录入时间**（登记日期）
    date_to: str | None = None
    location: str | None = None
    class_no: str | None = None              # 分类号前缀
    # active（默认，只要在架）/ all / withdrawn（只要已注销与已遗失）
    status: str = "active"
    q: str | None = None                     # 条码或题名片段
    sort: str = "barcode"


# ---------------------------------------------------------------------------
#  MARC 取值：直接读 JSONB，不为每行构造 Record
# ---------------------------------------------------------------------------

def _sub(fields: list | None, tag: str, *codes: str) -> str | None:
    """取该 tag 下第一个非空子字段值（按 codes 的优先级）。"""
    for f in fields or []:
        if f.get("tag") != tag:
            continue
        for code in codes:
            for c, v in (f.get("sf") or []):
                if c == code and (v or "").strip():
                    return v.strip()
    return None


def _parse_day(v: str | None, field: str) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        raise HTTPException(400, f"{field} 需为 YYYY-MM-DD 格式：{v}")


def _rows(db: Session, payload: PropertyRegisterIn, currency: str) -> tuple[list[dict], dict]:
    query = (db.query(HoldingORM, RecordORM)
             .join(RecordORM, RecordORM.id == HoldingORM.record_id))
    lib_id = payload.library_id
    if lib_id:
        lib = db.get(BookLibrary, lib_id)
        if lib is None:
            raise HTTPException(404, f"图书库不存在：{lib_id}")
        query = query.filter(HoldingORM.library_id == lib_id)
    if payload.barcode_from:
        query = query.filter(HoldingORM.barcode >= payload.barcode_from.strip())
    if payload.barcode_to:
        query = query.filter(HoldingORM.barcode <= payload.barcode_to.strip())
    if payload.location:
        query = query.filter(HoldingORM.location == payload.location.strip())
    if payload.class_no:
        query = query.filter(RecordORM.class_no.ilike(
            f"{like_escape(payload.class_no.strip())}%", escape="\\"))
    if payload.q:
        pat = f"%{like_escape(payload.q.strip())}%"
        query = query.filter(HoldingORM.barcode.ilike(pat, escape="\\")
                             | RecordORM.title_norm.ilike(pat, escape="\\"))
    d_from = _parse_day(payload.date_from, "date_from")
    if d_from:
        query = query.filter(HoldingORM.created_at >= datetime.combine(d_from, time.min))
    d_to = _parse_day(payload.date_to, "date_to")
    if d_to:
        # 含当天：用「次日 0 点」开区间，免得用户以为当天漏了
        query = query.filter(HoldingORM.created_at
                             < datetime.combine(d_to + timedelta(days=1), time.min))
    if payload.status == "active":
        query = query.filter(HoldingORM.status == STATUS_AVAILABLE)
    elif payload.status == "withdrawn":
        query = query.filter(HoldingORM.status.in_(INACTIVE_STATUSES))
    elif payload.status == "suspended":
        query = query.filter(HoldingORM.status == STATUS_SUSPENDED)
    elif payload.status != "all":
        raise HTTPException(400, "status 只能是 active / withdrawn / suspended / all")

    total = query.count()
    if total > MAX_ROWS:
        raise HTTPException(400, (
            f"符合条件的有 {total} 册，超过单次上限 {MAX_ROWS}。"
            f"请用条码区间或登记日期分批出账。"))

    order = (HoldingORM.barcode, HoldingORM.id) if payload.sort == "barcode" \
        else (HoldingORM.call_no, HoldingORM.barcode)
    pairs = query.order_by(*order).all()

    rows: list[dict] = []
    amount = 0.0
    titles: set[int] = set()
    for h, r in pairs:
        fields = r.fields or []
        # 定价取 records.price 冗余列（010$d 的解析结果，写入时同步，见）：
        # 账目上"每册单价 × 册数 = 合计"必须与统计页的码洋同源，不能再解析一遍。
        price = record_price(r, currency)
        amount += price
        titles.add(r.id)
        rows.append({
            "barcode": h.barcode,
            "call_no": h.call_no,
            "class_no": r.class_no,
            "title": _sub(fields, "200", "a") or r.title_norm or "",
            "author": _sub(fields, "200", "f", "g") or r.author_norm or "",
            "publisher": _sub(fields, "210", "c") or "",
            "year": _sub(fields, "210", "d") or (str(r.year) if r.year else ""),
            "pages": _sub(fields, "215", "a") or "",
            "binding": _sub(fields, "010", "b") or "",
            "price": price,
            "isbn": r.isbn_norm or "",
            "volume": h.volume or "",
            "location": h.location or "",
            "shelf_no": h.shelf_no or "",
            "source": h.source or r.source or "",
            "status": h.status,
            "registered_at": (h.acquired_at or h.created_at).strftime("%Y-%m-%d")
                             if (h.acquired_at or h.created_at) else "",
            "withdraw_no": h.withdraw_no or "",
            "withdraw_reason": h.withdraw_reason or "",
        })

    summary = {
        "copies": len(rows),
        "titles": len(titles),
        "amount": round(amount, 2),
        "currency": currency,
        "withdrawn": sum(1 for x in rows if x["status"] in INACTIVE_STATUSES),
    }
    return rows, summary


@router.post("/preview")
@api(module="stats", level="read")
def property_register(payload: PropertyRegisterIn, db: Session = Depends(get_db)):
    """财产登记表数据（只读）。按册展开，含合计行所需的汇总。

    预览与打印用的是同一份数据：前端拿到什么就打什么，不另做一份"打印版查询"。
    """
    if payload.sort not in SORTS:
        raise HTTPException(400, f"sort 只能是 {' / '.join(SORTS)}")
    currency = dicts.default_currency(db)
    rows, summary = _rows(db, payload, currency)
    lib = (db.get(BookLibrary, payload.library_id) if payload.library_id
           else db.query(BookLibrary).order_by(BookLibrary.id).first())
    return {
        "library": {"id": lib.id, "name": lib.name} if lib else None,
        "currency": currency,
        "summary": summary,
        "rows": rows,
    }


@router.post("/export")
@api(module="stats")
def export_property_register(payload: PropertyRegisterIn,
                             db: Session = Depends(get_db)):
    """按当前筛选导出一册一行的财产清册（Excel）。"""
    if payload.sort not in SORTS:
        raise HTTPException(400, f"sort 只能是 {' / '.join(SORTS)}")
    currency = dicts.default_currency(db)
    rows, summary = _rows(db, payload, currency)
    lib = (db.get(BookLibrary, payload.library_id) if payload.library_id
           else db.query(BookLibrary).order_by(BookLibrary.id).first())
    name = lib.name if lib else "图书库"

    wb = Workbook()
    ws = wb.active
    ws.title = "财产登记"
    headers = ["登记号（条码）", "索书号", "分类号", "题名", "责任者", "出版者",
               "出版年", "页数", "装帧", "单价", "馆藏地", "排架号", "状态", "登记日期",
               "注销号", "注销原因"]
    ws.append(headers)
    status_labels = {STATUS_AVAILABLE: "在架", "WITHDRAWN": "已注销",
                     "LOST": "已遗失", STATUS_SUSPENDED: "停流通"}
    for row in rows:
        ws.append([
            row["barcode"] or "", row["call_no"] or "", row["class_no"] or "",
            row["title"], row["author"], row["publisher"], row["year"],
            row["pages"], row["binding"], row["price"], row["location"],
            row["shelf_no"], status_labels.get(row["status"], row["status"]),
            row["registered_at"], row["withdraw_no"], row["withdraw_reason"],
        ])
    ws.append(["合计", "", "", f"{summary['titles']} 种", "", "", "", "", "",
               summary["amount"], f"{summary['copies']} 册", "", "", "", "", ""])
    widths = [18, 16, 12, 42, 20, 28, 10, 10, 10, 10, 20, 12, 10, 14, 16, 16]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width
    ws.freeze_panes = "A2"
    filename = f"{name}-财产登记-{date.today().isoformat()}.xlsx"
    return xlsx_response(wb, filename, count=summary["copies"])


@router.get("/locations")
@api(module="stats")
def property_locations(library_id: int | None = None, db: Session = Depends(get_db)):
    """该库实际用到的馆藏地点（给出账页的下拉）。"""
    query = db.query(HoldingORM.location).filter(HoldingORM.location.isnot(None),
                                                 HoldingORM.location != "")
    if library_id:
        query = query.filter(HoldingORM.library_id == library_id)
    return sorted({loc for (loc,) in query.all()})
