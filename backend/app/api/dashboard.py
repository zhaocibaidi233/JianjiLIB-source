# -*- coding: utf-8 -*-
"""馆员工作台聚合。

只读运营摘要：KPI、近 14 天借还曲线、馆藏结构、待办、热门与新书。
数据口径与借还台 / 统计报表一致（在架册数、本地日归并），不在这里另造一套。

`/api/dashboard` 在 `core/perm.py` 里登记为**登录即可访问**：它是上班第一眼看的
运营摘要，任何馆员都该看到；具体明细仍由各模块权限控制。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.perm import PERM_READ, role_can
from ..db import get_db
from ..models import (FINE_UNPAID, LOAN_ACTIVE, READER_SUSPENDED,
                      RESERVATION_READY, STATUS_AVAILABLE, AgentToken,
                      BookLibrary, HoldingORM, LoanORM, ReaderORM, RecordORM,
                      ReservationORM, Role, User)
from ..services.trend import loan_day_trend
from ..core.route_meta import api
from .circulation import _local_day_start
from .stats import _class_key, _level1

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _role_can_read_readers(db: Session, role_id: int | None) -> bool:
    """按角色判「读者」模块读权限。三条身份来源共用这一份口径。"""
    role = db.get(Role, role_id) if role_id is not None else None
    perms = (role.permissions or {}) if role is not None else {}
    return role_can(perms, "readers", PERM_READ)


def _may_see_reader_identity(db: Session, request: Request) -> bool:
    """当前请求方能不能看到读者姓名 / 借书证号。

    ## 三条身份来源都要判，漏一条就是一条旁路

    - **登录账号**：`request.state.user_id` → 查它的角色；
    - **agent 长期凭据**：`request.state.agent_id` → 查凭据绑的角色。agent 与人共用
      同一套模块权限（中间件就是这么判的），不该因为「不是人」而更宽；
    - **共享密钥**：绑了角色就按角色判；**没绑**是文档写明的旧口径（跳过全部模块
      权限），继续放行，否则会把既有的脚本部署一夜打死。

    ## 为什么工作台要单独判一次

    `/api/dashboard` 登记为「登录即可访问」——上班第一眼看运营摘要，不该被模块
    权限挡住。但它顺带返回了**逾期读者的姓名与证号**，而这两个字段属于「读者」
    模块的敏感信息：一个只做统计、没有读者模块权限的角色，不该从工作台旁路拿到
    全校逾期名单（未成年人数据）。所以摘要照给（数字是聚合的，不敏感），身份字段
    按读者模块权限裁剪。前端 `AdminShell` 对空值已有兜底（显示「读者」）。

    修复前只判了 `user_id is None → 全部可见`：agent 请求该字段恒为空，于是被当成
    「登录未启用（单机形态）」，任何绑了角色的凭据都能把名单读走。
    """
    if getattr(request.state, "shared_token", False):
        role_id = getattr(request.state, "shared_role_id", None)
        return True if role_id is None else _role_can_read_readers(db, role_id)

    agent_id = getattr(request.state, "agent_id", None)
    if agent_id is not None:
        agent = db.get(AgentToken, agent_id)
        return agent is not None and _role_can_read_readers(db, agent.role_id)

    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        # 登录未启用（单机形态）：本来就没有权限概念，全部可见
        return True
    user = db.get(User, user_id)
    if user is None or not user.enabled:
        return False
    return _role_can_read_readers(db, user.role_id)


def _count(db: Session, model, *where, column=None) -> int:
    q = db.query(func.count(column if column is not None else model.id)).select_from(model)
    if where:
        q = q.filter(*where)
    return int(q.scalar() or 0)


def _available_by_record(db: Session, record_ids: list[int]) -> dict[int, int]:
    """在架且当前未外借的册数（热门/新书卡片的口径）。"""
    if not record_ids:
        return {}
    holdings = (db.query(HoldingORM.id, HoldingORM.record_id)
                .filter(HoldingORM.record_id.in_(record_ids),
                        HoldingORM.status == STATUS_AVAILABLE)
                .all())
    on_loan = {hid for (hid,) in
               db.query(LoanORM.holding_id)
               .filter(LoanORM.holding_id.in_([h[0] for h in holdings]),
                       LoanORM.status == LOAN_ACTIVE).all()}
    counts: dict[int, int] = {}
    for holding_id, record_id in holdings:
        if holding_id not in on_loan:
            counts[record_id] = counts.get(record_id, 0) + 1
    return counts


@router.get("")
@api(module="auth")
def dashboard(
    request: Request,
    library_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    today = date.today()
    start_today = _local_day_start(today)
    start_tomorrow = _local_day_start(today + timedelta(days=1))
    start_yesterday = _local_day_start(today - timedelta(days=1))

    library = None
    if library_id is not None:
        library = db.get(BookLibrary, library_id)
        if library is None:
            raise HTTPException(404, f"图书库不存在：{library_id}")
    else:
        library = db.query(BookLibrary).order_by(BookLibrary.id).first()
    if library is None:
        return {
            "library": None,
            "today": today.isoformat(),
            "kpis": {k: 0 for k in (
                "today_borrowed", "yesterday_borrowed", "today_returned",
                "yesterday_returned", "active_loans", "overdue", "readers_active",
                "reader_total", "collection_copies", "title_count")},
            "trend": [], "collection": {"total": 0, "rows": []},
            "todos": [], "overdue_items": [], "hot_books": [], "new_books": [],
        }

    lib_id = library.id
    # 读者姓名/证号是否可见（见 _may_see_reader_identity）：摘要照给，身份裁剪
    may_see_reader = _may_see_reader_identity(db, request)

    def loans():
        return db.query(LoanORM).filter(LoanORM.library_id == lib_id)

    today_borrowed = loans().filter(LoanORM.loaned_at >= start_today,
                                    LoanORM.loaned_at < start_tomorrow).count()
    today_returned = loans().filter(LoanORM.returned_at >= start_today,
                                    LoanORM.returned_at < start_tomorrow).count()
    yesterday_borrowed = loans().filter(LoanORM.loaned_at >= start_yesterday,
                                        LoanORM.loaned_at < start_today).count()
    yesterday_returned = loans().filter(LoanORM.returned_at >= start_yesterday,
                                        LoanORM.returned_at < start_today).count()
    active_loans = loans().filter(LoanORM.status == LOAN_ACTIVE).count()
    overdue = loans().filter(LoanORM.status == LOAN_ACTIVE,
                             LoanORM.due_on < today).count()
    readers_active = int(loans().filter(LoanORM.status == LOAN_ACTIVE)
                         .with_entities(func.count(func.distinct(LoanORM.reader_id)))
                         .scalar() or 0)

    reader_total = _count(db, ReaderORM, ReaderORM.library_id == lib_id)
    collection_copies = _count(db, HoldingORM,
                               HoldingORM.library_id == lib_id,
                               HoldingORM.status == STATUS_AVAILABLE)
    title_count = _count(db, RecordORM, RecordORM.library_id == lib_id)

    # 近 14 天借还曲线：按本地日归并（与 circulation.stats 同一口径，
    # 实现见 services/trend.py）
    trend = loan_day_trend(db, library_id=lib_id)

    # 馆藏结构：在架册数按中图法首字母归并
    letter = func.upper(func.left(func.btrim(func.coalesce(RecordORM.class_no, "")), 1))
    rows = (db.query(letter.label("k"),
                     func.count(func.distinct(RecordORM.id)).label("titles"),
                     func.count(HoldingORM.id).label("copies"))
            .select_from(HoldingORM)
            .join(RecordORM, HoldingORM.record_id == RecordORM.id)
            .filter(HoldingORM.library_id == lib_id,
                    HoldingORM.status == STATUS_AVAILABLE)
            .group_by(letter).all())
    level1 = _level1(db)
    groups: dict[str, dict] = {}
    for raw, titles, copies in rows:
        key, label = _class_key(raw, level1)
        g = groups.setdefault(key, {"key": key, "label": label,
                                    "titles": 0, "copies": 0})
        g["titles"] += int(titles)
        g["copies"] += int(copies)
    collection_rows = sorted(groups.values(), key=lambda x: -x["copies"])
    total_copies = sum(x["copies"] for x in collection_rows)
    for row in collection_rows:
        row["percent"] = round(row["copies"] * 100 / total_copies, 1) if total_copies else 0.0
    collection = {"total": total_copies, "rows": collection_rows}

    # 待办：只派生“现在就能去处理”的事项
    todos = []
    overdue_rows = (loans().filter(LoanORM.status == LOAN_ACTIVE,
                                   LoanORM.due_on < today)
                    .order_by(LoanORM.due_on.asc()).limit(3).all())
    if overdue:
        earliest = overdue_rows[0] if overdue_rows else None
        detail = ""
        if earliest is not None:
            days_late = (today - earliest.due_on).days
            who = (earliest.reader_name or earliest.reader_card_no or "读者") \
                if may_see_reader else "读者"
            detail = f"{who}《{earliest.title or '未知题名'}》已逾期 {days_late} 天"
        todos.append({"kind": "overdue", "label": "逾期未还", "count": overdue,
                      "detail": detail, "link": "/admin/circulation?tab=overdue"})

    unpaid_sum, unpaid_count = (loans().filter(LoanORM.compensation_status == FINE_UNPAID)
                                .with_entities(func.coalesce(func.sum(LoanORM.compensation_amount), 0),
                                               func.count(LoanORM.id)).one())
    if unpaid_count:
        todos.append({"kind": "compensation", "label": "待收赔偿", "count": int(unpaid_count),
                      "detail": f"合计 ¥{float(unpaid_sum or 0):.2f}",
                      "link": "/admin/readers"})

    ready_reservations = _count(
        db, ReservationORM, ReservationORM.library_id == lib_id,
        ReservationORM.status == RESERVATION_READY,
        ReservationORM.expires_at > datetime.now(timezone.utc))
    if ready_reservations:
        earliest = (db.query(ReservationORM.expires_at)
                    .filter(ReservationORM.library_id == lib_id,
                            ReservationORM.status == RESERVATION_READY,
                            ReservationORM.expires_at > datetime.now(timezone.utc))
                    .order_by(ReservationORM.expires_at.asc()).first())
        detail = "读者已到书待取"
        if earliest and earliest[0] is not None:
            detail = f"最早保留至 {earliest[0].astimezone().strftime('%m月%d日')}"
        todos.append({"kind": "ready_reservation", "label": "预约到书待取",
                      "count": ready_reservations, "detail": detail,
                      "link": "/admin/reservations?tab=ready"})

    suspended = _count(db, ReaderORM, ReaderORM.library_id == lib_id,
                       ReaderORM.status == READER_SUSPENDED)
    if suspended:
        todos.append({"kind": "suspended", "label": "暂停读者", "count": suspended,
                      "detail": "查看暂停原因与恢复时间",
                      "link": "/admin/readers?status=suspended"})

    # 热门：近 30 天借阅次数（含已归还，看的是流通热度）
    start_30 = _local_day_start(today - timedelta(days=29))
    hot_rows = (loans().filter(LoanORM.loaned_at >= start_30)
                .with_entities(LoanORM.record_id, func.count().label("n"))
                .group_by(LoanORM.record_id)
                .order_by(func.count().desc()).limit(5).all())
    hot_ids = [rid for rid, _ in hot_rows]
    records = {r.id: r for r in db.query(RecordORM).filter(RecordORM.id.in_(hot_ids)).all()} \
        if hot_ids else {}
    avail = _available_by_record(db, hot_ids)
    hot_books = []
    for rid, n in hot_rows:
        rec = records.get(rid)
        if rec is None:
            continue
        hot_books.append({
            "record_id": rid, "title": rec.title_norm, "author": rec.author_norm,
            "class_no": rec.class_no, "loans_30d": int(n),
            "available_copies": int(avail.get(rid, 0)),
        })

    new_records = (db.query(RecordORM).filter(RecordORM.library_id == lib_id)
                   .order_by(RecordORM.created_at.desc(), RecordORM.id.desc())
                   .limit(4).all())
    new_ids = [r.id for r in new_records]
    new_avail = _available_by_record(db, new_ids)
    new_books = [{
        "record_id": r.id, "title": r.title_norm, "author": r.author_norm,
        "class_no": r.class_no,
        "created_at": r.created_at.date().isoformat() if r.created_at else None,
        "available_copies": int(new_avail.get(r.id, 0)),
    } for r in new_records]

    return {
        "library": {"id": library.id, "name": library.name},
        "today": today.isoformat(),
        "kpis": {
            "today_borrowed": int(today_borrowed),
            "yesterday_borrowed": int(yesterday_borrowed),
            "today_returned": int(today_returned),
            "yesterday_returned": int(yesterday_returned),
            "active_loans": int(active_loans),
            "overdue": int(overdue),
            "readers_active": readers_active,
            "reader_total": reader_total,
            "collection_copies": collection_copies,
            "title_count": title_count,
        },
        "trend": trend,
        "collection": collection,
        "todos": todos,
        "overdue_items": [{
            "loan_id": loan.id, "record_id": loan.record_id,
            "title": loan.title, "reader_id": loan.reader_id,
            # 姓名与证号按读者模块权限裁剪（见 _may_see_reader_identity）
            "reader_name": loan.reader_name if may_see_reader else None,
            "reader_card_no": loan.reader_card_no if may_see_reader else None,
            "due_on": loan.due_on.isoformat(),
            "days_late": (today - loan.due_on).days,
        } for loan in overdue_rows],
        "hot_books": hot_books,
        "new_books": new_books,
    }
