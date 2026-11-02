# -*- coding: utf-8 -*-
"""盘点：建盘、扫码、未扫到 / 外借 / 异常清单、结束与导出。

盘点只出清单，不自动改馆藏状态；未扫到的册是否注销由馆员另行决定。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..db import get_db
from ..models import InventorySessionORM
from ..services.inventory import (SNAPSHOT_MAX_ROWS, close_session,
                                  create_session, list_missing, list_on_loan,
                                  list_problems, recent_scans, scan, scan_dict,
                                  session_dict)
from .book_libraries import resolve_library
from .common import xlsx_response
from ..core.route_meta import api
from ..schemas import InventorySessionList

router = APIRouter(prefix="/inventory", tags=["inventory"])

_SHELF_SUFFIX = r"(?<![A-Za-z0-9])\s*(?:[·,，/／]\s*)?{}\s*$"


def _standard_location(location: str | None, shelf_no: str | None) -> str:
    """导出用标准地点：旧快照里若已带同一排架号，去掉重复尾缀。"""
    loc = (location or "").strip()
    shelf = (shelf_no or "").strip()
    if not loc or not shelf:
        return loc
    pattern = re.compile(_SHELF_SUFFIX.format(re.escape(shelf)), re.IGNORECASE)
    return pattern.sub("", loc).strip().rstrip(" ·,，/／").strip() or loc


class SessionIn(BaseModel):
    library_id: int | None = None
    name: str | None = Field(None, max_length=128)
    note: str | None = Field(None, max_length=256)


class ScanIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=32)


class CloseIn(BaseModel):
    note: str | None = Field(None, max_length=256)


def _session_or_404(db: Session, session_id: int) -> InventorySessionORM:
    row = db.get(InventorySessionORM, session_id)
    if row is None:
        raise HTTPException(404, "盘点记录不存在")
    return row


@router.get("/sessions", response_model=InventorySessionList)
@api(module="circulation")
def list_sessions(library_id: int | None = None,
                  limit: int = Query(50, ge=1, le=100),
                  db: Session = Depends(get_db)):
    q = db.query(InventorySessionORM)
    if library_id:
        q = q.filter(InventorySessionORM.library_id == library_id)
    rows = (q.order_by(InventorySessionORM.id.desc()).limit(limit).all())
    return {"items": [session_dict(db, x) for x in rows]}


@router.post("/sessions")
@api(module="circulation")
def create_session_api(payload: SessionIn, request: Request,
                       db: Session = Depends(get_db)):
    library = resolve_library(db, payload.library_id)
    try:
        row = create_session(db, library_id=library.id, name=payload.name,
                             note=payload.note, operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(row)
    return {"session": session_dict(db, row),
            "message": f"盘点已开始：{row.name}（应盘 {row.expected_count} 册）"}


@router.get("/sessions/{session_id}")
@api(module="circulation")
def get_session(session_id: int, db: Session = Depends(get_db)):
    row = _session_or_404(db, session_id)
    return {**session_dict(db, row), "recent": recent_scans(db, row)}


@router.post("/sessions/{session_id}/scan")
@api(module="circulation")
def scan_api(session_id: int, payload: ScanIn, request: Request,
             db: Session = Depends(get_db)):
    row = _session_or_404(db, session_id)
    try:
        item, message = scan(db, session=row, barcode=payload.barcode,
                             operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(item)
    db.refresh(row)
    return {"scan": scan_dict(item, message=message),
            "session": session_dict(db, row), "message": message}


@router.post("/sessions/{session_id}/close")
@api(module="circulation")
def close_session_api(session_id: int, payload: CloseIn, request: Request,
                      db: Session = Depends(get_db)):
    row = _session_or_404(db, session_id)
    try:
        if payload.note and payload.note.strip():
            row.note = payload.note.strip()[:256]
        close_session(db, session=row, operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(row)
    return {"session": session_dict(db, row), "message": "盘点已结束，清单已生成"}


@router.get("/sessions/{session_id}/missing")
@api(module="circulation")
def get_missing(session_id: int, q: str | None = None,
                page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500),
                db: Session = Depends(get_db)):
    row = _session_or_404(db, session_id)
    total, items = list_missing(db, session=row, q=q, page=page,
                                page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size,
            "items": items}


@router.get("/sessions/{session_id}/on-loan")
@api(module="circulation")
def get_on_loan(session_id: int, q: str | None = None,
                page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500),
                db: Session = Depends(get_db)):
    row = _session_or_404(db, session_id)
    total, items = list_on_loan(db, session=row, q=q, page=page,
                                page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size,
            "items": items}


@router.get("/sessions/{session_id}/problems")
@api(module="circulation")
def get_problems(session_id: int, q: str | None = None,
                 page: int = Query(1, ge=1),
                 page_size: int = Query(50, ge=1, le=500),
                 db: Session = Depends(get_db)):
    row = _session_or_404(db, session_id)
    total, items = list_problems(db, session=row, q=q, page=page,
                                 page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size,
            "items": items}


@router.get("/sessions/{session_id}/export")
@api(module="circulation")
def export_session(session_id: int,
                   kind: str = Query("missing", description="missing | on_loan"),
                   db: Session = Depends(get_db)):
    row = _session_or_404(db, session_id)
    if kind not in ("missing", "on_loan"):
        raise HTTPException(400, "kind 只能是 missing / on_loan")
    if kind == "missing":
        total, items = list_missing(db, session=row, q=None, page=1,
                                    page_size=SNAPSHOT_MAX_ROWS)
        headers = ["条码", "题名", "索书号", "馆藏地", "排架号"]
        lines = [[x["barcode"] or "", x["title"] or "", x["call_no"] or "",
                  _standard_location(x.get("location"), x.get("shelf_no")),
                  x.get("shelf_no") or ""] for x in items]
        title = "未扫到"
    else:
        total, items = list_on_loan(db, session=row, q=None, page=1,
                                    page_size=SNAPSHOT_MAX_ROWS)
        headers = ["条码", "题名", "借书证号", "姓名", "班级",
                   "应还日期", "逾期天数"]
        lines = [[x["barcode"] or "", x["title"] or "",
                  x["reader_card_no"] or "", x["reader_name"] or "",
                  x["reader_class"] or "", x["due_on"],
                  x["days_overdue"]] for x in items]
        title = "外借未归"

    # 导出固定最多 SNAPSHOT_MAX_ROWS 行。超过时**必须明说**：静默截断会让馆员
    # 把不完整的清单当全量交上去。已结束盘点的快照本身可能已截断，真实总数
    # 记在 `result_snapshot["truncated"]` 里，两边取大。
    snap = row.result_snapshot if isinstance(row.result_snapshot, dict) else {}
    snapshot_total = int((snap.get("truncated") or {}).get(kind) or 0)
    known_total = max(int(total), snapshot_total)
    truncated = known_total > len(items)

    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for line in lines:
        ws.append(line)
    widths = ([18, 42, 18, 18, 12] if kind == "missing"
              else [18, 42, 14, 12, 16, 12, 10])
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"
    if truncated:
        # 单独一张说明表：不往数据行里塞提示，以免下游把提示当盘点结果。
        note = wb.create_sheet("导出说明")
        note.append(["导出截断提示"])
        note.append([f"清单共 {known_total} 条，本次只导出前 {len(items)} 条。"])
        note.append([f"单次导出上限 {SNAPSHOT_MAX_ROWS} 条；其余记录请通过页面分页查看。"
                     "如需完整清单，请分批盘点或直接查询数据库。"])
        note.column_dimensions["A"].width = 80
    resp = xlsx_response(wb, f"{row.name}-{title}.xlsx", count=len(items))
    # 头信息给前端/脚本用；打开文件的人看「导出说明」表。
    resp.headers["X-Total"] = str(known_total)
    resp.headers["X-Exported"] = str(len(items))
    resp.headers["X-Truncated"] = "1" if truncated else "0"
    return resp
