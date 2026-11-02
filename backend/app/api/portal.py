# -*- coding: utf-8 -*-
"""读者门户：登录、发现、我的借阅与预约。

馆员鉴权与读者鉴权是两套完全独立的令牌；`/api/portal` 在馆员中间件里整体旁路，
除登录/状态外每个路由都通过 `current_reader` 强制读者身份。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..core import throttle
from ..db import get_db, like_escape
from ..models import (LOAN_ACTIVE, LOAN_LOST, LOAN_RETURNED, READER_ACTIVE,
                      RESERVATION_ACTIVE_STATUSES, STATUS_AVAILABLE,
                      BookLibrary, CLCClass, HoldingORM, LoanORM, ReaderORM,
                      RecordORM, ReservationORM)
from ..services import portal_auth
from ..services import reservations as reservation_service
from ..services.circulation import (active_loans_of_reader, loan_dict,
                                    overdue_count, reader_compensation_summary,
                                    reader_fine_summary, renew_loan,
                                    rules_value, type_rule)
from ..services.recommendations import cards_for_records, page_data
from ..services.paging import paginate
from ..core.route_meta import api
from ..schemas import BookCardPage

router = APIRouter(prefix="/portal", tags=["portal"])


def request_client(request: Request) -> str | None:
    return ((request.headers.get("User-Agent") or "")[:256] or None)


def current_reader(request: Request, db: Session = Depends(get_db)) -> ReaderORM:
    reader = portal_auth.reader_from_request(db, request)
    if reader is None:
        raise HTTPException(401, "读者登录已过期，请重新登录")
    return reader


def _library(db: Session, reader: ReaderORM) -> BookLibrary:
    lib = db.get(BookLibrary, reader.library_id)
    if lib is None:
        raise HTTPException(404, "图书库不存在")
    return lib


def _reader_full(db: Session, reader: ReaderORM) -> dict:
    rules = rules_value(db)
    active = active_loans_of_reader(db, reader.id)
    rule = type_rule(rules, reader.reader_type)
    account = portal_auth.account_info(db, reader.id)
    return {
        **portal_auth.to_public(reader),
        "rules": {"loan_days": rule["loan_days"], "max_items": rule["max_items"],
                  "max_renew": rule["max_renew"], "renew_days": rule["renew_days"]},
        "stats": {"active": len(active), "overdue": overdue_count(db, reader.id),
                  "total": db.query(LoanORM).filter(LoanORM.reader_id == reader.id).count()},
        "fines": reader_fine_summary(db, reader.id, rules),
        "compensations": reader_compensation_summary(db, reader.id),
        "account": account,
    }


def _field_sub(fields, tag: str, code: str) -> str | None:
    for field in fields or []:
        if str(field.get("tag")) != tag:
            continue
        for pair in field.get("sf") or []:
            if pair and pair[0] == code and str(pair[1] or "").strip():
                return str(pair[1]).strip()
    return None


def _category_rows(db: Session, library_id: int) -> list[dict]:
    letter = func.upper(func.left(func.btrim(func.coalesce(RecordORM.class_no, "")), 1))
    rows = (db.query(letter.label("k"), func.count(HoldingORM.id).label("n"))
            .select_from(HoldingORM)
            .join(RecordORM, HoldingORM.record_id == RecordORM.id)
            .filter(HoldingORM.library_id == library_id,
                    HoldingORM.status == STATUS_AVAILABLE)
            .group_by(letter).all())
    labels = dict(db.query(CLCClass.class_no, CLCClass.title)
                  .filter(CLCClass.level == 1).all())
    out = []
    for key, count in rows:
        if not key:
            continue
        out.append({"key": key, "label": labels.get(key, key), "copies": int(count)})
    out.sort(key=lambda item: -item["copies"])
    return out


def _active_loan_count(db: Session, library_id: int) -> int:
    return (db.query(func.count(LoanORM.id))
            .filter(LoanORM.library_id == library_id,
                    LoanORM.status == LOAN_ACTIVE).scalar() or 0)


@router.get("/auth/libraries")
@api(module="auth")
def portal_libraries(db: Session = Depends(get_db)):
    """登录页消歧用：本系统有哪些图书库。**只给 id 与名称**，不含任何读者数据。

    ## 为什么需要这条公开接口

    借书证号只在**图书库内**唯一：两个库各自从 0001 编起是正常状态，同一个证号
    会对应两个不同的读者。登录接口遇到这种情形会要求指定 `library_id`，但登录页
    原本没有任何图书库控件——于是多库学校的读者永远登不进门户（单库学校不受影响，
    这也是这个缺口一直没被发现的原因）。

    暴露库名不构成信息泄露：它等同于「这个系统服务于哪几个校区」，界面上本来就
    看得见。读者姓名、证号、借阅记录一条都不在这里。
    """
    rows = db.query(BookLibrary.id, BookLibrary.name).order_by(BookLibrary.id).all()
    return {"libraries": [{"id": row[0], "name": row[1]} for row in rows]}


class PortalLoginIn(BaseModel):
    card_no: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)
    library_id: int | None = None


def _portal_login_blocking(db: Session, request: Request, payload: "PortalLoginIn",
                           card_key: str) -> dict:
    """门户登录的**阻塞**部分（查库 + scrypt + 签发会话）。跑在线程池里。"""
    try:
        token, reader = portal_auth.login(
            db, payload.card_no, payload.password,
            library_id=payload.library_id,
            client=request_client(request))
    except PermissionError as e:
        # 证号不存在 / 口令错 / 账号停用 → 统一记一次失败并回同一句话，
        # 不给「这个证号存在」留任何可观测差异（文案与耗时都已对齐）。
        throttle.PORTAL_BY_CARD.fail(card_key)
        raise HTTPException(401, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    throttle.PORTAL_BY_CARD.reset(card_key)
    return {"token": token, "reader": _reader_full(db, reader),
            "message": "登录成功"}


@router.post("/auth/login")
@api(module="auth")
async def portal_login(payload: PortalLoginIn, request: Request,
                       db: Session = Depends(get_db)):
    """读者门户登录。

    ## 为什么这里比馆员登录更需要限速

    借书证号多为「年份 + 流水号」，是可以猜的；
    初始口令又由馆员逐个设定，现场很容易出现「全班同一个口令」。两者叠加，
    遍历证号 + 猜常见口令就能读到同学的借阅历史——而受害者完全无法察觉
    （门户不显示登录设备）。

    所以按**证号**做失败计数（15 分钟 5 次），并共用口令校验的并发闸门。
    不按来源：局域网形态下前端是代理，后端看到的来源恒为 127.0.0.1。

    与馆员登录同样改成 async：闸门排队在事件循环上，不占线程池（见
    api/auth.py::auth_login 的说明）。
    """
    card_key = (payload.card_no or "").strip().lower() or "-"
    wait = throttle.PORTAL_BY_CARD.retry_after(card_key)
    if wait > 0:
        raise HTTPException(429, f"尝试过于频繁，请 {wait} 秒后再试",
                            headers={"Retry-After": str(wait)})
    if not await throttle.acquire_verify_slot():
        raise HTTPException(429, "登录请求过多，请稍后重试",
                            headers={"Retry-After": "5"})
    try:
        return await run_in_threadpool(_portal_login_blocking, db, request,
                                       payload, card_key)
    finally:
        throttle.release_verify_slot()


@router.get("/auth/status")
@api(module="auth")
def portal_status(request: Request, db: Session = Depends(get_db)):
    reader = portal_auth.reader_from_request(db, request)
    return {"authenticated": reader is not None,
            "reader": _reader_full(db, reader) if reader is not None else None}


@router.post("/auth/logout")
@api(module="auth")
def portal_logout(request: Request, db: Session = Depends(get_db)):
    from ..services.auth import token_from_request
    portal_auth.logout(db, token_from_request(request))
    return {"ok": True}


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


@router.post("/auth/password")
@api(module="auth")
def portal_change_password(payload: PasswordIn, request: Request,
                           reader: ReaderORM = Depends(current_reader),
                           db: Session = Depends(get_db)):
    try:
        token = portal_auth.change_password(
            db, reader, payload.old_password, payload.new_password,
            client=request_client(request))
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    return {"ok": True, "token": token, "message": "密码已修改"}


@router.get("/me")
@api(module="auth")
def portal_me(reader: ReaderORM = Depends(current_reader),
              db: Session = Depends(get_db)):
    return _reader_full(db, reader)


@router.get("/home")
@api(module="auth")
def portal_home(reader: ReaderORM = Depends(current_reader),
                db: Session = Depends(get_db)):
    library = _library(db, reader)
    collection = (db.query(func.count(HoldingORM.id))
                  .filter(HoldingORM.library_id == library.id,
                          HoldingORM.status == STATUS_AVAILABLE).scalar() or 0)
    available = max(0, int(collection) - _active_loan_count(db, library.id))
    since = datetime.now() - timedelta(days=30)
    new_titles = (db.query(func.count(RecordORM.id))
                  .filter(RecordORM.library_id == library.id,
                          RecordORM.created_at >= since).scalar() or 0)
    recommendations = page_data(db, library)
    keywords = [card["title"] for card in recommendations["popular"][:4]
                if card.get("title")]
    return {
        "library": {"id": library.id, "name": library.name},
        "reader": portal_auth.to_public(reader),
        "stats": {"copies": int(collection), "available": int(available),
                  "titles": db.query(func.count(RecordORM.id))
                  .filter(RecordORM.library_id == library.id).scalar() or 0,
                  "new_titles": int(new_titles)},
        "categories": _category_rows(db, library.id),
        "curated": recommendations["curated"],
        "popular": recommendations["popular"],
        "new_arrivals": recommendations["new_arrivals"],
        "hot_keywords": keywords,
    }


@router.get("/books", response_model=BookCardPage)
@api(module="auth")
def portal_books(
    q: str | None = None,
    category: str | None = None,
    sort: str = Query("title", description="title | year | popular"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=60),
    reader: ReaderORM = Depends(current_reader),
    db: Session = Depends(get_db),
):
    query = db.query(RecordORM).filter(RecordORM.library_id == reader.library_id)
    if q:
        s = like_escape(q.strip())
        query = query.filter(or_(
            RecordORM.title_norm.ilike(f"%{s}%", escape="\\"),
            RecordORM.author_norm.ilike(f"%{s}%", escape="\\"),
            RecordORM.isbn_norm.like(f"%{like_escape(q.replace('-', ''))}%", escape="\\")))
    if category:
        query = query.filter(RecordORM.class_no.ilike(
            f"{like_escape(category.strip())}%", escape="\\"))
    # 排序：题名（默认，稳定翻页）/ 出版年（新在前）/ 借阅热度（本库外借次数）。
    # 热度用子查询外连聚合，不在 python 侧拉全量计数；空次数按 0 参与排序。
    if sort == "year":
        query = query.order_by(RecordORM.year.desc().nulls_last(),
                               RecordORM.title_norm.asc(), RecordORM.id.asc())
    elif sort == "popular":
        counts = (db.query(LoanORM.record_id, func.count().label("n"))
                  .filter(LoanORM.library_id == reader.library_id)
                  .group_by(LoanORM.record_id).subquery())
        query = (query.outerjoin(counts, RecordORM.id == counts.c.record_id)
                 .order_by(func.coalesce(counts.c.n, 0).desc(),
                           RecordORM.title_norm.asc(), RecordORM.id.asc()))
    elif sort != "title":
        raise HTTPException(400, "sort 只能是 title / year / popular")
    else:
        query = query.order_by(RecordORM.title_norm.asc(), RecordORM.id.asc())
    total, records = paginate(query, page, page_size)
    cards = cards_for_records(db, records)
    return {"total": total, "page": page, "page_size": page_size, "items": cards}


@router.get("/books/{record_id}")
@api(module="auth")
def portal_book_detail(record_id: int,
                       reader: ReaderORM = Depends(current_reader),
                       db: Session = Depends(get_db)):
    record = db.get(RecordORM, record_id)
    if record is None or record.library_id != reader.library_id:
        raise HTTPException(404, "书目不存在")
    card = cards_for_records(db, [record])[0]
    holdings = (db.query(HoldingORM)
                .filter(HoldingORM.record_id == record.id,
                        HoldingORM.status == STATUS_AVAILABLE)
                .order_by(HoldingORM.id).all())
    active_ids = {hid for (hid,) in
                  db.query(LoanORM.holding_id)
                  .filter(LoanORM.holding_id.in_([h.id for h in holdings]),
                          LoanORM.status == LOAN_ACTIVE).all()} if holdings else set()
    locations: list[str] = []
    for holding in holdings:
        if holding.id in active_ids or not holding.location:
            continue
        if holding.location not in locations:
            locations.append(holding.location)
    reservation = (db.query(ReservationORM)
                   .filter(ReservationORM.reader_id == reader.id,
                           ReservationORM.record_id == record.id,
                           ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
                   .first())
    current_loan = (db.query(LoanORM)
                    .filter(LoanORM.reader_id == reader.id,
                            LoanORM.record_id == record.id,
                            LoanORM.status == LOAN_ACTIVE).first())
    can_reserve = (card["available_count"] == 0
                   and reader.status == READER_ACTIVE
                   and reservation is None
                   and current_loan is None)
    reason = ""
    if current_loan is not None:
        reason = "您已借阅此书"
    elif card["available_count"] > 0:
        reason = "当前有可借馆藏，请到馆借阅"
    elif reader.status != READER_ACTIVE:
        reason = "读者当前状态不能预约"
    elif reservation is not None:
        reason = "已预约此书"
    return {
        **card,
        "summary": _field_sub(record.fields, "330", "a"),
        "subjects": [str(pair[1]) for field in (record.fields or [])
                     if str(field.get("tag")) == "606"
                     for pair in (field.get("sf") or [])
                     if pair and pair[0] == "a" and pair[1]],
        "locations": locations,
        "shelf_no": card.get("shelf_no") or "",
        "hold_days": int(rules_value(db).get("reservation_hold_days") or 3),
        "can_reserve": can_reserve,
        "reserve_reason": reason,
        "reservation": (reservation_service.serialize_many(db, [reservation])[0]
                        if reservation is not None else None),
        "reader_status": reader.status,
    }


@router.get("/loans")
@api(module="auth")
def portal_loans(status: str = Query("all", description="active | all"),
                 reader: ReaderORM = Depends(current_reader),
                 db: Session = Depends(get_db)):
    """`status=active` 是给门户欢迎带用的轻量形态：只查在借，跳过历史查询。

    欢迎带只需要「在借几本 + 最近到期」，历史 50 条在每次进首页时都拉一遍纯属浪费。
    """
    if status not in ("all", "active"):
        raise HTTPException(400, "status 只能是 active / all")
    rules = rules_value(db)
    active = active_loans_of_reader(db, reader.id)
    history: list[LoanORM] = []
    if status == "all":
        history = (db.query(LoanORM)
                   .filter(LoanORM.reader_id == reader.id,
                           LoanORM.status.in_((LOAN_RETURNED, LOAN_LOST)))
                   .order_by(func.coalesce(LoanORM.returned_at, LoanORM.lost_at).desc(),
                             LoanORM.id.desc())
                   .limit(50).all())
    rule = type_rule(rules, reader.reader_type)
    return {
        "reader": _reader_full(db, reader),
        "rules": {"loan_days": rule["loan_days"], "max_items": rule["max_items"],
                  "max_renew": rule["max_renew"], "renew_days": rule["renew_days"]},
        "active": [loan_dict(row) for row in active],
        "history": [loan_dict(row) for row in history],
        "summary": {"active": len(active), "overdue": overdue_count(db, reader.id),
                    "total": db.query(LoanORM).filter(LoanORM.reader_id == reader.id).count()},
    }


@router.post("/loans/{loan_id}/renew")
@api(module="auth")
def portal_renew(loan_id: int,
                 reader: ReaderORM = Depends(current_reader),
                 db: Session = Depends(get_db)):
    loan = db.get(LoanORM, loan_id)
    if loan is None or loan.reader_id != reader.id:
        raise HTTPException(404, "借阅记录不存在")
    try:
        renew_loan(db, loan=loan, rules=rules_value(db))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.refresh(loan)
    return {"loan": loan_dict(loan), "message": f"续借成功，应还 {loan.due_on.isoformat()}"}


@router.get("/reservations")
@api(module="auth")
def portal_reservations(status: str = "active",
                        reader: ReaderORM = Depends(current_reader),
                        db: Session = Depends(get_db)):
    query = db.query(ReservationORM).filter(ReservationORM.reader_id == reader.id)
    if status == "active":
        query = query.filter(ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
    elif status != "all":
        raise HTTPException(400, "status 只能是 active / all")
    rows = query.order_by(ReservationORM.reserved_at.desc()).all()
    return {"items": reservation_service.serialize_many(db, rows),
            "total": len(rows)}


class ReserveIn(BaseModel):
    record_id: int


@router.post("/reservations")
@api(module="auth")
def portal_reserve(payload: ReserveIn,
                   reader: ReaderORM = Depends(current_reader),
                   db: Session = Depends(get_db)):
    record = db.get(RecordORM, payload.record_id)
    if record is None or record.library_id != reader.library_id:
        raise HTTPException(404, "书目不存在")
    try:
        row = reservation_service.create_reservation(db, reader=reader, record=record)
        db.commit()
    except reservation_service.DuplicateReservationError as e:
        db.rollback()
        raise HTTPException(409, str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "预约提交冲突，请刷新后重试")
    db.refresh(row)
    return {"reservation": reservation_service.serialize_many(db, [row])[0],
            "message": "预约已登记，到书后会通知您"}


@router.post("/reservations/{reservation_id}/cancel")
@api(module="auth")
def portal_cancel_reservation(reservation_id: int,
                              reader: ReaderORM = Depends(current_reader),
                              db: Session = Depends(get_db)):
    row = db.get(ReservationORM, reservation_id)
    if row is None or row.reader_id != reader.id:
        raise HTTPException(404, "预约记录不存在")
    try:
        # 服务在书目锁内重新检查并取消、推进；保留上方本人权限校验。
        reservation_service.cancel(db, row=row, reason="读者自行取消",
                                   operator="读者门户")
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    return {"message": "预约已取消"}
