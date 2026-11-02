# -*- coding: utf-8 -*-
"""借还台：外借 / 归还 / 续借 / 借阅清单 / 逾期 / 统计 / 规则。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..db import get_db, like_escape
from ..models import (FINE_PAID, FINE_UNPAID, FINE_WAIVED, LOAN_ACTIVE,
                      LOAN_LOST, LOAN_RETURNED, RESERVATION_ACTIVE_STATUSES,
                      HoldingORM, LoanORM, ReaderORM, RecordORM,
                      ReservationORM)
from ..services.circulation import (DEFAULT_RULES, FINE_STATUS_LABELS,
                                    MAX_CLOSED_DATES,
                                    active_loan_for_holding, active_loans_of_reader,
                                    borrow, compute_fine,
                                    fines_enabled, loan_dict,
                                    mark_lost, overdue_count, reader_dict,
                                    reader_compensation_summary,
                                    reader_fine_summary, renew_loan,
                                    return_loan, rules_value, save_rules,
                                    settle_compensation, settle_fine,
                                    type_rule)
from ..services.sorting import apply_sort
from ..services.paging import paginate
from ..services.trend import loan_day_trend
from ..services import reservations as reservation_service
from .book_libraries import resolve_library
from .common import xlsx_response

router = APIRouter(prefix="/circulation", tags=["circulation"])

LOAN_SORTS = {
    "barcode": LoanORM.barcode,
    "title": LoanORM.title,
    "reader_card_no": LoanORM.reader_card_no,
    "reader_name": LoanORM.reader_name,
    "reader_class": LoanORM.reader_class,
    "loaned_at": LoanORM.loaned_at,
    "due_on": LoanORM.due_on,
    "returned_at": LoanORM.returned_at,
    "fine_amount": LoanORM.fine_amount,
    "compensation_amount": LoanORM.compensation_amount,
    "id": LoanORM.id,
}


class BorrowIn(BaseModel):
    library_id: int | None = None
    reader_id: int | None = None
    card_no: str | None = None
    barcode: str = Field(min_length=1, max_length=32)


class ReturnIn(BaseModel):
    library_id: int | None = None
    barcode: str = Field(min_length=1, max_length=32)


class RenewIn(BaseModel):
    loan_id: int


class TypeRuleIn(BaseModel):
    label: str | None = Field(None, max_length=16)
    loan_days: int = Field(30, ge=1, le=365)
    max_items: int = Field(2, ge=1, le=100)
    max_renew: int = Field(1, ge=0, le=10)
    renew_days: int = Field(15, ge=1, le=365)


class FinRulesIn(BaseModel):
    enabled: bool = False
    per_day: float = Field(0.10, ge=0, le=1000)
    grace_days: int = Field(0, ge=0, le=365)
    max_amount: float = Field(0.0, ge=0, le=100000)
    # 未缴罚款/赔偿停借。**必须出现在这里**：服务端 `services/circulation.py` 一直在读
    # 这个键，但请求体模型没有它时 Pydantic 会静默丢弃 —— 界面上勾了、GET 回来是 false，
    # 而 PUT 还会用默认值把库里手工设过的 true 覆盖回去，功能等于不存在
    # 。
    block_on_unpaid: bool = False


class RulesIn(BaseModel):
    types: dict[str, TypeRuleIn] = {}
    skip_weekends: bool = True
    closed_dates: list[str] = []
    block_on_overdue: bool = True
    reservation_hold_days: int = Field(3, ge=1, le=30)
    fines: FinRulesIn = FinRulesIn()


class FineSettleIn(BaseModel):
    status: str


class LostIn(BaseModel):
    amount: float = Field(0, ge=0, le=1000000)
    paid: bool = False
    note: str | None = Field(None, max_length=256)


class CompensationSettleIn(BaseModel):
    status: str


# 本地日界下沉到 core/dates.py（services 层共用），这里留别名保持既有引用
from ..core.dates import local_day_start as _local_day_start
from ..core.route_meta import api
from ..schemas import LoanPage


def _reader_or_404(db: Session, reader_id: int) -> ReaderORM:
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    return r


def _find_reader_by_card(db: Session, card_no: str, library_id: int | None) -> ReaderORM:
    q = db.query(ReaderORM).filter(ReaderORM.card_no == card_no.strip())
    if library_id:
        q = q.filter(ReaderORM.library_id == library_id)
        r = q.first()
        if r is not None:
            return r
        if db.query(ReaderORM).filter(
                ReaderORM.card_no == card_no.strip()).first() is not None:
            raise HTTPException(400, "该读者属于其他图书库，不能跨库借阅")
        raise HTTPException(404, f"本库没有借书证号「{card_no}」")
    rows = q.limit(2).all()
    if not rows:
        raise HTTPException(404, f"借书证号不存在：{card_no}")
    if len(rows) > 1:
        raise HTTPException(400, "该借书证号在多个图书库中存在，请先选择图书库")
    return rows[0]


def _find_holding_by_barcode(db: Session, barcode: str,
                             library_id: int | None) -> HoldingORM:
    q = db.query(HoldingORM).filter(HoldingORM.barcode == barcode.strip())
    if library_id:
        q = q.filter(HoldingORM.library_id == library_id)
        h = q.first()
        if h is None:
            other = (db.query(HoldingORM)
                     .filter(HoldingORM.barcode == barcode.strip()).first())
            if other is not None:
                raise HTTPException(400, "该条码属于其他图书库，不能跨库借还")
            raise HTTPException(404, f"条码不存在：{barcode}")
        return h
    rows = q.limit(2).all()
    if not rows:
        raise HTTPException(404, f"条码不存在：{barcode}")
    if len(rows) > 1:
        raise HTTPException(400, "该条码在多个图书库中存在，请先选择图书库")
    return rows[0]


def _reader_panel(db: Session, reader: ReaderORM, rules: dict) -> dict:
    active = active_loans_of_reader(db, reader.id)
    rule = type_rule(rules, reader.reader_type)
    return {
        **reader_dict(reader),
        "rules": {"max_items": rule["max_items"],
                  "loan_days": rule["loan_days"],
                  "max_renew": rule["max_renew"]},
        "active_count": len(active),
        "overdue_count": overdue_count(db, reader.id),
        "active_loans": [loan_dict(x) for x in active],
        "fines": reader_fine_summary(db, reader.id, rules),
        "compensations": reader_compensation_summary(db, reader.id),
    }


def _holding_panel(db: Session, holding: HoldingORM) -> dict:
    rec = db.get(RecordORM, holding.record_id)
    loan = active_loan_for_holding(db, holding.id)
    return {
        "holding_id": holding.id, "record_id": holding.record_id,
        "library_id": holding.library_id, "barcode": holding.barcode,
        "call_no": holding.call_no, "location": holding.location,
        "shelf_no": holding.shelf_no,
        "status": holding.status,
        "withdraw_no": holding.withdraw_no,
        "withdraw_reason": holding.withdraw_reason,
        "withdrawn_at": str(holding.withdrawn_at) if holding.withdrawn_at else None,
        "title": rec.title_norm if rec else None,
        "isbn": rec.isbn_norm if rec else None,
        "on_loan": loan is not None,
        "loan": loan_dict(loan) if loan is not None else None,
    }


@router.get("/reader")
@api(module="circulation")
def lookup_reader(card_no: str, library_id: int | None = None,
                  db: Session = Depends(get_db)):
    rules = rules_value(db)
    reader = _find_reader_by_card(db, card_no, library_id)
    return _reader_panel(db, reader, rules)


@router.get("/item")
@api(module="circulation")
def lookup_item(barcode: str, library_id: int | None = None,
                db: Session = Depends(get_db)):
    holding = _find_holding_by_barcode(db, barcode, library_id)
    return _holding_panel(db, holding)


@router.post("/borrow")
@api(module="circulation")
def borrow_api(payload: BorrowIn, request: Request, db: Session = Depends(get_db)):
    rules = rules_value(db)
    if payload.reader_id is not None:
        reader = _reader_or_404(db, payload.reader_id)
    elif payload.card_no and payload.card_no.strip():
        reader = _find_reader_by_card(db, payload.card_no, payload.library_id)
    else:
        raise HTTPException(400, "请提供 reader_id 或借书证号")

    lib_id = reader.library_id
    if payload.library_id is not None:
        lib = resolve_library(db, payload.library_id)
        if lib.id != reader.library_id:
            raise HTTPException(400, "读者不属于当前图书库，不能跨库借阅")
        lib_id = lib.id

    holding = _find_holding_by_barcode(db, payload.barcode, lib_id)
    rec = db.get(RecordORM, holding.record_id)
    try:
        loan = borrow(db, reader=reader, holding=holding,
                      title=rec.title_norm if rec else None,
                      rules=rules, operator=operator_of(request))
        reservation_service.fulfill_for_borrow(
            db, reader_id=reader.id, record_id=holding.record_id,
            operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    except IntegrityError:
        # 部分唯一索引兜底：两台终端同时扫同一本
        db.rollback()
        raise HTTPException(409, "该册已外借，请刷新后重试")
    db.refresh(loan)
    # 预约提示：READY 预约不锁具体条码（见 services/reservations.py 模块注释），
    # 别的读者随时可能把剩余可借册借走——借出时把「还有多少人在等」摆到界面上，
    # 馆员可以顺手提醒。
    waiting = (db.query(func.count(ReservationORM.id))
               .filter(ReservationORM.record_id == holding.record_id,
                       ReservationORM.reader_id != reader.id,
                       ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
               .scalar() or 0)
    msg = (f"外借成功：{reader.name}《{loan.title or holding.barcode}》，"
           f"应还 {loan.due_on.isoformat()}")
    if waiting > 0:
        msg += f"。注意：本书还有 {waiting} 人预约排队中"
    return {"loan": loan_dict(loan),
            "reader": _reader_panel(db, reader, rules),
            "item": _holding_panel(db, holding),
            "reservations_waiting": int(waiting),
            "message": msg}


@router.post("/return")
@api(module="circulation")
def return_api(payload: ReturnIn, request: Request, db: Session = Depends(get_db)):
    holding = _find_holding_by_barcode(db, payload.barcode, payload.library_id)
    rules = rules_value(db)
    try:
        loan, was_overdue = return_loan(db, holding=holding, rules=rules,
                                        operator=operator_of(request))
        # 已持有本书目锁，不能再跨书目懒过期（逆序锁可能导致并发归还死锁）。
        reservation_service.expire_ready(db, record_id=holding.record_id)
        promoted = reservation_service.promote_ready(
            db, holding.record_id, operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(loan)
    msg = f"归还成功：《{loan.title or loan.barcode or ''}》（{loan.reader_name or ''}）"
    if was_overdue:
        msg += f"，已逾期 {(date.today() - loan.due_on).days} 天"
    if loan.fine_amount and loan.fine_amount > 0:
        msg += f"，产生逾期罚款 ¥{loan.fine_amount:.2f}（未缴）"
    if promoted:
        msg += f"，预约队列已顺延 {len(promoted)} 位"
    return {"loan": loan_dict(loan), "item": _holding_panel(db, holding),
            "overdue": was_overdue, "message": msg,
            "reservations_promoted": len(promoted)}


@router.post("/renew")
@api(module="circulation")
def renew_api(payload: RenewIn, request: Request, db: Session = Depends(get_db)):
    loan = db.get(LoanORM, payload.loan_id)
    if loan is None:
        raise HTTPException(404, "借阅记录不存在")
    try:
        renew_loan(db, loan=loan, rules=rules_value(db),
                   operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(loan)
    return {"loan": loan_dict(loan),
            "message": f"续借成功，应还 {loan.due_on.isoformat()}"}


@router.post("/loans/{loan_id}/lost")
@api(module="circulation")
def mark_lost_api(loan_id: int, payload: LostIn, request: Request,
                  db: Session = Depends(get_db)):
    """按遗失处理：结束借阅、馆藏转遗失、登记赔偿（金额可为 0）。"""
    loan = db.get(LoanORM, loan_id)
    if loan is None:
        raise HTTPException(404, "借阅记录不存在")
    holding = db.get(HoldingORM, loan.holding_id)
    try:
        mark_lost(db, loan=loan, holding=holding, amount=payload.amount,
                  paid=payload.paid, note=payload.note,
                  operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(loan)
    msg = f"已按遗失处理：《{loan.title or loan.barcode or ''}》（{loan.reader_name or ''}）"
    if loan.compensation_amount and loan.compensation_amount > 0:
        label = FINE_STATUS_LABELS.get(loan.compensation_status or "", "")
        msg += f"，赔偿 ¥{loan.compensation_amount:.2f}（{label}）"
    else:
        msg += "，未登记赔偿"
    reader = db.get(ReaderORM, loan.reader_id)
    rules = rules_value(db)
    return {
        "loan": loan_dict(loan),
        "item": _holding_panel(db, holding) if holding is not None else None,
        "reader": _reader_panel(db, reader, rules) if reader is not None else None,
        "message": msg,
    }


@router.post("/loans/{loan_id}/compensation/settle")
@api(module="circulation")
def settle_compensation_api(loan_id: int, payload: CompensationSettleIn,
                            request: Request, db: Session = Depends(get_db)):
    loan = db.get(LoanORM, loan_id)
    try:
        settle_compensation(db, loan=loan, status=payload.status,
                            operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(loan)
    label = FINE_STATUS_LABELS.get(loan.compensation_status or "",
                                   loan.compensation_status)
    return {"loan": loan_dict(loan), "message": f"赔偿状态已改为「{label}」"}


@router.get("/loans", response_model=LoanPage)
@api(module="circulation")
def list_loans(
    library_id: int | None = None,
    status: str = Query("active",
                        description="active | overdue | returned | lost | all"),
    q: str | None = None,
    reader_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str | None = None, order: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(LoanORM)
    if library_id:
        query = query.filter(LoanORM.library_id == library_id)
    if reader_id:
        query = query.filter(LoanORM.reader_id == reader_id)
    if status == "active":
        query = query.filter(LoanORM.status == LOAN_ACTIVE)
    elif status == "overdue":
        query = query.filter(LoanORM.status == LOAN_ACTIVE,
                             LoanORM.due_on < date.today())
    elif status == "returned":
        query = query.filter(LoanORM.status == LOAN_RETURNED)
    elif status == "lost":
        query = query.filter(LoanORM.status == LOAN_LOST)
    elif status != "all":
        raise HTTPException(
            400, "status 只能是 active / overdue / returned / lost / all")
    if q:
        s = like_escape(q.strip())
        query = query.filter(or_(
            LoanORM.barcode.ilike(f"%{s}%", escape="\\"),
            LoanORM.title.ilike(f"%{s}%", escape="\\"),
            LoanORM.reader_card_no.ilike(f"%{s}%", escape="\\"),
            LoanORM.reader_name.ilike(f"%{s}%", escape="\\")))
    if date_from:
        query = query.filter(LoanORM.loaned_at >= _local_day_start(date_from))
    if date_to:
        query = query.filter(LoanORM.loaned_at <
                             _local_day_start(date_to + timedelta(days=1)))
    total, rows = paginate(apply_sort(query, LOAN_SORTS, sort, order,
                                      default="loaned_at", default_order="desc",
                                      tiebreak=LoanORM.id),
                           page, page_size)
    return {"total": total, "page": page, "page_size": page_size,
            "items": [loan_dict(x) for x in rows]}


FINE_SORTS = {
    "barcode": LoanORM.barcode,
    "title": LoanORM.title,
    "reader_name": LoanORM.reader_name,
    "reader_card_no": LoanORM.reader_card_no,
    "reader_class": LoanORM.reader_class,
    "due_on": LoanORM.due_on,
    "returned_at": LoanORM.returned_at,
    "fine_amount": LoanORM.fine_amount,
    "id": LoanORM.id,
}


@router.get("/fines")
@api(module="circulation")
def list_fines(
    library_id: int | None = None,
    status: str = Query("unpaid", description="unpaid | paid | waived | all"),
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str | None = None, order: str | None = None,
    db: Session = Depends(get_db),
):
    """罚款清单（只有归还时产生过金额的行）。开关关闭时前端不调用，但接口仍可查。"""
    base = db.query(LoanORM).filter(LoanORM.fine_amount > 0)
    if library_id:
        base = base.filter(LoanORM.library_id == library_id)

    query = base
    if status == "unpaid":
        query = query.filter(LoanORM.fine_status == FINE_UNPAID)
    elif status == "paid":
        query = query.filter(LoanORM.fine_status == FINE_PAID)
    elif status == "waived":
        query = query.filter(LoanORM.fine_status == FINE_WAIVED)
    elif status != "all":
        raise HTTPException(400, "status 只能是 unpaid / paid / waived / all")
    if q:
        s = like_escape(q.strip())
        query = query.filter(or_(
            LoanORM.barcode.ilike(f"%{s}%", escape="\\"),
            LoanORM.title.ilike(f"%{s}%", escape="\\"),
            LoanORM.reader_card_no.ilike(f"%{s}%", escape="\\"),
            LoanORM.reader_name.ilike(f"%{s}%", escape="\\")))
    total, rows = paginate(apply_sort(query, FINE_SORTS, sort, order,
                                      default="returned_at", default_order="desc",
                                      tiebreak=LoanORM.id),
                           page, page_size)

    # 汇总不受 status / q 影响（页签上的「未缴合计」始终是该库全量）
    unpaid_sum, unpaid_count = (
        base.filter(LoanORM.fine_status == FINE_UNPAID)
        .with_entities(func.coalesce(func.sum(LoanORM.fine_amount), 0),
                       func.count(LoanORM.id)).one())
    today = date.today()
    collected_today = float(
        base.filter(LoanORM.fine_status == FINE_PAID,
                    LoanORM.fine_settled_at >= _local_day_start(today),
                    LoanORM.fine_settled_at <
                    _local_day_start(today + timedelta(days=1)))
        .with_entities(func.coalesce(func.sum(LoanORM.fine_amount), 0))
        .scalar() or 0)
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [loan_dict(x) for x in rows],
        "summary": {"unpaid_count": int(unpaid_count or 0),
                    "unpaid_amount": round(float(unpaid_sum or 0), 2),
                    "collected_today": round(collected_today, 2)},
    }


@router.post("/fines/{loan_id}/settle")
@api(module="circulation")
def settle_fine_api(loan_id: int, payload: FineSettleIn, request: Request,
                    db: Session = Depends(get_db)):
    loan = db.get(LoanORM, loan_id)
    try:
        settle_fine(db, loan=loan, status=payload.status,
                    operator=operator_of(request))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(loan)
    label = FINE_STATUS_LABELS.get(loan.fine_status or "", loan.fine_status)
    return {"loan": loan_dict(loan), "message": f"逾期罚款状态已改为「{label}」"}


@router.get("/stats")
@api(module="circulation")
def circulation_stats(library_id: int | None = None, db: Session = Depends(get_db)):
    today = date.today()
    start_today = _local_day_start(today)
    start_tomorrow = _local_day_start(today + timedelta(days=1))

    def _base():
        q = db.query(LoanORM)
        if library_id:
            q = q.filter(LoanORM.library_id == library_id)
        return q

    today_borrowed = _base().filter(LoanORM.loaned_at >= start_today,
                                    LoanORM.loaned_at < start_tomorrow).count()
    today_returned = _base().filter(LoanORM.returned_at >= start_today,
                                    LoanORM.returned_at < start_tomorrow).count()
    active = _base().filter(LoanORM.status == LOAN_ACTIVE).count()
    overdue = _base().filter(LoanORM.status == LOAN_ACTIVE,
                             LoanORM.due_on < today).count()
    readers_active = (_base().filter(LoanORM.status == LOAN_ACTIVE)
                      .with_entities(func.count(func.distinct(LoanORM.reader_id)))
                      .scalar() or 0)
    lost = _base().filter(LoanORM.status == LOAN_LOST).count()
    comp_sum, comp_count = (
        _base().filter(LoanORM.compensation_status == FINE_UNPAID)
        .with_entities(func.coalesce(func.sum(LoanORM.compensation_amount), 0),
                       func.count(LoanORM.id)).one())

    # 近 14 天借还曲线：与 dashboard 同一份口径，实现见 services/trend.py
    trend = loan_day_trend(db, library_id=library_id)

    top = (_base().filter(LoanORM.loaned_at >=
                          _local_day_start(today - timedelta(days=29)),
                          LoanORM.title.isnot(None))
           .with_entities(LoanORM.title, func.count().label("n"))
           .group_by(LoanORM.title).order_by(func.count().desc())
           .limit(10).all())

    # 罚款统计只在开关打开时算（关闭时不查、不返回非零，界面也整块隐藏）
    fines_today = 0.0
    fines_unpaid = 0.0
    fines_unpaid_count = 0
    if fines_enabled(rules_value(db)):
        fines_today = float(
            _base().filter(LoanORM.fine_status == FINE_PAID,
                           LoanORM.fine_settled_at >= start_today,
                           LoanORM.fine_settled_at < start_tomorrow)
            .with_entities(func.coalesce(func.sum(LoanORM.fine_amount), 0))
            .scalar() or 0)
        unpaid_sum, unpaid_count = (
            _base().filter(LoanORM.fine_status == FINE_UNPAID)
            .with_entities(func.coalesce(func.sum(LoanORM.fine_amount), 0),
                           func.count(LoanORM.id)).one())
        fines_unpaid = float(unpaid_sum or 0)
        fines_unpaid_count = int(unpaid_count or 0)

    return {
        "summary": {"today_borrowed": today_borrowed,
                    "today_returned": today_returned,
                    "active": active, "overdue": overdue,
                    "readers_active": int(readers_active),
                    "lost": lost,
                    "compensation_unpaid": round(float(comp_sum or 0), 2),
                    "compensation_unpaid_count": int(comp_count or 0),
                    "fines_today": round(fines_today, 2),
                    "fines_unpaid": round(fines_unpaid, 2),
                    "fines_unpaid_count": fines_unpaid_count},
        "by_day": trend,
        "top_titles": [{"title": t, "count": n} for t, n in top],
    }


@router.get("/overdue/notices")
@api(module="circulation")
def overdue_notices(library_id: int | None = None,
                    db: Session = Depends(get_db)):
    """按读者汇总逾期册，供打印催还单。**只含 ACTIVE 且已过应还日**。

    联系方式优先取读者档案的当前值，档案缺失时回退借阅台账里的快照。
    失效读者（暂停 / 注销）只要还有逾期册也照样出单——债务不随状态消失。
    """
    library = resolve_library(db, library_id)
    today = date.today()
    rows = (db.query(LoanORM)
            .filter(LoanORM.library_id == library.id,
                    LoanORM.status == LOAN_ACTIVE,
                    LoanORM.due_on < today)
            .order_by(LoanORM.reader_class, LoanORM.reader_name,
                      LoanORM.due_on, LoanORM.id)
            .all())
    reader_ids = {x.reader_id for x in rows}
    readers = ({r.id: r for r in db.query(ReaderORM)
                .filter(ReaderORM.id.in_(reader_ids)).all()}
               if reader_ids else {})
    groups: dict[int, dict] = {}
    for loan in rows:
        r = readers.get(loan.reader_id)
        group = groups.get(loan.reader_id)
        if group is None:
            group = {
                "reader_id": loan.reader_id,
                "card_no": (r.card_no if r is not None else loan.reader_card_no),
                "name": (r.name if r is not None else loan.reader_name),
                "grade": r.grade if r is not None else None,
                "class_name": (r.class_name if r is not None
                               else loan.reader_class),
                "phone": r.phone if r is not None else None,
                "count": 0, "max_days_overdue": 0, "items": [],
            }
            groups[loan.reader_id] = group
        days = (today - loan.due_on).days
        group["count"] += 1
        group["max_days_overdue"] = max(group["max_days_overdue"], days)
        group["items"].append({
            "barcode": loan.barcode, "title": loan.title,
            "due_on": loan.due_on.isoformat(), "days_overdue": days,
            "renew_count": loan.renew_count or 0,
        })
    ordered = sorted(groups.values(),
                     key=lambda g: (g["class_name"] or "", g["name"] or "",
                                    g["card_no"] or ""))
    return {
        "library_id": library.id,
        "library_name": library.name,
        "today": today.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_readers": len(ordered),
        "total_loans": len(rows),
        "readers": ordered,
    }


@router.get("/overdue/export")
@api(module="circulation")
def export_overdue(library_id: int | None = None, db: Session = Depends(get_db)):
    """逾期清单 Excel。只含 ACTIVE 且已过应还日的借阅。"""
    q = db.query(LoanORM).filter(LoanORM.status == LOAN_ACTIVE,
                                 LoanORM.due_on < date.today())
    if library_id:
        q = q.filter(LoanORM.library_id == library_id)
    rows = q.order_by(LoanORM.due_on, LoanORM.id).all()
    rules = rules_value(db)
    fines_on = fines_enabled(rules)

    wb = Workbook()
    ws = wb.active
    ws.title = "逾期清单"
    headers = ["条码", "题名", "借书证号", "姓名", "班级",
               "外借日期", "应还日期", "逾期天数", "续借次数"]
    if fines_on:
        headers.append("预估逾期罚款（元）")
    ws.append(headers)
    today = date.today()
    for x in rows:
        line = [
            x.barcode or "", x.title or "", x.reader_card_no or "",
            x.reader_name or "", x.reader_class or "",
            x.loaned_at.astimezone().strftime("%Y-%m-%d") if x.loaned_at else "",
            x.due_on.isoformat(), (today - x.due_on).days, x.renew_count or 0]
        if fines_on:
            line.append(float(compute_fine(x.due_on, today, rules)))
        ws.append(line)
    widths = [18, 40, 14, 12, 16, 12, 12, 10, 10] + ([12] if fines_on else [])
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"
    return xlsx_response(wb, f"逾期清单_{today.isoformat()}.xlsx")


@router.get("/rules")
@api(module="circulation")
def get_rules(db: Session = Depends(get_db)):
    return {"rules": rules_value(db), "defaults": DEFAULT_RULES}


@router.put("/rules")
@api(module="circulation")
def put_rules(payload: RulesIn, db: Session = Depends(get_db)):
    if len(payload.closed_dates) > MAX_CLOSED_DATES:
        raise HTTPException(400, f"闭馆日最多 {MAX_CLOSED_DATES} 天")
    bad: list[str] = []
    for v in payload.closed_dates:
        try:
            date.fromisoformat(v)
        except ValueError:
            bad.append(v)
    if bad:
        raise HTTPException(400, f"闭馆日格式应为 YYYY-MM-DD：{'、'.join(bad[:5])}")
    rules = save_rules(db, payload.model_dump())
    db.commit()
    return {"rules": rules, "message": "借阅规则已保存"}
