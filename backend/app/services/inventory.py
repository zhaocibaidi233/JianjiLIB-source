# -*- coding: utf-8 -*-
"""盘点业务。

盘点**只记录**：扫描流水 + 漏盘 / 外借 / 异常清单。漏盘不等于遗失，
可能是乱架、外借或正在修补，所以结束时绝不自动注销馆藏。

应盘口径 = 建盘时该库 `holdings.status == AVAILABLE` 的册数快照：
- 已注销 / 已遗失的不应出现在架上，不要求扫到；
- 正在外借的册系统里仍是 AVAILABLE，但实物不在架，单列「外借未归」；
- 盘点期间新编目的册不计入应盘（否则进度永远差几册）。

本模块只 flush，不 commit（与 services/circulation.py 同一约定）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import like_escape
from ..models import (INV_CLOSED, INV_DUPLICATE, INV_FOUND_RESULTS, INV_OK,
                      INV_ON_LOAN, INV_OPEN, INV_OTHER_LIBRARY,
                      INV_PROBLEM_RESULTS, INV_UNKNOWN, INV_WITHDRAWN,
                      LOAN_ACTIVE, STATUS_AVAILABLE,
                      BookLibrary, HoldingORM, InventoryScanORM,
                      InventorySessionORM, LoanORM, RecordORM)
from .paging import paginate

RESULT_LABELS = {
    INV_OK: "正常",
    INV_DUPLICATE: "重复扫描",
    INV_ON_LOAN: "系统外借",
    INV_WITHDRAWN: "已注销/遗失",
    INV_OTHER_LIBRARY: "其他图书库",
    INV_UNKNOWN: "无此条码",
}


def _now() -> datetime:
    return datetime.now().astimezone()


def _found_scan(session_id: int):
    """相关子查询：本册在本次盘点里已被实物扫到。"""
    return exists().where(
        InventoryScanORM.session_id == session_id,
        InventoryScanORM.holding_id == HoldingORM.id,
        InventoryScanORM.result.in_(INV_FOUND_RESULTS),
    )


def _active_loan():
    return exists().where(
        LoanORM.holding_id == HoldingORM.id,
        LoanORM.status == LOAN_ACTIVE,
    )


def _expected_holdings(db: Session, session: InventorySessionORM):
    """应盘范围（不含已注销/遗失，且只含建盘前已存在的册）。

    建盘期间新编目的册不计入：`created_at` 晚于 `started_at` 的册不算“漏扫”，
    否则盘点进度会随着同事在旁边编目而变化。
    """
    q = db.query(HoldingORM).filter(
        HoldingORM.library_id == session.library_id,
        HoldingORM.status == STATUS_AVAILABLE)
    if session.started_at is not None:
        q = q.filter(HoldingORM.created_at <= session.started_at)
    return q


def session_stats(db: Session, session: InventorySessionORM) -> dict:
    found_q = (db.query(func.count(func.distinct(InventoryScanORM.holding_id)))
               .join(HoldingORM, HoldingORM.id == InventoryScanORM.holding_id)
               .filter(InventoryScanORM.session_id == session.id,
                       InventoryScanORM.result.in_(INV_FOUND_RESULTS),
                       HoldingORM.library_id == session.library_id))
    if session.started_at is not None:
        # 盘点中新增的册即使被扫到，也不算“本次应盘已扫到”
        found_q = found_q.filter(HoldingORM.created_at <= session.started_at)
    found = found_q.scalar() or 0
    base = _expected_holdings(db, session)
    missing = base.filter(~_found_scan(session.id), ~_active_loan()).count()
    on_loan = base.filter(~_found_scan(session.id), _active_loan()).count()

    def _count(result: str) -> int:
        return (db.query(func.count(InventoryScanORM.id))
                .filter(InventoryScanORM.session_id == session.id,
                        InventoryScanORM.result == result)
                .scalar() or 0)

    unknown = _count(INV_UNKNOWN)
    other = _count(INV_OTHER_LIBRARY)
    withdrawn = _count(INV_WITHDRAWN)
    on_loan_scanned = _count(INV_ON_LOAN)
    duplicate = _count(INV_DUPLICATE)
    expected = int(session.expected_count or 0)
    return {
        "expected": expected,
        "found": int(found),
        "missing": int(missing),
        "on_loan": int(on_loan),
        "unknown": unknown,
        "other_library": other,
        "withdrawn": withdrawn,
        "on_loan_scanned": on_loan_scanned,
        "duplicate": duplicate,
        "problems": unknown + other + withdrawn + on_loan_scanned,
        "scans": duplicate + int(found) + unknown + other + withdrawn,
        "progress": (min(100.0, round(found * 100 / expected, 1))
                     if expected else 0.0),
    }


def _snapshot(session: InventorySessionORM) -> dict | None:
    if (session.status == INV_CLOSED
            and isinstance(session.result_snapshot, dict)):
        return session.result_snapshot
    return None


def session_dict(db: Session, session: InventorySessionORM) -> dict:
    snap = _snapshot(session)
    stats = snap.get("stats") if snap else session_stats(db, session)
    return {
        "id": session.id,
        "library_id": session.library_id,
        "name": session.name,
        "status": session.status,
        "status_label": "进行中" if session.status == INV_OPEN else "已结束",
        "expected_count": int(session.expected_count or 0),
        "started_at": str(session.started_at) if session.started_at else None,
        "started_by": session.started_by,
        "closed_at": str(session.closed_at) if session.closed_at else None,
        "closed_by": session.closed_by,
        "note": session.note,
        "stats": stats,
        "truncated": snap.get("truncated") if snap else None,
    }


def scan_dict(scan: InventoryScanORM, *, message: str = "") -> dict:
    return {
        "id": scan.id,
        "barcode": scan.barcode,
        "result": scan.result,
        "result_label": RESULT_LABELS.get(scan.result, scan.result),
        "holding_id": scan.holding_id,
        "title": scan.title,
        "call_no": scan.call_no,
        "location": scan.location,
        "reader_name": scan.reader_name,
        "scanned_at": str(scan.scanned_at) if scan.scanned_at else None,
        "scanned_by": scan.scanned_by,
        "message": message,
    }


def _find_open_session(db: Session, library_id: int) -> InventorySessionORM | None:
    """该库进行中的盘点（服务层前置检查；并发窗口由数据库唯一索引兜底）。"""
    return (db.query(InventorySessionORM)
            .filter(InventorySessionORM.library_id == library_id,
                    InventorySessionORM.status == INV_OPEN)
            .first())


def create_session(db: Session, *, library_id: int, name: str | None,
                   note: str | None, operator: str | None) -> InventorySessionORM:
    opened = _find_open_session(db, library_id)
    if opened is not None:
        raise ValueError(
            f"该图书库已有进行中的盘点「{opened.name}」（#{opened.id}），请先结束")
    started = _now()
    expected = (db.query(func.count(HoldingORM.id))
                .filter(HoldingORM.library_id == library_id,
                        HoldingORM.status == STATUS_AVAILABLE,
                        HoldingORM.created_at <= started)
                .scalar() or 0)
    label = (name or "").strip() or f"盘点 {started.strftime('%Y-%m-%d %H:%M')}"
    row = InventorySessionORM(
        library_id=library_id, name=label[:128], status=INV_OPEN,
        expected_count=int(expected), started_at=started, started_by=operator,
        note=(note or "").strip()[:256] or None,
    )
    # 上面的查询是 check-then-act：两个窗口同时点「开始盘点」会都通过检查。
    # `uq_inventory_sessions_open` 让第二个 INSERT 直接失败，这里把它翻译成同一句
    # 人话。用 SAVEPOINT 而不是 rollback —— 回滚会连带丢掉调用方事务里的其它写入。
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        raise ValueError("该图书库已有进行中的盘点（可能是刚刚在另一个窗口创建的），请刷新后重试")
    return row


def _record_title(db: Session, record_id: int) -> str | None:
    rec = db.get(RecordORM, record_id)
    return rec.title_norm if rec is not None else None


def scan(db: Session, *, session: InventorySessionORM, barcode: str,
         operator: str | None) -> tuple[InventoryScanORM, str]:
    """扫一条码。返回 `(扫描流水, 给馆员看的一句话)`。**不 commit**。"""
    if session.status != INV_OPEN:
        raise ValueError("盘点已结束，不能再扫描")
    code = (barcode or "").strip()
    if not code:
        raise ValueError("条码不能为空")

    previous = (db.query(InventoryScanORM)
                .filter(InventoryScanORM.session_id == session.id,
                        InventoryScanORM.barcode == code)
                .order_by(InventoryScanORM.id)
                .first())
    if previous is not None:
        row = InventoryScanORM(
            session_id=session.id, library_id=session.library_id,
            barcode=code, holding_id=previous.holding_id,
            result=INV_DUPLICATE, title=previous.title,
            call_no=previous.call_no, location=previous.location,
            reader_name=previous.reader_name, scanned_by=operator)
        db.add(row)
        db.flush()
        return row, f"条码 {code} 本次已扫过（第 {previous.id} 号记录）"

    holding = (db.query(HoldingORM)
               .filter(HoldingORM.library_id == session.library_id,
                       HoldingORM.barcode == code)
               .first())
    if holding is not None:
        title = _record_title(db, holding.record_id)
        reader_name = None
        if holding.status == STATUS_AVAILABLE:
            loan = (db.query(LoanORM)
                    .filter(LoanORM.holding_id == holding.id,
                            LoanORM.status == LOAN_ACTIVE)
                    .first())
            if loan is not None:
                result = INV_ON_LOAN
                reader_name = loan.reader_name
                message = (f"《{title or code}》当前外借中"
                           f"（{loan.reader_name or '未知读者'}），请先归还")
            else:
                result = INV_OK
                message = f"正常：{title or '（无题名）'}"
        else:
            result = INV_WITHDRAWN
            label = ("已遗失" if holding.status == "LOST"
                     else "停流通" if holding.status == "SUSPENDED"
                     else "已注销")
            message = f"《{title or code}》状态为{label}，不应在架"
        row = InventoryScanORM(
            session_id=session.id, library_id=session.library_id,
            barcode=code, holding_id=holding.id, result=result,
            title=title, call_no=holding.call_no, location=holding.location,
            reader_name=reader_name, scanned_by=operator)
        db.add(row)
        db.flush()
        return row, message

    other = (db.query(HoldingORM).filter(HoldingORM.barcode == code).first())
    if other is not None:
        lib = db.get(BookLibrary, other.library_id)
        title = _record_title(db, other.record_id)
        row = InventoryScanORM(
            session_id=session.id, library_id=session.library_id,
            barcode=code, holding_id=other.id, result=INV_OTHER_LIBRARY,
            title=title, call_no=other.call_no, location=other.location,
            scanned_by=operator)
        db.add(row)
        db.flush()
        return row, f"条码 {code} 属于「{lib.name if lib else other.library_id}」，不在本库"

    row = InventoryScanORM(
        session_id=session.id, library_id=session.library_id,
        barcode=code, result=INV_UNKNOWN, scanned_by=operator)
    db.add(row)
    db.flush()
    return row, f"本库没有条码 {code}"


# 结束盘点时快照里每张清单最多保留的行数。
# 快照（JSONB）要"定格"盘点结论：清单不存，之后借书/注销，历史盘点的「未扫到」
# 就跟着变了。但无上限地塞 10 万行 JSONB 会拖垮这一行（结束盘点变慢、整行进出
# 内存），所以设顶：中小学馆的正常量级（未扫到几百条）远碰不到；真超过时 stats
# 仍是**完整**口径（它单独算，不依赖清单行数），只有明细被截断，截断情况记录在
# 快照顶层的 `truncated` 键里。
SNAPSHOT_MAX_ROWS = 5000


def close_session(db: Session, *, session: InventorySessionORM,
                  operator: str | None) -> InventorySessionORM:
    if session.status == INV_CLOSED:
        raise ValueError("该盘点已经结束")
    # 先按"仍在盘"取清单，再落快照。之后借书 / 注销不会改变这次盘点的结论。
    stats = session_stats(db, session)
    snapshot: dict = {"stats": stats}
    truncated: dict[str, int] = {}
    for key, fn in (("missing", list_missing), ("on_loan", list_on_loan),
                    ("problems", list_problems)):
        total, rows = fn(db, session=session, q=None, page=1,
                         page_size=SNAPSHOT_MAX_ROWS)
        snapshot[key] = rows
        if total > len(rows):
            truncated[key] = total
    if truncated:
        snapshot["truncated"] = truncated
    session.result_snapshot = snapshot
    session.status = INV_CLOSED
    session.closed_at = _now()
    session.closed_by = operator
    db.flush()
    return session


def _snapshot_page(rows: list[dict], q: str | None, page: int,
                   page_size: int, keys: tuple[str, ...]) -> tuple[int, list[dict]]:
    if q and q.strip():
        needle = q.strip().lower()
        rows = [r for r in rows
                if any(needle in str(r.get(k) or "").lower() for k in keys)]
    total = len(rows)
    start = (page - 1) * page_size
    return total, rows[start:start + page_size]


def _with_shelf_no(db: Session, items: list[dict]) -> list[dict]:
    """给盘点清单补种级排架号（快照存于清洗前，也要按当前记录补全）。"""
    ids = {x["record_id"] for x in items if x.get("record_id")}
    shelves: dict[int, str] = {}
    if ids:
        for rid, shelf in (db.query(HoldingORM.record_id, HoldingORM.shelf_no)
                           .filter(HoldingORM.record_id.in_(ids))
                           .order_by(HoldingORM.id).all()):
            if shelf and rid not in shelves:
                shelves[rid] = shelf
    for item in items:
        item["shelf_no"] = shelves.get(item.get("record_id")) or ""
    return items


def list_missing(db: Session, *, session: InventorySessionORM, q: str | None,
                 page: int, page_size: int) -> tuple[int, list[dict]]:
    snap = _snapshot(session)
    if snap is not None and "missing" in snap:
        total, items = _snapshot_page(list(snap["missing"]), q, page, page_size,
                                      ("barcode", "title", "call_no", "location"))
        return total, _with_shelf_no(db, items)
    query = (_expected_holdings(db, session)
             .outerjoin(RecordORM, RecordORM.id == HoldingORM.record_id)
             # 题名跟主查询一起取：旧实现逐行 `_record_title()` 调 `db.get`，
             # 一页 50 行就是 50 次额外查询（N+1）。left join 后无记录时仍为 NULL，
             # 口径与 `_record_title` 一致。
             .add_columns(RecordORM.title_norm)
             .filter(~_found_scan(session.id), ~_active_loan()))
    if q and q.strip():
        s = like_escape(q.strip())
        query = query.filter(
            (HoldingORM.barcode.ilike(f"%{s}%", escape="\\"))
            | (HoldingORM.call_no.ilike(f"%{s}%", escape="\\"))
            | (HoldingORM.location.ilike(f"%{s}%", escape="\\"))
            | (RecordORM.title_norm.ilike(f"%{s}%", escape="\\")))
    total, rows = paginate(query.order_by(HoldingORM.call_no.nulls_last(),
                                          HoldingORM.barcode),
                           page, page_size)
    return total, _with_shelf_no(db, [{
        "holding_id": h.id, "record_id": h.record_id, "barcode": h.barcode,
        "title": title, "call_no": h.call_no,
        "location": h.location, "status": h.status,
    } for h, title in rows])


def list_on_loan(db: Session, *, session: InventorySessionORM, q: str | None,
                 page: int, page_size: int) -> tuple[int, list[dict]]:
    """应盘但没扫到、且当前外借的册（不是遗失，是还没还回来）。"""
    snap = _snapshot(session)
    if snap is not None and "on_loan" in snap:
        return _snapshot_page(list(snap["on_loan"]), q, page, page_size,
                              ("barcode", "title", "reader_name",
                               "reader_card_no"))
    query = (db.query(LoanORM)
             .join(HoldingORM, HoldingORM.id == LoanORM.holding_id)
             .filter(LoanORM.library_id == session.library_id,
                     LoanORM.status == LOAN_ACTIVE,
                     HoldingORM.status == STATUS_AVAILABLE,
                     ~_found_scan(session.id)))
    if session.started_at is not None:
        query = query.filter(HoldingORM.created_at <= session.started_at)
    if q and q.strip():
        s = like_escape(q.strip())
        query = query.filter(
            (LoanORM.barcode.ilike(f"%{s}%", escape="\\"))
            | (LoanORM.title.ilike(f"%{s}%", escape="\\"))
            | (LoanORM.reader_name.ilike(f"%{s}%", escape="\\"))
            | (LoanORM.reader_card_no.ilike(f"%{s}%", escape="\\")))
    total, rows = paginate(query.order_by(LoanORM.due_on, LoanORM.id),
                           page, page_size)
    today = _now().date()
    return total, [{
        "loan_id": x.id, "holding_id": x.holding_id, "record_id": x.record_id,
        "barcode": x.barcode, "title": x.title,
        "reader_id": x.reader_id, "reader_name": x.reader_name,
        "reader_card_no": x.reader_card_no, "reader_class": x.reader_class,
        "due_on": x.due_on.isoformat(),
        "days_overdue": max(0, (today - x.due_on).days),
    } for x in rows]


def list_problems(db: Session, *, session: InventorySessionORM, q: str | None,
                  page: int, page_size: int) -> tuple[int, list[dict]]:
    snap = _snapshot(session)
    if snap is not None and "problems" in snap:
        return _snapshot_page(list(snap["problems"]), q, page, page_size,
                              ("barcode", "title", "reader_name", "result_label"))
    query = (db.query(InventoryScanORM)
             .filter(InventoryScanORM.session_id == session.id,
                     InventoryScanORM.result.in_(INV_PROBLEM_RESULTS)))
    if q and q.strip():
        s = like_escape(q.strip())
        query = query.filter(
            (InventoryScanORM.barcode.ilike(f"%{s}%", escape="\\"))
            | (InventoryScanORM.title.ilike(f"%{s}%", escape="\\"))
            | (InventoryScanORM.reader_name.ilike(f"%{s}%", escape="\\")))
    total, rows = paginate(query.order_by(InventoryScanORM.id.desc()),
                           page, page_size)
    return total, [scan_dict(x) for x in rows]


def recent_scans(db: Session, session: InventorySessionORM, limit: int = 20) -> list[dict]:
    rows = (db.query(InventoryScanORM)
            .filter(InventoryScanORM.session_id == session.id)
            .order_by(InventoryScanORM.id.desc()).limit(limit).all())
    return [scan_dict(x) for x in rows]
