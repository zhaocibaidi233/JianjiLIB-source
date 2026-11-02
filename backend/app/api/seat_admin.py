# -*- coding: utf-8 -*-
"""座位区域管理（馆员端，归「全局设置」模块）。

读者端 `/api/portal/seats` 只读区域与座位；本文件负责配置：区域名称、楼层、
开放时间、行列与启停。三条约束写在这里而不是交给前端：

1. **行列变更会重建座位**，因此只要该区域产生过任何预约就拒绝调整 —— 重建座位
   会级联删掉 `seat_bookings` 的历史行，台账不能这样消失。
2. **删除区域同样要求零预约记录**；想停用就关 `enabled`，历史仍在。
3. 开放时长至少 2 小时（== 一个预约时段），否则 `slots_for()` 会返回空列表，
   读者端看起来像“区域坏了”。
"""
from __future__ import annotations

from datetime import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (SEAT_BOOKED, SEAT_CHECKED_IN, BookLibrary, Seat,
                      SeatArea, SeatBooking)
from ..services import seats as seat_service
from ..core.route_meta import api

router = APIRouter(prefix="/settings/seats", tags=["seat-admin"])

ACTIVE_STATUSES = (SEAT_BOOKED, SEAT_CHECKED_IN)
MAX_SIDE = 20


class SeatAreaBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    floor: str | None = Field(None, max_length=32)
    description: str | None = Field(None, max_length=256)
    rows: int = Field(6, ge=1, le=MAX_SIDE)
    cols: int = Field(8, ge=1, le=MAX_SIDE)
    open_time: str = "08:00"
    close_time: str = "22:00"
    enabled: bool = True


class SeatAreaIn(SeatAreaBase):
    library_id: int


def _parse_time(raw: str, label: str) -> time:
    try:
        hour_text, minute_text = (raw or "").strip().split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        raise HTTPException(400, f"{label}格式应为 HH:MM")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise HTTPException(400, f"{label}不是合法时间")
    return time(hour, minute)


def _normalized(payload: SeatAreaBase) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "区域名称不能为空")
    open_time = _parse_time(payload.open_time, "开放时间")
    close_time = _parse_time(payload.close_time, "闭馆时间")
    span = ((close_time.hour * 60 + close_time.minute)
            - (open_time.hour * 60 + open_time.minute))
    if span < seat_service.SLOT_MINUTES:
        raise HTTPException(
            400, f"开放时长至少 {seat_service.SLOT_MINUTES // 60} 小时，否则无法排出预约时段")
    return {
        "name": name,
        "floor": (payload.floor or "").strip() or None,
        "description": (payload.description or "").strip() or None,
        "rows": payload.rows,
        "cols": payload.cols,
        "open_time": open_time,
        "close_time": close_time,
        "enabled": bool(payload.enabled),
    }


def _ensure_unique_name(db: Session, library_id: int, name: str,
                        exclude_id: int | None = None) -> None:
    query = db.query(SeatArea).filter(SeatArea.library_id == library_id,
                                      SeatArea.name == name)
    if exclude_id is not None:
        query = query.filter(SeatArea.id != exclude_id)
    if query.first() is not None:
        raise HTTPException(409, "同一图书库下已有同名座位区域")


def _booking_count(db: Session, area_id: int) -> int:
    return int(db.query(func.count(SeatBooking.id))
               .join(Seat, SeatBooking.seat_id == Seat.id)
               .filter(Seat.area_id == area_id).scalar() or 0)


def _active_booking_count(db: Session, area_id: int) -> int:
    return int(db.query(func.count(SeatBooking.id))
               .join(Seat, SeatBooking.seat_id == Seat.id)
               .filter(Seat.area_id == area_id,
                       SeatBooking.status.in_(ACTIVE_STATUSES)).scalar() or 0)


def _area_row(db: Session, area: SeatArea, library_name: str | None = None) -> dict:
    seat_count = int(db.query(func.count(Seat.id))
                     .filter(Seat.area_id == area.id).scalar() or 0)
    if library_name is None:
        library = db.get(BookLibrary, area.library_id)
        library_name = library.name if library is not None else None
    return {
        "id": area.id,
        "library_id": area.library_id,
        "library_name": library_name,
        "name": area.name,
        "floor": area.floor,
        "description": area.description,
        "rows": area.rows,
        "cols": area.cols,
        "open_time": seat_service._hhmm(area.open_time),
        "close_time": seat_service._hhmm(area.close_time),
        "enabled": bool(area.enabled),
        "seat_count": seat_count,
        "booking_count": _booking_count(db, area.id),
        "active_booking_count": _active_booking_count(db, area.id),
    }


def _create_seats(db: Session, area: SeatArea) -> None:
    for row in range(area.rows):
        for col in range(area.cols):
            db.add(Seat(area_id=area.id, code=f"{chr(65 + row)}-{col + 1:02d}",
                        row=row, col=col, kind="normal", enabled=True))
    db.flush()


@router.get("/areas")
@api(module="settings")
def list_seat_areas(library_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(SeatArea).order_by(SeatArea.library_id, SeatArea.id)
    if library_id:
        query = query.filter(SeatArea.library_id == library_id)
    names = {lib.id: lib.name for lib in db.query(BookLibrary).all()}
    return {"items": [_area_row(db, area, names.get(area.library_id))
                      for area in query.all()]}


@router.post("/areas")
@api(module="settings")
def create_seat_area(payload: SeatAreaIn, db: Session = Depends(get_db)):
    library = db.get(BookLibrary, payload.library_id)
    if library is None:
        raise HTTPException(404, "图书库不存在")
    data = _normalized(payload)
    _ensure_unique_name(db, payload.library_id, data["name"])
    area = SeatArea(library_id=library.id, **data)
    db.add(area)
    db.flush()
    _create_seats(db, area)
    db.commit()
    db.refresh(area)
    return {"area": _area_row(db, area, library.name), "message": "座位区域已创建"}


@router.put("/areas/{area_id}")
@api(module="settings")
def update_seat_area(area_id: int, payload: SeatAreaBase,
                     db: Session = Depends(get_db)):
    area = db.get(SeatArea, area_id)
    if area is None:
        raise HTTPException(404, "座位区域不存在")
    data = _normalized(payload)
    _ensure_unique_name(db, area.library_id, data["name"], exclude_id=area.id)

    resized = data["rows"] != area.rows or data["cols"] != area.cols
    if resized and _booking_count(db, area.id):
        raise HTTPException(400, "该区域已有预约记录，不能调整行列；可新建区域或停用后另行配置")

    for key, value in data.items():
        setattr(area, key, value)
    if resized:
        db.query(Seat).filter(Seat.area_id == area.id).delete(synchronize_session=False)
        db.flush()
        _create_seats(db, area)
    db.commit()
    db.refresh(area)
    return {"area": _area_row(db, area), "message": "座位区域已保存"}


@router.delete("/areas/{area_id}")
@api(module="settings")
def delete_seat_area(area_id: int, db: Session = Depends(get_db)):
    area = db.get(SeatArea, area_id)
    if area is None:
        raise HTTPException(404, "座位区域不存在")
    if _booking_count(db, area.id):
        raise HTTPException(400, "该区域已有预约记录，不能删除；可将其停用")
    db.delete(area)
    db.commit()
    return {"deleted": area_id, "message": "座位区域已删除"}
