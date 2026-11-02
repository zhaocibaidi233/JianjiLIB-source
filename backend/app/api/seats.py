# -*- coding: utf-8 -*-
"""读者端座位预约路由。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ReaderORM, Seat, SeatArea, SeatBooking
from ..services import seats as service
from ..core.route_meta import api
from .portal import current_reader

router = APIRouter(prefix="/portal/seats", tags=["portal-seats"])


def _booking_payload(db: Session, booking: SeatBooking) -> dict:
    seat = db.get(Seat, booking.seat_id)
    area = db.get(SeatArea, seat.area_id) if seat else None
    return service.booking_dict(booking, seat, area)


# 并发下预检查会同时放行，最终由两个部分唯一索引分别兜底。两个索引对应两条
# 完全不同的业务规则，提示语不能混用：说成"座位已被预约"会让读者一直重试，
# 而实际规则是"每人每天一个"，重试永远不会成功。
_INTEGRITY_RULES = {
    "uq_seat_bookings_reader_day": (400, "每位读者每天只能预约一个座位"),
    "uq_seat_bookings_active_slot": (409, "该座位已被预约，请重新选择"),
}


def _conflict(exc: IntegrityError) -> HTTPException:
    """把唯一索引冲突翻译成对应的业务提示。"""
    name = ""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    if diag is not None:
        name = getattr(diag, "constraint_name", "") or ""
    if not name:  # 驱动或版本拿不到约束名时退回字符串匹配
        text = str(getattr(exc, "orig", exc))
        name = next((k for k in _INTEGRITY_RULES if k in text), "")
    status, message = _INTEGRITY_RULES.get(name, (409, "预约冲突，请刷新后重试"))
    return HTTPException(status, message)


def _parse_day(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "date 需为 YYYY-MM-DD 格式")


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "start_time 需为 HH:MM 格式")


@router.get("/areas")
@api(module="auth")
def seat_areas(reader: ReaderORM = Depends(current_reader),
               db: Session = Depends(get_db)):
    if service.expire_no_shows(db, reader.id):
        db.commit()
    areas = (db.query(SeatArea)
             .filter(SeatArea.library_id == reader.library_id,
                     SeatArea.enabled.is_(True))
             .order_by(SeatArea.id).all())
    return {"areas": [service.area_dict(area) for area in areas],
            "ban": service.reader_ban(db, reader.id),
            "reader_status": reader.status}


@router.get("")
@api(module="auth")
def seat_availability(date_str: str | None = Query(None, alias="date"),
                      area_id: int | None = None,
                      slot: str | None = None,
                      reader: ReaderORM = Depends(current_reader),
                      db: Session = Depends(get_db)):
    day = _parse_day(date_str)
    today = date.today()
    if day < today or day >= today + timedelta(days=service.BOOKING_DAYS):
        raise HTTPException(400, f"只能查看今天起 {service.BOOKING_DAYS} 天内的座位")
    if service.expire_no_shows(db, reader.id):
        db.commit()
    area_query = db.query(SeatArea).filter(
        SeatArea.library_id == reader.library_id, SeatArea.enabled.is_(True))
    area = (db.get(SeatArea, area_id) if area_id
            else area_query.order_by(SeatArea.id).first())
    if area is None or area.library_id != reader.library_id:
        raise HTTPException(404, "座位区域不存在")
    slots = service.slots_for(area)
    if not slots:
        raise HTTPException(400, "该区域没有可用时段")
    if slot:
        selected = next((item for item in slots if item["start"] == slot), None)
        if selected is None:
            raise HTTPException(400, "预约时段不合法")
    else:
        now = datetime.now()
        if day == today:
            selected = next((item for item in slots
                             if item["start"] >= f"{now.hour:02d}:{now.minute:02d}"),
                            slots[-1])
        else:
            selected = slots[0]
    try:
        result = service.seat_map(db, area=area, day=day,
                                  slot_start=_parse_time(selected["start"]),
                                  reader_id=reader.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


class BookingIn(BaseModel):
    seat_id: int
    booking_date: str
    start_time: str = Field(min_length=5, max_length=5)


@router.post("/bookings")
@api(module="auth")
def create_seat_booking(payload: BookingIn,
                        reader: ReaderORM = Depends(current_reader),
                        db: Session = Depends(get_db)):
    seat = db.get(Seat, payload.seat_id)
    if seat is None:
        raise HTTPException(404, "座位不存在")
    area = db.get(SeatArea, seat.area_id)
    if area is None or area.library_id != reader.library_id:
        raise HTTPException(404, "座位不属于当前图书库")
    day = _parse_day(payload.booking_date)
    start = _parse_time(payload.start_time)
    if service.expire_no_shows(db):
        db.commit()
    try:
        booking = service.create_booking(db, reader=reader, seat=seat,
                                         day=day, start=start)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    except IntegrityError as e:
        db.rollback()
        raise _conflict(e)
    db.refresh(booking)
    return {"booking": _booking_payload(db, booking), "message": "座位预约成功"}


@router.get("/bookings")
@api(module="auth")
def my_seat_bookings(reader: ReaderORM = Depends(current_reader),
                     db: Session = Depends(get_db)):
    expire = service.expire_no_shows(db, reader.id)
    if expire:
        db.commit()
    return {"items": service.my_bookings(db, reader.id),
            "ban": service.reader_ban(db, reader.id)}


@router.post("/bookings/{booking_id}/cancel")
@api(module="auth")
def cancel_seat_booking(booking_id: int,
                        reader: ReaderORM = Depends(current_reader),
                        db: Session = Depends(get_db)):
    booking = db.get(SeatBooking, booking_id)
    try:
        service.cancel_booking(db, booking=booking, reader_id=reader.id)
        db.commit()
    except service.BookingNoShowError as e:
        # 迟到取消仍然返 400，但必须先提交爽约，不能借取消逃避处罚。
        db.commit()
        raise HTTPException(400, str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    return {"message": "座位预约已取消"}


@router.post("/bookings/{booking_id}/check-in")
@api(module="auth")
def check_in_seat(booking_id: int,
                  reader: ReaderORM = Depends(current_reader),
                  db: Session = Depends(get_db)):
    booking = db.get(SeatBooking, booking_id)
    try:
        service.check_in(db, booking=booking, reader_id=reader.id)
        db.commit()
    except service.BookingNoShowError as e:
        # 错误响应与状态提交分开：签到失败不代表本次爽约判定应该回滚。
        db.commit()
        raise HTTPException(400, str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(booking)
    return {"booking": _booking_payload(db, booking), "message": "签到成功"}
