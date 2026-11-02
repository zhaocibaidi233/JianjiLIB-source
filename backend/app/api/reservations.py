# -*- coding: utf-8 -*-
"""预约到书：馆员端队列、到书、取消与外借履行。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..db import get_db, like_escape
from ..models import (RESERVATION_CANCELLED, RESERVATION_EXPIRED,
                      RESERVATION_FULFILLED, RESERVATION_PENDING,
                      RESERVATION_READY, HoldingORM, ReaderORM,
                      RecordORM, ReservationORM)
from ..services import circulation as loan_service
from ..services import reservations as service
from ..services.circulation import loan_dict
from ..services.paging import paginate
from ..core.route_meta import api
from ..schemas import ReservationPage
from .book_libraries import resolve_library

router = APIRouter(prefix="/reservations", tags=["reservations"])


class ReservationIn(BaseModel):
    reader_id: int
    record_id: int
    note: str | None = Field(None, max_length=256)


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=128)


class FulfillIn(BaseModel):
    barcode: str | None = Field(None, max_length=32)


def _local_day_start(d: date) -> datetime:
    return datetime.combine(d, time.min).astimezone(timezone.utc)


def _reservation_or_404(db: Session, reservation_id: int) -> ReservationORM:
    row = db.get(ReservationORM, reservation_id)
    if row is None:
        raise HTTPException(404, "预约记录不存在")
    return row


def _summary(db: Session, library_id: int | None) -> dict:
    q = db.query(ReservationORM)
    if library_id:
        q = q.filter(ReservationORM.library_id == library_id)
    pending = q.filter(ReservationORM.status == RESERVATION_PENDING).count()
    ready = q.filter(ReservationORM.status == RESERVATION_READY).count()
    now = datetime.now(timezone.utc)
    expiring = (q.filter(ReservationORM.status == RESERVATION_READY,
                         ReservationORM.expires_at.isnot(None),
                         ReservationORM.expires_at < now + timedelta(hours=24))
                .count())
    fulfilled_today = (q.filter(
        ReservationORM.status == RESERVATION_FULFILLED,
        ReservationORM.fulfilled_at >= _local_day_start(date.today()),
        ReservationORM.fulfilled_at < _local_day_start(date.today() + timedelta(days=1)))
        .count())
    return {"pending": int(pending), "ready": int(ready),
            "expiring_24h": int(expiring),
            "fulfilled_today": int(fulfilled_today),
            "hold_days": int(loan_service.rules_value(db)["reservation_hold_days"])}


@router.get("", response_model=ReservationPage)
@api(module="circulation")
def list_reservations(
    library_id: int | None = None,
    status: str = Query("active",
                        description="active | pending | ready | closed | all"),
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if library_id is not None:
        resolve_library(db, library_id)

    expired = service.expire_ready(db, library_id=library_id)
    if expired:
        db.commit()

    query = db.query(ReservationORM).outerjoin(
        ReaderORM, ReaderORM.id == ReservationORM.reader_id).outerjoin(
        RecordORM, RecordORM.id == ReservationORM.record_id)
    if library_id:
        query = query.filter(ReservationORM.library_id == library_id)
    if status == "active":
        query = query.filter(ReservationORM.status.in_((RESERVATION_PENDING,
                                                        RESERVATION_READY)))
    elif status == "pending":
        query = query.filter(ReservationORM.status == RESERVATION_PENDING)
    elif status == "ready":
        query = query.filter(ReservationORM.status == RESERVATION_READY)
    elif status == "closed":
        query = query.filter(ReservationORM.status.in_(
            (RESERVATION_FULFILLED, RESERVATION_CANCELLED, RESERVATION_EXPIRED)))
    elif status != "all":
        raise HTTPException(400, "status 只能是 active / pending / ready / closed / all")
    if q:
        s = like_escape(q.strip())
        query = query.filter(or_(
            ReaderORM.name.ilike(f"%{s}%", escape="\\"),
            ReaderORM.card_no.ilike(f"%{s}%", escape="\\"),
            RecordORM.title_norm.ilike(f"%{s}%", escape="\\"),
            RecordORM.isbn_norm.ilike(f"%{s}%", escape="\\")))

    priority = case((ReservationORM.status == RESERVATION_READY, 0),
                    (ReservationORM.status == RESERVATION_PENDING, 1),
                    else_=2)
    total, rows = paginate(query.order_by(priority,
                                          ReservationORM.reserved_at.asc(),
                                          ReservationORM.id.asc()),
                           page, page_size)
    return {"total": total, "page": page, "page_size": page_size,
            "items": service.serialize_many(db, rows),
            "summary": _summary(db, library_id)}


@router.post("")
@api(module="circulation")
def create_reservation(payload: ReservationIn, request: Request,
                       db: Session = Depends(get_db)):
    reader = db.get(ReaderORM, payload.reader_id)
    if reader is None:
        raise HTTPException(404, "读者不存在")
    record = db.get(RecordORM, payload.record_id)
    if record is None:
        raise HTTPException(404, "书目记录不存在")
    try:
        row = service.create_reservation(
            db, reader=reader, record=record, note=payload.note,
            operator=operator_of(request))
        db.commit()
    except service.DuplicateReservationError as e:
        db.rollback()
        raise HTTPException(409, str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该读者已预约此书，不能重复预约")
    db.refresh(row)
    return {"reservation": service.serialize_many(db, [row])[0],
            "message": f"已为 {reader.name} 登记预约"}


@router.post("/{reservation_id}/ready")
@api(module="circulation")
def mark_ready(reservation_id: int, request: Request,
               db: Session = Depends(get_db)):
    row = _reservation_or_404(db, reservation_id)
    try:
        service.mark_ready(db, row=row, operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(row)
    return {"reservation": service.serialize_many(db, [row])[0],
            "message": "已标记为待取书"}


@router.post("/{reservation_id}/cancel")
@api(module="circulation")
def cancel_reservation(reservation_id: int, payload: CancelIn, request: Request,
                       db: Session = Depends(get_db)):
    row = _reservation_or_404(db, reservation_id)
    try:
        # 服务获锁后重新判定状态，并在同一事务推进队列。
        service.cancel(db, row=row, reason=payload.reason,
                       operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(row)
    return {"reservation": service.serialize_many(db, [row])[0],
            "message": "预约已取消"}


@router.post("/{reservation_id}/fulfill")
@api(module="circulation")
def fulfill_reservation(reservation_id: int, payload: FulfillIn, request: Request,
                        db: Session = Depends(get_db)):
    row = _reservation_or_404(db, reservation_id)
    operator = operator_of(request)
    loan = None
    if payload.barcode and payload.barcode.strip():
        reader = db.get(ReaderORM, row.reader_id)
        record = db.get(RecordORM, row.record_id)
        if reader is None or record is None:
            raise HTTPException(404, "预约关联的读者或书目不存在")
        holding = (db.query(HoldingORM)
                   .filter(HoldingORM.barcode == payload.barcode.strip(),
                           HoldingORM.library_id == row.library_id)
                   .first())
        if holding is None:
            raise HTTPException(404, f"条码不存在：{payload.barcode.strip()}")
        if holding.record_id != row.record_id:
            raise HTTPException(400, "该条码不属于预约的书目")
        try:
            loan = loan_service.borrow(
                db, reader=reader, holding=holding,
                title=record.title_norm, rules=loan_service.rules_value(db),
                operator=operator)
            service.fulfill(db, row=row, operator=operator)
            db.commit()
        except ValueError as e:
            db.rollback()
            raise HTTPException(400, str(e))
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "该册已被外借，请刷新后重试")
        db.refresh(loan)
    else:
        try:
            service.fulfill(db, row=row, operator=operator)
            db.commit()
        except ValueError as e:
            db.rollback()
            raise HTTPException(400, str(e))
    db.refresh(row)
    return {"reservation": service.serialize_many(db, [row])[0],
            "loan": loan_dict(loan) if loan is not None else None,
            "message": "已登记外借" if loan is None else "外借成功，预约已履行"}
