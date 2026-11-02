# -*- coding: utf-8 -*-
"""图书预约队列。

预约对象是**书目**：同一书目下任意一册归还即可把队首转为待取。READY 不锁定
具体条码，读者取书时由馆员扫任意在架副本外借——锁定条码会让「书在架上但
预约没来取」的册无法被其他读者借阅，与图书馆实际做法不符。

本模块只做业务判定与 flush，**不 commit**；提交与回滚由 API 层统一掌控。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..models import (LOAN_ACTIVE, READER_ACTIVE, RESERVATION_ACTIVE_STATUSES,
                      RESERVATION_CANCELLED, RESERVATION_EXPIRED,
                      RESERVATION_FULFILLED, RESERVATION_PENDING,
                      RESERVATION_READY, RESERVATION_STATUS_LABELS, HoldingORM,
                      LoanORM, ReaderORM, RecordORM, ReservationORM)
from .circulation import _lock_reader, lock_record, rules_value


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def available_copies(db: Session, record_id: int) -> int:
    """当前可借册数：在架馆藏减去仍外借的册。"""
    holding_ids = [hid for (hid,) in
                   db.query(HoldingORM.id)
                   .filter(HoldingORM.record_id == record_id,
                           HoldingORM.status == "AVAILABLE").all()]
    if not holding_ids:
        return 0
    on_loan = (db.query(func.count(LoanORM.id))
               .filter(LoanORM.holding_id.in_(holding_ids),
                       LoanORM.status == LOAN_ACTIVE)
               .scalar() or 0)
    return max(0, len(holding_ids) - int(on_loan))


def _hold_days(db: Session) -> int:
    return int(rules_value(db).get("reservation_hold_days") or 3)


def ready_deadline(db: Session, since: datetime | None = None) -> datetime:
    return (since or now_utc()) + timedelta(days=_hold_days(db))


class DuplicateReservationError(ValueError):
    pass


def create_reservation(db: Session, *, reader: ReaderORM, record: RecordORM,
                       note: str | None = None, operator: str | None = None) -> ReservationORM:
    """登记一条预约。不满足预约条件时抛 `ValueError`。**不 commit**。"""
    reader = _lock_reader(db, reader.id)
    if reader is None:
        raise ValueError("读者不存在")
    lock_record(db, record.id)
    if reader.library_id != record.library_id:
        raise ValueError("读者与书目不属于同一个图书库")
    if reader.status != READER_ACTIVE:
        raise ValueError("读者当前状态不能预约")
    existing = (db.query(ReservationORM)
                .filter(ReservationORM.reader_id == reader.id,
                        ReservationORM.record_id == record.id,
                        ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
                .first())
    if existing is not None:
        raise DuplicateReservationError("该读者已预约此书，请勿重复提交")
    if available_copies(db, record.id) > 0:
        raise ValueError("该书当前有可借馆藏，请直接借阅，无需预约")
    row = ReservationORM(
        library_id=record.library_id, record_id=record.id, reader_id=reader.id,
        status=RESERVATION_PENDING, note=(note or "").strip()[:256] or None,
        created_by=operator, updated_by=operator)
    db.add(row)
    db.flush()
    return row


def _lock_reservation(db: Session, row: ReservationORM) -> ReservationORM:
    row_id, record_id = row.id, row.record_id
    lock_record(db, record_id)
    with db.no_autoflush:
        current = (db.query(ReservationORM).filter(ReservationORM.id == row_id)
                   .populate_existing().with_for_update().one_or_none())
    if current is None or current.record_id != record_id:
        raise ValueError("预约记录不存在或已变更")
    return current


def _copy_count(db: Session, record_id: int) -> int:
    """在册副本数（含外借中的册，按 holdings.status 口径）。

    手工「标记到书」面对的是「书已回到馆员手里、但归还还没入账」的场景——
    PENDING 本身就意味着在架可借为 0，若按 `available_copies` 判，任何手工
    到书都会被拒。这里退一步按在册册数封顶，保证不会把整队 PENDING 全标 READY。
    """
    return int(db.query(func.count(HoldingORM.id))
               .filter(HoldingORM.record_id == record_id,
                       HoldingORM.status == "AVAILABLE")
               .scalar() or 0)


def mark_ready(db: Session, *, row: ReservationORM, operator: str | None = None) -> ReservationORM:
    row = _lock_reservation(db, row)
    if row.status != RESERVATION_PENDING:
        raise ValueError("只有排队中的预约可以置为待取")
    # 手工「标记到书」不是免检通道：先按 FIFO 确认没人排在前面，
    # 再按「在册副本 - 已 READY」封顶，不能把整队 PENDING 全标到书。
    db.flush()
    earlier = (db.query(ReservationORM.id)
               .filter(ReservationORM.record_id == row.record_id,
                       ReservationORM.status == RESERVATION_PENDING,
                       or_(ReservationORM.reserved_at < row.reserved_at,
                           and_(ReservationORM.reserved_at == row.reserved_at,
                                ReservationORM.id < row.id)))
               .first())
    if earlier is not None:
        raise ValueError("前面还有排队中的预约，请按顺序标记到书")
    active_ready = (db.query(func.count(ReservationORM.id))
                    .filter(ReservationORM.record_id == row.record_id,
                            ReservationORM.status == RESERVATION_READY)
                    .scalar() or 0)
    if _copy_count(db, row.record_id) - int(active_ready) <= 0:
        raise ValueError("当前没有可分配的册，不能标记到书")
    now = now_utc()
    row.status = RESERVATION_READY
    row.ready_at = now
    row.expires_at = ready_deadline(db, now)
    row.updated_by = operator
    db.flush()
    return row


def cancel(db: Session, *, row: ReservationORM, reason: str,
           operator: str | None = None) -> ReservationORM:
    row = _lock_reservation(db, row)
    if row.status not in RESERVATION_ACTIVE_STATUSES:
        raise ValueError("该预约已结束，不能取消")
    text = (reason or "").strip()
    if not text:
        raise ValueError("请填写取消原因")
    row.status = RESERVATION_CANCELLED
    row.cancelled_at = now_utc()
    row.cancel_reason = text[:128]
    row.updated_by = operator
    db.flush()
    # 判断/释放/顺延均在同一书目锁中，调用方不必使用可能过时的 was_ready。
    promote_ready(db, row.record_id, operator=operator)
    return row


def fulfill(db: Session, *, row: ReservationORM, operator: str | None = None) -> ReservationORM:
    row = _lock_reservation(db, row)
    if row.status not in RESERVATION_ACTIVE_STATUSES:
        raise ValueError("该预约已结束，不能标记为已外借")
    row.status = RESERVATION_FULFILLED
    row.fulfilled_at = now_utc()
    row.updated_by = operator
    db.flush()
    return row


def fulfill_for_borrow(db: Session, *, reader_id: int, record_id: int,
                       operator: str | None = None) -> ReservationORM | None:
    """外借成功后结清同读者、同图书记录的预约。"""
    lock_record(db, record_id)
    row = (db.query(ReservationORM)
           .filter(ReservationORM.reader_id == reader_id,
                   ReservationORM.record_id == record_id,
                   ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
           .order_by(ReservationORM.id)
           .populate_existing().with_for_update().first())
    if row is None:
        return None
    now = now_utc()
    if row.status == RESERVATION_READY:
        row.status = RESERVATION_FULFILLED
        row.fulfilled_at = now
    else:
        # 读者没等排队就直接拿到了这本书，队列中的占位应立即释放。
        row.status = RESERVATION_CANCELLED
        row.cancelled_at = now
        row.cancel_reason = "读者已直接外借"
    row.updated_by = operator
    db.flush()
    return row


def promote_ready(db: Session, record_id: int, *,
                  operator: str | None = None) -> list[ReservationORM]:
    """在可借册数允许的范围内，把队首若干条 PENDING 转为 READY。**不 commit**。

    READY 数不会超过当前可借册数：三册在架就最多三个读者收到到书通知，
    不会出现「排在后面的人先收到、前面的人还在等」。
    """
    # 先锁书目再 flush/计算：另一册归还必须等待本次提交，随后能看见全量可借册。
    lock_record(db, record_id)
    db.flush()
    available = available_copies(db, record_id)
    if available <= 0:
        return []
    active_ready = (db.query(func.count(ReservationORM.id))
                    .filter(ReservationORM.record_id == record_id,
                            ReservationORM.status == RESERVATION_READY)
                    .scalar() or 0)
    slots = available - int(active_ready)
    if slots <= 0:
        return []
    pending = (db.query(ReservationORM)
               .filter(ReservationORM.record_id == record_id,
                       ReservationORM.status == RESERVATION_PENDING)
               .order_by(ReservationORM.reserved_at.asc(), ReservationORM.id.asc())
               .limit(slots)
               .populate_existing().with_for_update()
               .all())
    now = now_utc()
    expires = ready_deadline(db, now)
    for row in pending:
        row.status = RESERVATION_READY
        row.ready_at = now
        row.expires_at = expires
        row.updated_by = operator
    if pending:
        db.flush()
    return pending


def expire_ready(db: Session, *, library_id: int | None = None,
                 record_id: int | None = None, operator: str = "system") -> int:
    """过期和顺延遵循书目锁协议；已持有书目锁的归还入口只传本书 id。"""
    now = now_utc()
    query = (db.query(ReservationORM)
             .filter(ReservationORM.status == RESERVATION_READY,
                     ReservationORM.expires_at.isnot(None),
                     ReservationORM.expires_at < now))
    if library_id is not None:
        query = query.filter(ReservationORM.library_id == library_id)
    if record_id is not None:
        query = query.filter(ReservationORM.record_id == record_id)
    # 初查只发现候选书目，不锁预约。等待书目锁后必须重新查询到期状态。
    record_ids = sorted({rid for (rid,) in
                         query.with_entities(ReservationORM.record_id).distinct().all()})
    changed = 0
    for rid in record_ids:
        lock_record(db, rid)
        rows = (query.filter(ReservationORM.record_id == rid)
                .populate_existing().with_for_update().all())
        for row in rows:
            row.status = RESERVATION_EXPIRED
            row.updated_by = operator
        changed += len(rows)
        db.flush()
        promote_ready(db, rid, operator=operator)
    return changed


def cancel_active_for_reader(db: Session, reader_id: int, reason: str,
                             operator: str | None = None) -> list[int]:
    return cancel_active_for_readers(db, [reader_id], reason, operator=operator)


def cancel_active_for_readers(db: Session, reader_ids: list[int], reason: str,
                              operator: str | None = None) -> list[int]:
    """先落读者状态，再按书目升序取消并顺延，全部处于调用方的同一事务。"""
    db.flush()
    query = db.query(ReservationORM).filter(
        ReservationORM.reader_id.in_(reader_ids),
        ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
    record_ids = sorted({rid for (rid,) in
                         query.with_entities(ReservationORM.record_id).distinct().all()})
    now = now_utc()
    for record_id in record_ids:
        lock_record(db, record_id)
        rows = (query.filter(ReservationORM.record_id == record_id)
                .populate_existing().with_for_update().all())
        for row in rows:
            row.status = RESERVATION_CANCELLED
            row.cancelled_at = now
            row.cancel_reason = reason[:128]
            row.updated_by = operator
        db.flush()
        promote_ready(db, record_id, operator=operator)
    return record_ids


def cancel_active_for_record(db: Session, record_id: int, reason: str,
                             operator: str | None = None,
                             reassign_record_id: int | None = None) -> int:
    lock_record(db, record_id)
    rows = (db.query(ReservationORM)
            .filter(ReservationORM.record_id == record_id,
                    ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
            .populate_existing().with_for_update().all())
    now = now_utc()
    for row in rows:
        row.status = RESERVATION_CANCELLED
        row.cancelled_at = now
        row.cancel_reason = reason[:128]
        row.updated_by = operator
        if reassign_record_id is not None:
            # 源记录马上要删，预约的 record_id 是 ondelete=CASCADE：直接删除
            # 会把「谁被取消、为什么」一并抹掉。改挂到接手的记录上，保留审计行。
            row.record_id = reassign_record_id
    if rows:
        db.flush()
    return len(rows)


def cancel_if_unavailable(db: Session, record_ids, reason: str,
                          operator: str | None = None) -> int:
    """馆藏变动后，取消已无在架副本的书目上的进行中预约。**不 commit**。"""
    cancelled = 0
    for record_id in sorted(set(record_ids)):
        lock_record(db, record_id)
        remain = (db.query(HoldingORM.id)
                  .filter(HoldingORM.record_id == record_id,
                          HoldingORM.status == "AVAILABLE")
                  .first())
        if remain is None:
            cancelled += cancel_active_for_record(
                db, record_id, reason, operator=operator)
    return cancelled


def call_no_map(db: Session, record_ids: list[int]) -> dict[int, str]:
    if not record_ids:
        return {}
    rows = (db.query(HoldingORM.record_id, HoldingORM.call_no)
            .filter(HoldingORM.record_id.in_(record_ids),
                    HoldingORM.call_no.isnot(None))
            .order_by(HoldingORM.id).all())
    out: dict[int, str] = {}
    for record_id, call_no in rows:
        if call_no:
            out.setdefault(record_id, call_no)
    return out


def queue_ranks(db: Session, record_ids: list[int]) -> dict[int, int]:
    """当前进行中预约在各自书目队列里的顺位（1 起）。"""
    if not record_ids:
        return {}
    rows = (db.query(ReservationORM.id, ReservationORM.record_id)
            .filter(ReservationORM.record_id.in_(record_ids),
                    ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
            .order_by(ReservationORM.reserved_at.asc(), ReservationORM.id.asc())
            .all())
    ranks: dict[int, int] = {}
    counters: dict[int, int] = {}
    for row_id, record_id in rows:
        counters[record_id] = counters.get(record_id, 0) + 1
        ranks[row_id] = counters[record_id]
    return ranks


def serialize_many(db: Session, rows: list[ReservationORM]) -> list[dict]:
    if not rows:
        return []
    record_ids = sorted({row.record_id for row in rows})
    reader_ids = sorted({row.reader_id for row in rows})
    records = {r.id: r for r in
               db.query(RecordORM).filter(RecordORM.id.in_(record_ids)).all()}
    readers = {r.id: r for r in
               db.query(ReaderORM).filter(ReaderORM.id.in_(reader_ids)).all()}
    calls = call_no_map(db, record_ids)
    ranks = queue_ranks(db, record_ids)
    now = now_utc()

    holdings = (db.query(HoldingORM.id, HoldingORM.record_id)
                .filter(HoldingORM.record_id.in_(record_ids),
                        HoldingORM.status == "AVAILABLE").all())
    holding_record = {hid: rid for hid, rid in holdings}
    active_holding_ids: set[int] = set()
    if holding_record:
        active_holding_ids = {hid for (hid,) in
                              db.query(LoanORM.holding_id)
                              .filter(LoanORM.holding_id.in_(holding_record.keys()),
                                      LoanORM.status == LOAN_ACTIVE).all()}
    available: dict[int, int] = {}
    for hid, rid in holdings:
        if hid not in active_holding_ids:
            available[rid] = available.get(rid, 0) + 1

    out = []
    for row in rows:
        rec = records.get(row.record_id)
        reader = readers.get(row.reader_id)
        seconds_left = None
        if row.status == RESERVATION_READY and row.expires_at is not None:
            seconds_left = max(0, int((row.expires_at - now).total_seconds()))
        out.append({
            "id": row.id,
            "library_id": row.library_id,
            "record_id": row.record_id,
            "reader_id": row.reader_id,
            "status": row.status,
            "status_label": RESERVATION_STATUS_LABELS.get(row.status, row.status),
            "reserved_at": row.reserved_at.isoformat() if row.reserved_at else None,
            "ready_at": row.ready_at.isoformat() if row.ready_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "fulfilled_at": row.fulfilled_at.isoformat() if row.fulfilled_at else None,
            "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
            "cancel_reason": row.cancel_reason,
            "note": row.note,
            "seconds_left": seconds_left,
            "queue_position": ranks.get(row.id),
            "available_count": available.get(row.record_id, 0),
            "title": rec.title_norm if rec else None,
            "author": rec.author_norm if rec else None,
            "class_no": rec.class_no if rec else None,
            "isbn": rec.isbn_norm if rec else None,
            "call_no": calls.get(row.record_id),
            "reader": {
                "id": reader.id if reader else row.reader_id,
                "card_no": reader.card_no if reader else None,
                "name": reader.name if reader else None,
                "reader_type": reader.reader_type if reader else None,
                "class_name": reader.class_name if reader else None,
                "phone": reader.phone if reader else None,
                "status": reader.status if reader else None,
            },
        })
    return out
