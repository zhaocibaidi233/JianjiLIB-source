# -*- coding: utf-8 -*-
"""座位预约。

时段按区域开放时间切为 2 小时；每位读者每天最多一个有效预约；座位占用由
数据库部分唯一索引兜底。签到窗口为开始前 30 分钟至开始后 30 分钟，超过即爽约；
连续 3 次爽约后 7 天内不能预约。

本模块只做判定与 flush/commit 由 API 控制的部分；列表查询会顺手把过期未签到
的预约置为爽约（懒清理，不引入调度器）。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import (SEAT_BOOKED, SEAT_CANCELLED, SEAT_CHECKED_IN,
                      SEAT_NO_SHOW, SEAT_STATUS_LABELS, READER_ACTIVE,
                      Seat, SeatArea, SeatBooking)

SLOT_MINUTES = 120
CHECKIN_GRACE_MINUTES = 30
NO_SHOW_LIMIT = 3
NO_SHOW_BAN_DAYS = 7
BOOKING_DAYS = 7


def _now_local() -> datetime:
    return datetime.now()


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def slots_for(area: SeatArea) -> list[dict]:
    start = _minutes(area.open_time)
    end = _minutes(area.close_time)
    out: list[dict] = []
    cursor = start
    while cursor + SLOT_MINUTES <= end:
        out.append({
            "start": f"{cursor // 60:02d}:{cursor % 60:02d}",
            "end": f"{(cursor + SLOT_MINUTES) // 60:02d}:{(cursor + SLOT_MINUTES) % 60:02d}",
        })
        cursor += SLOT_MINUTES
    return out


def area_dict(area: SeatArea) -> dict:
    return {
        "id": area.id, "library_id": area.library_id, "name": area.name,
        "floor": area.floor, "description": area.description,
        "rows": area.rows, "cols": area.cols,
        "open_time": _hhmm(area.open_time), "close_time": _hhmm(area.close_time),
        "slots": slots_for(area), "enabled": bool(area.enabled),
    }


def seat_dict(seat: Seat) -> dict:
    return {"id": seat.id, "code": seat.code, "row": seat.row, "col": seat.col,
            "kind": seat.kind, "enabled": bool(seat.enabled)}


def booking_dict(booking: SeatBooking, seat: Seat | None = None,
                 area: SeatArea | None = None) -> dict:
    return {
        "id": booking.id, "seat_id": booking.seat_id,
        "seat_code": seat.code if seat else None,
        "seat_kind": seat.kind if seat else None,
        "area_id": area.id if area else None,
        "area_name": area.name if area else None,
        "floor": area.floor if area else None,
        "booking_date": booking.booking_date.isoformat(),
        "start_time": _hhmm(booking.start_time), "end_time": _hhmm(booking.end_time),
        "status": booking.status,
        "status_label": SEAT_STATUS_LABELS.get(booking.status, booking.status),
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
        "checked_in_at": booking.checked_in_at.isoformat() if booking.checked_in_at else None,
        "cancelled_at": booking.cancelled_at.isoformat() if booking.cancelled_at else None,
    }


def expire_no_shows(db: Session, reader_id: int | None = None) -> int:
    """把超过签到窗口仍未签到的预约置为爽约。**不 commit**。"""
    now = _now_local()
    cutoff = now - timedelta(minutes=CHECKIN_GRACE_MINUTES)
    query = db.query(SeatBooking).filter(SeatBooking.status == SEAT_BOOKED)
    if reader_id is not None:
        query = query.filter(SeatBooking.reader_id == reader_id)
    changed = 0
    for row in (query.order_by(SeatBooking.id).populate_existing()
                .with_for_update().all()):
        start_dt = datetime.combine(row.booking_date, row.start_time)
        if start_dt < cutoff:
            row.status = SEAT_NO_SHOW
            changed += 1
    if changed:
        db.flush()
    return changed


def reader_ban(db: Session, reader_id: int) -> dict:
    """连续 3 次爽约后暂停预约 7 天（从最近一次爽约日起算）。"""
    rows = (db.query(SeatBooking)
            .filter(SeatBooking.reader_id == reader_id,
                    SeatBooking.status == SEAT_NO_SHOW)
            .order_by(SeatBooking.booking_date.desc(),
                      SeatBooking.start_time.desc())
            .limit(NO_SHOW_LIMIT).all())
    if len(rows) < NO_SHOW_LIMIT:
        return {"banned": False, "until": None, "no_show_count": len(rows)}
    latest = rows[0].booking_date
    until = latest + timedelta(days=NO_SHOW_BAN_DAYS)
    banned = date.today() < until
    return {"banned": banned, "until": until.isoformat() if banned else None,
            "no_show_count": len(rows)}


def seat_map(db: Session, *, area: SeatArea, day: date, slot_start: time,
             reader_id: int) -> dict:
    slots = slots_for(area)
    slot = next((item for item in slots if item["start"] == _hhmm(slot_start)), None)
    if slot is None:
        raise ValueError("该时段不在开放时间内")
    rows = (db.query(Seat).filter(Seat.area_id == area.id)
            .order_by(Seat.row, Seat.col).all())
    bookings = (db.query(SeatBooking)
                .filter(SeatBooking.seat_id.in_([s.id for s in rows]),
                        SeatBooking.booking_date == day,
                        SeatBooking.start_time == slot_start,
                        SeatBooking.status.in_((SEAT_BOOKED, SEAT_CHECKED_IN)))
                .all()) if rows else []
    by_seat = {b.seat_id: b for b in bookings}
    out = []
    for seat in rows:
        booking = by_seat.get(seat.id)
        item = seat_dict(seat)
        item["booking_id"] = booking.id if booking is not None else None
        item["mine"] = booking is not None and booking.reader_id == reader_id
        item["booked"] = booking is not None and booking.reader_id != reader_id
        out.append(item)
    my_booking = (db.query(SeatBooking)
                  .filter(SeatBooking.reader_id == reader_id,
                          SeatBooking.booking_date == day,
                          SeatBooking.status.in_((SEAT_BOOKED, SEAT_CHECKED_IN)))
                  .first())
    my_seat = db.get(Seat, my_booking.seat_id) if my_booking is not None else None
    return {"area": area_dict(area), "date": day.isoformat(), "slot": slot,
            "seats": out,
            "my_booking": (booking_dict(my_booking, my_seat, area)
                           if my_booking is not None and my_seat is not None
                           else None),
            "ban": reader_ban(db, reader_id)}


def create_booking(db: Session, *, reader, seat: Seat, day: date,
                   start: time) -> SeatBooking:
    if reader.status != READER_ACTIVE:
        raise ValueError("读者当前状态不能预约座位")
    today = date.today()
    if day < today or day >= today + timedelta(days=BOOKING_DAYS):
        raise ValueError(f"只能预约今天起 {BOOKING_DAYS} 天内的座位")
    area = db.get(SeatArea, seat.area_id)
    if area is None or not area.enabled:
        raise ValueError("座位区域未开放")
    if not seat.enabled:
        raise ValueError("该座位已停用")
    if seat.area_id != area.id:
        raise ValueError("座位与区域不匹配")
    slot = next((item for item in slots_for(area) if item["start"] == _hhmm(start)), None)
    if slot is None:
        raise ValueError("预约时段不合法")
    total = _minutes(start) + SLOT_MINUTES
    end = time(total // 60, total % 60)
    now = _now_local()
    start_dt = datetime.combine(day, start)
    if start_dt + timedelta(minutes=CHECKIN_GRACE_MINUTES) < now:
        raise ValueError("该时段已开始，不能预约")
    ban = reader_ban(db, reader.id)
    if ban["banned"]:
        raise ValueError(f"因连续 {NO_SHOW_LIMIT} 次未签到，预约功能暂停至 {ban['until']}")
    exists = (db.query(SeatBooking)
              .filter(SeatBooking.reader_id == reader.id,
                      SeatBooking.booking_date == day,
                      SeatBooking.status.in_((SEAT_BOOKED, SEAT_CHECKED_IN)))
              .first())
    if exists is not None:
        raise ValueError("每位读者每天只能预约一个座位")
    row = SeatBooking(library_id=area.library_id, seat_id=seat.id,
                      reader_id=reader.id, booking_date=day,
                      start_time=start, end_time=end, status=SEAT_BOOKED)
    db.add(row)
    db.flush()
    return row


class BookingNoShowError(ValueError):
    """业务请求被拒绝，但已记录爽约；API 必须提交此状态而不是 rollback。"""


def _lock_my_booking(db: Session, booking: SeatBooking | None,
                     reader_id: int) -> SeatBooking:
    if booking is None or booking.reader_id != reader_id:
        raise ValueError("预约记录不存在")
    with db.no_autoflush:
        current = (db.query(SeatBooking)
                   .filter(SeatBooking.id == booking.id, SeatBooking.reader_id == reader_id)
                   .populate_existing().with_for_update().one_or_none())
    if current is None:
        raise ValueError("预约记录不存在")
    return current


def _record_no_show(db: Session, booking: SeatBooking, now: datetime) -> None:
    deadline = (datetime.combine(booking.booking_date, booking.start_time)
                + timedelta(minutes=CHECKIN_GRACE_MINUTES))
    if booking.status == SEAT_BOOKED and now > deadline:
        booking.status = SEAT_NO_SHOW
        db.flush()
        raise BookingNoShowError("已超过签到保留时间，预约记为未签到")


def cancel_booking(db: Session, *, booking: SeatBooking | None, reader_id: int) -> SeatBooking:
    booking = _lock_my_booking(db, booking, reader_id)
    _record_no_show(db, booking, _now_local())
    if booking.status not in (SEAT_BOOKED, SEAT_CHECKED_IN):
        raise ValueError("该预约已结束，不能取消")
    booking.status = SEAT_CANCELLED
    booking.cancelled_at = datetime.now(timezone.utc)
    db.flush()
    return booking


def check_in(db: Session, *, booking: SeatBooking | None, reader_id: int) -> SeatBooking:
    booking = _lock_my_booking(db, booking, reader_id)
    now = _now_local()
    _record_no_show(db, booking, now)
    if booking.status != SEAT_BOOKED:
        raise ValueError("当前状态不能签到")
    if booking.booking_date != now.date():
        raise ValueError("只能在预约当天签到")
    start_dt = datetime.combine(booking.booking_date, booking.start_time)
    if now < start_dt - timedelta(minutes=CHECKIN_GRACE_MINUTES):
        raise ValueError("尚未到签到时间")
    booking.status = SEAT_CHECKED_IN
    booking.checked_in_at = datetime.now(timezone.utc)
    db.flush()
    return booking


def my_bookings(db: Session, reader_id: int) -> list[dict]:
    rows = (db.query(SeatBooking)
            .filter(SeatBooking.reader_id == reader_id)
            .order_by(SeatBooking.booking_date.desc(),
                      SeatBooking.start_time.desc())
            .limit(50).all())
    seats = {s.id: s for s in db.query(Seat).filter(
        Seat.id.in_([r.seat_id for r in rows])).all()} if rows else {}
    areas = {a.id: a for a in db.query(SeatArea).filter(
        SeatArea.id.in_([s.area_id for s in seats.values()])).all()} if seats else {}
    out = []
    for row in rows:
        seat = seats.get(row.seat_id)
        area = areas.get(seat.area_id) if seat else None
        out.append(booking_dict(row, seat, area))
    return out
