# -*- coding: utf-8 -*-
"""总括登录 / 个别登录账（教基〔2018〕5号第十六条）。

- 总括登录：按 accession_batch_no 汇总种数、册数、金额、入藏日期范围。
- 个别登录：逐册登录号、条码、题名、索书号、价格、入藏日期、来源、馆藏地。

两本账是**资产台账**，包含已注销/遗失/停流通的册；不是 905（905 只含可流通册）。
"""
from __future__ import annotations

from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from openpyxl import Workbook
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from ..core.route_meta import api
from ..db import get_db
from ..models import BookLibrary, HoldingORM, RecordORM
from .common import xlsx_response

router = APIRouter(prefix="/accession", tags=["accession"])


def _parse_day(value: str | None, field: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"{field} 必须是 YYYY-MM-DD")


def _library(db: Session, library_id: int | None) -> BookLibrary:
    lib = (db.get(BookLibrary, library_id) if library_id
           else db.query(BookLibrary).order_by(BookLibrary.id).first())
    if lib is None:
        raise HTTPException(400, "还没有图书库")
    return lib


@router.get("/summary")
@api(module="stats", level="read", capability="report.accession")
def accession_summary(library_id: int | None = None,
                      date_from: str | None = None,
                      date_to: str | None = None,
                      db: Session = Depends(get_db)):
    lib = _library(db, library_id)
    d_from = _parse_day(date_from, "date_from")
    d_to = _parse_day(date_to, "date_to")
    q = (db.query(
            HoldingORM.accession_batch_no,
            func.count(distinct(HoldingORM.record_id)),
            func.count(HoldingORM.id),
            func.coalesce(func.sum(HoldingORM.price), 0),
            func.min(HoldingORM.acquired_at),
            func.max(HoldingORM.acquired_at),
         )
         .filter(HoldingORM.library_id == lib.id)
         .group_by(HoldingORM.accession_batch_no)
         .order_by(HoldingORM.accession_batch_no))
    if d_from:
        q = q.filter(HoldingORM.acquired_at >= datetime.combine(d_from, time.min))
    if d_to:
        q = q.filter(HoldingORM.acquired_at
                     < datetime.combine(d_to, time.max))
    rows = []
    for batch, titles, copies, amount, first_at, last_at in q.all():
        rows.append({
            "batch_no": batch or "未分批",
            "titles": int(titles or 0),
            "copies": int(copies or 0),
            "amount": float(amount or 0),
            "first_at": first_at.isoformat() if first_at else None,
            "last_at": last_at.isoformat() if last_at else None,
        })
    return {"library": {"id": lib.id, "name": lib.name},
            "summary": {
                "batches": len(rows),
                "titles": sum(x["titles"] for x in rows),
                "copies": sum(x["copies"] for x in rows),
                "amount": round(sum(x["amount"] for x in rows), 2),
            },
            "batches": rows}


@router.get("/items")
@api(module="stats", level="read", capability="report.accession")
def accession_items(library_id: int | None = None,
                    batch_no: str | None = None,
                    date_from: str | None = None,
                    date_to: str | None = None,
                    limit: int = Query(200, ge=1, le=1000),
                    offset: int = Query(0, ge=0),
                    db: Session = Depends(get_db)):
    lib = _library(db, library_id)
    d_from = _parse_day(date_from, "date_from")
    d_to = _parse_day(date_to, "date_to")
    q = (db.query(HoldingORM, RecordORM)
         .join(RecordORM, HoldingORM.record_id == RecordORM.id)
         .filter(HoldingORM.library_id == lib.id)
         .order_by(HoldingORM.acquired_at, HoldingORM.accession_no, HoldingORM.id))
    if batch_no:
        q = q.filter(HoldingORM.accession_batch_no == batch_no)
    if d_from:
        q = q.filter(HoldingORM.acquired_at >= datetime.combine(d_from, time.min))
    if d_to:
        q = q.filter(HoldingORM.acquired_at < datetime.combine(d_to, time.max))
    total = q.count()
    rows = []
    for h, r in q.offset(offset).limit(limit).all():
        rows.append({
            "id": h.id,
            "accession_no": h.accession_no or h.barcode,
            "barcode": h.barcode,
            "record_id": h.record_id,
            "title": r.title_norm or "",
            "call_no": h.call_no,
            "price": float(h.price) if h.price is not None else None,
            "acquired_at": h.acquired_at.isoformat() if h.acquired_at else None,
            "source": h.source or r.source,
            "location": h.location,
            "status": h.status,
        })
    return {"library": {"id": lib.id, "name": lib.name},
            "total": total, "items": rows}


@router.get("/export")
@api(module="stats", capability="report.accession")
def export_accession(library_id: int | None = None,
                     batch_no: str | None = None,
                     date_from: str | None = None,
                     date_to: str | None = None,
                     db: Session = Depends(get_db)):
    """导出两页 Excel：总括登录 + 个别登录。"""
    summary = accession_summary(library_id, date_from, date_to, db)
    items = accession_items(library_id, batch_no, date_from, date_to,
                            limit=100000, offset=0, db=db)
    wb = Workbook()
    ws = wb.active
    ws.title = "总括登录"
    ws.append(["批次号", "种数", "册数", "金额", "首次入藏", "最后入藏"])
    for row in summary["batches"]:
        ws.append([row["batch_no"], row["titles"], row["copies"], row["amount"],
                   row["first_at"] or "", row["last_at"] or ""])
    ws.append(["合计", summary["summary"]["titles"],
               summary["summary"]["copies"], summary["summary"]["amount"], "", ""])

    ws2 = wb.create_sheet("个别登录")
    ws2.append(["登录号", "条码", "题名", "索书号", "价格", "入藏日期",
                "来源", "馆藏地", "状态"])
    for row in items["items"]:
        ws2.append([row["accession_no"] or "", row["barcode"] or "",
                    row["title"] or "", row["call_no"] or "",
                    row["price"] if row["price"] is not None else "",
                    row["acquired_at"] or "", row["source"] or "",
                    row["location"] or "", row["status"]])
    for sheet in (ws, ws2):
        sheet.freeze_panes = "A2"
    name = f"{summary['library']['name']}-登录账"
    return xlsx_response(wb, f"{name}.xlsx", count=items["total"])
