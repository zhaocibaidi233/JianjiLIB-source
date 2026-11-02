from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer,
    String, Time, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

SEAT_BOOKED = "BOOKED"


SEAT_CHECKED_IN = "CHECKED_IN"


SEAT_COMPLETED = "COMPLETED"


SEAT_CANCELLED = "CANCELLED"


SEAT_NO_SHOW = "NO_SHOW"


SEAT_STATUSES = (SEAT_BOOKED, SEAT_CHECKED_IN, SEAT_COMPLETED, SEAT_CANCELLED, SEAT_NO_SHOW)


SEAT_STATUS_LABELS = {
    SEAT_BOOKED: "已预约",
    SEAT_CHECKED_IN: "已签到",
    SEAT_COMPLETED: "已完成",
    SEAT_CANCELLED: "已取消",
    SEAT_NO_SHOW: "未签到",
}


class SeatArea(Base):
    __tablename__ = "seat_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id",
                   name="fk_seat_areas_library_id_book_libraries"),
        index=True)
    name: Mapped[str] = mapped_column(String(64))
    floor: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(String(256))
    rows: Mapped[int] = mapped_column(Integer, default=6)
    cols: Mapped[int] = mapped_column(Integer, default=8)
    open_time: Mapped[time] = mapped_column(Time)
    close_time: Mapped[time] = mapped_column(Time)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("area_id", "code", name="uq_seats_area_code"),
        UniqueConstraint("area_id", "row", "col", name="uq_seats_area_row_col"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(
        ForeignKey("seat_areas.id", ondelete="CASCADE", name="fk_seats_area_id_seat_areas"),
        index=True)
    code: Mapped[str] = mapped_column(String(16))
    row: Mapped[int] = mapped_column(Integer)
    col: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="normal")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SeatBooking(Base):
    __tablename__ = "seat_bookings"
    __table_args__ = (
        Index("uq_seat_bookings_active_slot", "seat_id", "booking_date",
              "start_time", unique=True,
              postgresql_where=text("status IN ('BOOKED', 'CHECKED_IN')")),
        Index("uq_seat_bookings_reader_day", "reader_id", "booking_date",
              unique=True,
              postgresql_where=text("status IN ('BOOKED', 'CHECKED_IN')")),
        Index("ix_seat_bookings_library_date", "library_id", "booking_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id",
                   name="fk_seat_bookings_library_id_book_libraries"),
        index=True)
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id", ondelete="CASCADE", name="fk_seat_bookings_seat_id_seats"),
        index=True)
    reader_id: Mapped[int] = mapped_column(
        ForeignKey("readers.id", name="fk_seat_bookings_reader_id_readers"),
        index=True)
    booking_date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    status: Mapped[str] = mapped_column(String(16), default=SEAT_BOOKED, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
