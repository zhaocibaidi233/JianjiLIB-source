# -*- coding: utf-8 -*-
"""馆藏处理：生成复本（条码 + 索书号）、查询、删除、注销/恢复；905 馆藏字段同步。"""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..core.volume import SHELF_NO_MAX_LEN
from ..db import get_db, like_escape
from ..models import (INACTIVE_STATUSES, LOAN_ACTIVE, STATUS_AVAILABLE,
                      STATUS_LOST, STATUS_SUSPENDED, STATUS_WITHDRAWN,
                      CallNoCounter, HoldingORM, LoanORM, RecordORM)
from ..services.batch import (barcode_key, check_barcode_digits, create_holdings,
                              next_withdraw_no, raise_withdraw_seq)
from ..services.circulation import (active_loan_for_holding,
                                    active_loan_holding_ids, lock_record)
from ..services import item_lifecycle, record_projection
from ..services import reservations as reservation_service
from ..services.sorting import apply_sort
from ..services.paging import paginate
from ..core.route_meta import api
from ..schemas import HoldingPage

router = APIRouter(tags=["holdings"])

# 条码总览 / 注销清单**可排序的列**（白名单，见 `services/sorting.py`）。
# 键与前端表格列的 `prop` 对齐；题名是 join 来的（`RecordORM.title_norm`）。
HOLDING_SORTS = {
    "barcode": HoldingORM.barcode,
    "location": HoldingORM.location,
    "call_no": HoldingORM.call_no,
    "status": HoldingORM.status,
    "library_id": HoldingORM.library_id,
    "created_at": HoldingORM.created_at,
    "title": RecordORM.title_norm,
}
# 注销清单：默认按注销时间倒序（原行为），可改按条码/原因/注销号…
WITHDRAWN_SORTS = {
    "barcode": HoldingORM.barcode,
    "location": HoldingORM.location,
    "withdrawn_at": HoldingORM.withdrawn_at,
    "withdraw_reason": HoldingORM.withdraw_reason,
    "withdraw_no": HoldingORM.withdraw_no,
    "title": RecordORM.title_norm,
}


def _lock_holdings(db: Session, ids: list[int]) -> dict[int, HoldingORM]:
    record_ids = {rid for (rid,) in db.query(HoldingORM.record_id).filter(HoldingORM.id.in_(ids)).all()}
    for rid in sorted(record_ids):
        lock_record(db, rid)
    rows = (db.query(HoldingORM).filter(HoldingORM.id.in_(ids)).order_by(HoldingORM.id)
            .populate_existing().with_for_update().all())
    if any(row.record_id not in record_ids for row in rows):
        raise HTTPException(409, "馆藏所属书目已变更，请刷新后重试")
    return {row.id: row for row in rows}


def _lib_of(db: Session, library_id: int | None, record_id: int | None) -> int:
    """确定条码所属图书库：记录所属库 > 显式 library_id > 默认库。"""
    from .book_libraries import resolve_library
    if record_id:
        orm = db.get(RecordORM, record_id)
        if orm is not None:
            return orm.library_id
    return resolve_library(db, library_id).id


def _max_barcode(q) -> tuple[int, str | None]:
    """一组条码里**数值部分最大**的那个。

    「最后使用」在本项目里一直是"数值最大"而不是"时间上最后一条"
    （`/holdings/last-barcode` 原本的措辞就是"数值部分最大者"），这里沿用同一含义。
    """
    best_num, best_code = -1, None
    for (bc,) in q.all():
        m = re.search(r"(\d+)$", bc or "")
        if m:
            n = int(m.group(1))
            if n > best_num:
                best_num, best_code = n, bc
    return best_num, best_code


@router.get("/holdings/last-barcode")
@api(module="barcodes")
def last_barcode(request: Request, library_id: int | None = None,
                 scope: str = "operator", db: Session = Depends(get_db)):
    """条码续号（`+N`）的基准号。按图书库隔离——多校管理下各校条码各自编号。

    `scope`（**默认 `operator`**）：

    - `operator`：**当前编目员自己录过**的条码里数值最大者。
      多个编目员并行录入时各续各的号段 —— 用全库最大值会让 B 一按 `+1`
      就跳进 A 的号段。归属按**记录的编目员**（`records.created_by`）算，
      因为条码本身不记人。
    - `library`：全库最大值（旧口径）。

    ⚠️ **该编目员还没录过 → 回落到全库最大值**（否则新编目员一上手 `+N` 就报
    "尚无历史条码"），并如实回 `scope_used` 说明用的是哪个。
    ⚠️ 顺带回 `library_last` / `library_num`（**全库**最大值）：本人号段可能低于
    全库最大值（典型来源是**导入的记录没有编目员归属** —— 只有 `POST /records`
    会写 `created_by`），界面据此提示"本库已用到 X"，免得续号撞上去。
    """
    lib_id = _lib_of(db, library_id, None)
    base = db.query(HoldingORM.barcode).filter(
        HoldingORM.library_id == lib_id, HoldingORM.barcode.isnot(None))

    lib_num, lib_code = _max_barcode(base)
    who = None if scope == "library" else operator_of(request)
    mine_num, mine_code = (-1, None)
    if who:
        mine_num, mine_code = _max_barcode(
            base.join(RecordORM, RecordORM.id == HoldingORM.record_id)
                .filter(RecordORM.created_by == who))

    used = "operator" if mine_code is not None else "library"
    code = mine_code if mine_code is not None else lib_code
    num = mine_num if mine_code is not None else lib_num
    return {"last": code, "num": num if num >= 0 else None,
            "library_id": lib_id, "scope_used": used, "operator": who,
            "library_last": lib_code, "library_num": lib_num if lib_num >= 0 else None}


@router.get("/holdings/preview")
@api(module="barcodes")
def preview_barcodes(count: int = Query(1, ge=1, le=99),
                     start: str | None = None,
                     record_id: int | None = None,
                     library_id: int | None = None,
                     db: Session = Depends(get_db)):
    """条码预览（不消耗号段）：自动流水 peek 或手动起始号递增，附冲突检测。

    流水与冲突检测都限定在记录所属图书库内。
    """
    import re as _re
    lib_id = _lib_of(db, library_id, record_id)
    if start:
        m = _re.fullmatch(r"(\D*?)(\d+)", start.strip())
        if not m:
            raise HTTPException(400, f"起始条码格式不支持：{start}")
        prefix, num = m.group(1), m.group(2)
        width, base = len(num), int(num)
        codes = [f"{prefix}{base + i:0{width}d}" for i in range(count)]
    else:
        from ..services.batch import _barcode_digits
        counter = db.get(CallNoCounter, barcode_key(lib_id))
        cur = counter.current_seq if counter else 0
        digits = _barcode_digits(db)
        codes = [f"{cur + i + 1:0{digits}d}" for i in range(count)]
    conflicts = [c for c in codes
                 if db.query(HoldingORM.id).filter(HoldingORM.library_id == lib_id,
                                                   HoldingORM.barcode == c).first()]
    return {"codes": codes, "conflicts": conflicts, "library_id": lib_id}


@router.get("/holdings", response_model=HoldingPage)
@api(module="barcodes")
def list_holdings(
    library_id: int | None = None,
    q: str | None = None,
    location: str | None = None,
    record_id: int | None = None,
    status: str = Query("active", description="active（默认，仅在架）| all | 具体状态"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str | None = None, order: str | None = None,
    db: Session = Depends(get_db),
):
    """跨记录的条码总览（条码管理页用）。

    与 `/records/{id}/holdings` 的区别：这是全馆视角，支持按图书库、条码片段、
    馆藏地点、状态筛选，供批量核对、改号与清理。

    `sort` / `order`：点击表头排序（白名单见 `HOLDING_SORTS`）。**不给 sort 时
    保持原默认顺序** `library_id, barcode`（`then` 参数原样保留，别静默改掉）。

    默认只看**在架**册——注销/遗失的册已不属于馆藏，混在列表里会让人误以为还在。
    """
    query = (db.query(HoldingORM, RecordORM)
             .join(RecordORM, RecordORM.id == HoldingORM.record_id))
    if library_id:
        query = query.filter(HoldingORM.library_id == library_id)
    if record_id:
        query = query.filter(HoldingORM.record_id == record_id)
    if q:
        query = query.filter(HoldingORM.barcode.ilike(f"%{like_escape(q.strip())}%", escape="\\"))
    if location:
        query = query.filter(HoldingORM.location == location)
    if status == "available":
        on_loan_ids = db.query(LoanORM.holding_id).filter(
            LoanORM.status == LOAN_ACTIVE)
        query = query.filter(HoldingORM.status == STATUS_AVAILABLE,
                             ~HoldingORM.id.in_(on_loan_ids))
    elif status == "on_loan":
        # 借出中的册 holdings.status 仍是 AVAILABLE，
        # 「借出」要按 loans 的 ACTIVE 行筛，不能按 holdings.status。
        query = query.filter(HoldingORM.id.in_(
            db.query(LoanORM.holding_id).filter(LoanORM.status == LOAN_ACTIVE)))
    elif status != "all":
        query = query.filter(HoldingORM.status ==
                             (STATUS_AVAILABLE if status == "active" else status))
    total, rows = paginate(apply_sort(query, HOLDING_SORTS, sort, order,
                                      default="library_id", default_order="asc",
                                      tiebreak=HoldingORM.id, then=("barcode",)),
                           page, page_size)
    loan_map: dict[int, LoanORM] = {}
    if rows:
        for loan in (db.query(LoanORM)
                     .filter(LoanORM.holding_id.in_([h.id for h, _ in rows]),
                             LoanORM.status == LOAN_ACTIVE)):
            loan_map[loan.holding_id] = loan
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [{
            "id": h.id, "barcode": h.barcode, "record_id": h.record_id,
            "library_id": h.library_id, "location": h.location,
            "call_no": h.call_no, "volume": h.volume, "status": h.status,
            "withdraw_no": h.withdraw_no, "withdraw_reason": h.withdraw_reason,
            "withdrawn_at": str(h.withdrawn_at) if h.withdrawn_at else None,
            "withdrawn_by": h.withdrawn_by,
            "title": r.title_norm, "isbn": r.isbn_norm, "class_no": r.class_no,
            "shelf_no": h.shelf_no, "created_at": str(h.created_at) if h.created_at else None,
            "on_loan": h.id in loan_map,
            "loan_id": loan_map[h.id].id if h.id in loan_map else None,
            "reader_name": loan_map[h.id].reader_name if h.id in loan_map else None,
            "reader_card_no": (loan_map[h.id].reader_card_no
                               if h.id in loan_map else None),
            "due_on": (loan_map[h.id].due_on.isoformat()
                       if h.id in loan_map else None),
        } for h, r in rows],
    }


@router.get("/holdings/withdrawn")
@api(module="barcodes")
def list_withdrawn(
    library_id: int | None = None,
    reason: str | None = None,
    # 用原生 date 类型：非法日期由 FastAPI 直接 422，不再 DataError 500
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str | None = None, order: str | None = None,
    db: Session = Depends(get_db),
):
    """注销清单（对标旧版系统「图书注销分类统计」的可查明细）。

    支持按图书库 / 注销原因 / 注销时间段 / 条码或题名片段筛选，供核对与上报。
    `sort` / `order`：点击表头排序（白名单见 `WITHDRAWN_SORTS`）；不给时保持原默认
    `withdrawn_at DESC, id DESC`（tie-breaker 传表达式，逐字保留）。
    """
    query = (db.query(HoldingORM, RecordORM)
             .join(RecordORM, RecordORM.id == HoldingORM.record_id)
             .filter(HoldingORM.status.in_(INACTIVE_STATUSES)))
    if library_id:
        query = query.filter(HoldingORM.library_id == library_id)
    if reason:
        query = query.filter(HoldingORM.withdraw_reason == reason)
    if date_from:
        query = query.filter(HoldingORM.withdrawn_at >= f"{date_from.isoformat()} 00:00:00+08")
    if date_to:
        query = query.filter(HoldingORM.withdrawn_at <= f"{date_to.isoformat()} 23:59:59+08")
    if q:
        s = q.strip()
        query = query.filter(HoldingORM.barcode.ilike(f"%{like_escape(s)}%", escape="\\")
                             | RecordORM.title_norm.ilike(f"%{like_escape(s)}%", escape="\\"))
    total, rows = paginate(apply_sort(query, WITHDRAWN_SORTS, sort, order,
                                      default="withdrawn_at", default_order="desc",
                                      tiebreak=HoldingORM.id.desc()),
                           page, page_size)
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [{
            "id": h.id, "barcode": h.barcode, "record_id": h.record_id,
            "library_id": h.library_id, "location": h.location,
            "call_no": h.call_no, "status": h.status,
            "withdraw_no": h.withdraw_no, "withdraw_reason": h.withdraw_reason,
            "withdrawn_at": str(h.withdrawn_at) if h.withdrawn_at else None,
            "withdrawn_by": h.withdrawn_by, "remark": h.remark,
            "title": r.title_norm, "isbn": r.isbn_norm, "class_no": r.class_no,
            "shelf_no": h.shelf_no,
        } for h, r in rows],
    }


class WithdrawIn(BaseModel):
    ids: list[int]
    reason: str
    withdraw_no: str | None = None   # 留空则按库自动生成 ZX{库}-{流水}
    approval_ref: str | None = None  # 资产处置审批文号/依据
    destination: str | None = None   # 处置去向（报废库/另库等）
    by: str | None = None            # 留空则取请求头 X-Operator


class RestoreIn(BaseModel):
    ids: list[int]


class SuspendIn(BaseModel):
    ids: list[int]
    reason: str
    review_batch_no: str | None = None
    approval_ref: str | None = None
    destination: str | None = None
    by: str | None = None


class ReleaseIn(BaseModel):
    ids: list[int]
    by: str | None = None


@router.post("/holdings/withdraw")
@api(module="barcodes")
def withdraw_holdings(payload: WithdrawIn, request: Request,
                      db: Session = Depends(get_db)):
    """批量注销（剔旧 / 损毁 / 遗失 / 其他）。

    采用**逐条处理**而非全有全无：馆员扫一批书，不该因为其中一本外借就整批失败。
    返回成功与失败明细，逐条给出原因。
    """
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(400, "注销原因不能为空（剔旧 / 损毁 / 遗失 / 其他）")
    ids = [int(i) for i in (payload.ids or [])]
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    if len(ids) > 500:
        raise HTTPException(400, "单次最多注销 500 册")

    operator = (payload.by or operator_of(request) or "").strip() or None
    explicit_no = (payload.withdraw_no or "").strip() or None
    no_by_lib: dict[int, str] = {}

    def _no_for(lib_id: int) -> str:
        """一次操作一个注销号；显式给出则整批沿用。"""
        if explicit_no:
            return explicit_no
        if lib_id not in no_by_lib:
            no_by_lib[lib_id] = next_withdraw_no(db, lib_id)
        return no_by_lib[lib_id]

    succeeded: list[dict] = []
    failed: list[dict] = []
    touched: set[int] = set()
    holdings = _lock_holdings(db, ids)
    on_loan = active_loan_holding_ids(db, ids)

    for hid in ids:
        h = holdings.get(hid)
        if h is None:
            failed.append({"id": hid, "reason": "馆藏不存在"})
            continue
        rec = db.get(RecordORM, h.record_id)
        title = rec.title_norm if rec else None
        try:
            if h.status == STATUS_WITHDRAWN:
                raise ValueError("该册已注销")
            if h.status == STATUS_LOST:
                raise ValueError("该册已按遗失处理，不能再注销；请先恢复或走遗失流程")
            if h.id in on_loan:
                raise ValueError("该册正在外借中，不能注销，请先办理归还")
            result = item_lifecycle.withdraw_item(
                db, h, reason=reason, withdraw_no=_no_for(h.library_id),
                approval_ref=payload.approval_ref,
                destination=payload.destination, operator=operator)
            touched.update(result.affected_record_ids)
            succeeded.append({"id": h.id, "barcode": h.barcode, "title": title,
                              "withdraw_no": h.withdraw_no})
        except ValueError as e:
            failed.append({"id": h.id, "barcode": h.barcode, "title": title,
                           "reason": str(e)})

    # 手工指定注销号同样要抬该库的注销号计数器，否则下次自动号会撞上手工号
    # （与 set_book_no 抬书次号同理）。号形如 ZX{库}-{流水}，库 id 就在号里。
    if explicit_no and succeeded:
        m = re.fullmatch(r"ZX(\d+)-(\d+)", explicit_no)
        if m:
            raise_withdraw_seq(db, int(m.group(1)), int(m.group(2)))

    db.flush()
    record_projection.rebuild_905_many(db, touched)
    reservation_service.cancel_if_unavailable(
        db, touched, "馆藏已注销", operator=operator)
    db.commit()
    return {"succeeded": succeeded, "failed": failed,
            "withdrawn": len(succeeded), "skipped": len(failed)}


@router.post("/holdings/restore")
@api(module="barcodes")
def restore_holdings(payload: RestoreIn, request: Request,
                     db: Session = Depends(get_db)):
    """批量恢复：把已注销 / 已遗失的册恢复为在架（对标旧版系统「图书恢复」）。

    只允许从 WITHDRAWN / LOST 恢复；已在架的册报错但不影响其余条。
    """
    operator = operator_of(request)
    ids = [int(i) for i in (payload.ids or [])]
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    if len(ids) > 500:
        raise HTTPException(400, "单次最多恢复 500 册")

    succeeded: list[dict] = []
    failed: list[dict] = []
    touched: set[int] = set()
    holdings = _lock_holdings(db, ids)
    for hid in ids:
        h = holdings.get(hid)
        if h is None:
            failed.append({"id": hid, "reason": "馆藏不存在"})
            continue
        rec = db.get(RecordORM, h.record_id)
        title = rec.title_norm if rec else None
        try:
            if h.status == STATUS_AVAILABLE:
                raise ValueError("该册已在架，无需恢复")
            if h.status not in INACTIVE_STATUSES:
                raise ValueError(f"当前状态为「{h.status}」，只有已注销或已遗失的馆藏才能恢复")
            result = item_lifecycle.restore_item(db, h, operator=operator)
            touched.update(result.affected_record_ids)
            succeeded.append({"id": h.id, "barcode": h.barcode, "title": title})
        except ValueError as e:
            failed.append({"id": h.id, "barcode": h.barcode, "title": title,
                           "reason": str(e)})
    db.flush()
    record_projection.rebuild_905_many(db, touched)
    # _lock_holdings 已按记录升序加锁；恢复的在架册同样要推进预约队列。
    for rid in sorted(touched):
        reservation_service.promote_ready(db, rid)
    db.commit()
    return {"succeeded": succeeded, "failed": failed,
            "restored": len(succeeded), "skipped": len(failed)}


@router.post("/holdings/suspend")
@api(module="barcodes", capability="catalog.item.suspend")
def suspend_holdings(payload: SuspendIn, request: Request,
                     db: Session = Depends(get_db)):
    """批量停流通/另库保存；不是注销，905 会移除该册。"""
    ids = [int(i) for i in (payload.ids or [])]
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    if len(ids) > 500:
        raise HTTPException(400, "单次最多停流通 500 册")
    operator = payload.by or operator_of(request)
    succeeded: list[dict] = []
    failed: list[dict] = []
    touched: set[int] = set()
    holdings = _lock_holdings(db, ids)
    for hid in ids:
        h = holdings.get(hid)
        if h is None:
            failed.append({"id": hid, "reason": "馆藏不存在"})
            continue
        try:
            result = item_lifecycle.suspend_item(
                db, h, reason=payload.reason,
                review_batch_no=payload.review_batch_no,
                approval_ref=payload.approval_ref,
                destination=payload.destination, operator=operator)
            touched.update(result.affected_record_ids)
            rec = db.get(RecordORM, h.record_id)
            succeeded.append({"id": h.id, "barcode": h.barcode,
                              "title": rec.title_norm if rec else None})
        except ValueError as e:
            failed.append({"id": h.id, "barcode": h.barcode, "reason": str(e)})
    db.flush()
    record_projection.rebuild_905_many(db, touched)
    reservation_service.cancel_if_unavailable(
        db, touched, "馆藏已停流通", operator=operator)
    db.commit()
    return {"succeeded": succeeded, "failed": failed,
            "suspended": len(succeeded), "skipped": len(failed)}


@router.post("/holdings/release")
@api(module="barcodes", capability="catalog.item.release")
def release_holdings(payload: ReleaseIn, request: Request,
                     db: Session = Depends(get_db)):
    """批量解除停流通：SUSPENDED → AVAILABLE，905 与预约队列恢复。"""
    ids = [int(i) for i in (payload.ids or [])]
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    if len(ids) > 500:
        raise HTTPException(400, "单次最多释放 500 册")
    operator = payload.by or operator_of(request)
    succeeded: list[dict] = []
    failed: list[dict] = []
    touched: set[int] = set()
    holdings = _lock_holdings(db, ids)
    for hid in ids:
        h = holdings.get(hid)
        if h is None:
            failed.append({"id": hid, "reason": "馆藏不存在"})
            continue
        try:
            result = item_lifecycle.release_item(db, h, operator=operator)
            touched.update(result.affected_record_ids)
            succeeded.append({"id": h.id, "barcode": h.barcode})
        except ValueError as e:
            failed.append({"id": h.id, "barcode": h.barcode, "reason": str(e)})
    db.flush()
    record_projection.rebuild_905_many(db, touched)
    for rid in sorted(touched):
        reservation_service.promote_ready(db, rid, operator=operator)
    db.commit()
    return {"succeeded": succeeded, "failed": failed,
            "released": len(succeeded), "skipped": len(failed)}


@router.get("/holdings/locations")
@api(module="barcodes")
def holding_locations(library_id: int | None = None, db: Session = Depends(get_db)):
    """实际用过的馆藏地点（去重），供筛选下拉——比设置里的预设列表更贴近现状。"""
    q = db.query(HoldingORM.location).filter(HoldingORM.location.isnot(None))
    if library_id:
        q = q.filter(HoldingORM.library_id == library_id)
    return {"locations": sorted({r[0] for r in q.distinct() if r[0]})}


class HoldingsIn(BaseModel):
    copies: int = 1
    location: str | None = None
    barcode_start: str | None = None    # 手动条码起始号（递增），空则自动流水
    barcodes: list[str] | None = None   # 手动完整条码列表（优先于 barcode_start）
    book_no: str | None = None          # 手动书次号（覆盖自动书次号）


class HoldingUpdate(BaseModel):
    barcode: str | None = None
    location: str | None = None
    shelf_no: str | None = None


class HoldingBatchIn(BaseModel):
    ids: list[int]
    location: str | None = None         # 提供则批量修改馆藏地点


class HoldingIdsIn(BaseModel):
    """批量删除的载荷（Pydantic 校验：非数字 id → 422，而非裸 dict 时代的 500）。"""

    ids: list[int]


def _json(h: HoldingORM) -> dict:
    return {"id": h.id, "record_id": h.record_id, "library_id": h.library_id,
            "barcode": h.barcode, "accession_no": h.accession_no,
            "acquired_at": (h.acquired_at.isoformat() if h.acquired_at else None),
            "price": float(h.price) if h.price is not None else None,
            "source": h.source,
            "location": h.location, "call_no": h.call_no,
            "volume": h.volume, "status": h.status,
            "shelf_no": h.shelf_no,
            "suspended_at": (h.suspended_at.isoformat() if h.suspended_at else None),
            "suspended_by": h.suspended_by, "suspended_reason": h.suspended_reason,
            "review_batch_no": h.review_batch_no, "approval_ref": h.approval_ref,
            "destination": h.destination,
            "withdraw_no": h.withdraw_no, "withdraw_reason": h.withdraw_reason,
            "withdrawn_at": str(h.withdrawn_at) if h.withdrawn_at else None,
            "withdrawn_by": h.withdrawn_by}


@router.put("/holdings/{holding_id}")
@api(module="barcodes")
def update_holding(holding_id: int, payload: HoldingUpdate, db: Session = Depends(get_db)):
    """编辑馆藏（条码/馆藏地点），同步更新记录 905。

    已注销 / 已遗失的册不允许直接改条码或地点——它们已不属于馆藏，
    改了会让注销账与实物对不上。要先恢复再改。
    """
    h = _lock_holdings(db, [holding_id]).get(holding_id)
    if h is None:
        raise HTTPException(404, "馆藏不存在")
    if h.status == STATUS_SUSPENDED:
        raise HTTPException(400, "该册已停流通，请先释放或注销后再修改")
    if h.status in INACTIVE_STATUSES:
        raise HTTPException(400, f"该册状态为「{h.status}」（已注销 / 已遗失），"
                                 f"请先恢复为在架再修改")
    if payload.barcode is not None and payload.barcode.strip() and payload.barcode.strip() != h.barcode:
        # 借阅台账存了条码快照；在借期间改号会让台账与实物对不上
        if active_loan_for_holding(db, holding_id) is not None:
            raise HTTPException(400, "该册正在外借中，不能修改条码（请先归还）")
        old_bc = h.barcode
        new_bc = payload.barcode.strip()
        # 位数必须是「全局设置 → 条码位数」里允许的位数之一（硬拦，与新增条码同一规则）
        try:
            check_barcode_digits(db, new_bc)
        except ValueError as e:
            raise HTTPException(400, str(e))
        # 查重限定本库（跨库允许同号）
        if db.query(HoldingORM.id).filter(HoldingORM.library_id == h.library_id,
                                          HoldingORM.barcode == new_bc,
                                          HoldingORM.id != holding_id).first():
            raise HTTPException(400, f"条码已存在（本库）：{new_bc}")
        h.barcode = new_bc
        # 登录号默认等于条码；只有它仍是旧条码时才跟着改，独立登录号不动。
        if h.accession_no and h.accession_no == old_bc:
            h.accession_no = new_bc
    if payload.location is not None:
        h.location = payload.location.strip() or None
    if payload.shelf_no is not None:
        shelf = payload.shelf_no.strip()
        if len(shelf) > SHELF_NO_MAX_LEN:
            raise HTTPException(400, f"排架号最多 {SHELF_NO_MAX_LEN} 个字符")
        h.shelf_no = shelf or None
    db.flush()
    record_projection.rebuild_905(db, h.record_id)
    db.commit()
    return _json(h)


@router.post("/records/{record_id}/holdings")
@api(module="records")
def add_holdings(record_id: int, payload: HoldingsIn, db: Session = Depends(get_db)):
    orm = db.get(RecordORM, record_id, with_for_update=True, populate_existing=True)
    if orm is None:
        raise HTTPException(404, "记录不存在")
    try:
        holdings = create_holdings(db, orm, payload.copies, payload.location,
                                   barcode_start=payload.barcode_start,
                                   barcodes=payload.barcodes,
                                   book_no=payload.book_no)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 新册上架即刻释放预约空位：与归还同一口径（在架可借 - 已 READY）。
    reservation_service.promote_ready(db, record_id)
    db.commit()
    return {"created": len(holdings),
            "holdings": [_json(h) for h in holdings]}


@router.get("/records/{record_id}/holdings")
@api(module="records")
def list_record_holdings(record_id: int, db: Session = Depends(get_db)):
    """某条书目记录下的全部册。

    名字不能叫 `list_holdings` —— 本模块上面已经有一个「按条件检索全馆册」的
    同名函数（`/holdings`），重名会让**后定义的把先定义的模块级名字覆盖掉**。
    路由已由装饰器登记，运行时不受影响，但调试时 `holdings.list_holdings`
    指向的是这一个，很容易看错（ruff 的 F811 就是抓这个）。
    """
    rows = (db.query(HoldingORM).filter(HoldingORM.record_id == record_id)
            .order_by(HoldingORM.id).all())
    return {"holdings": [_json(h) for h in rows]}


@router.delete("/holdings/{holding_id}")
@api(module="barcodes")
def delete_holding(holding_id: int, db: Session = Depends(get_db)):
    h = _lock_holdings(db, [holding_id]).get(holding_id)
    if h is None:
        raise HTTPException(404, "馆藏不存在")
    if active_loan_for_holding(db, holding_id) is not None:
        raise HTTPException(400, "该册正在外借中，不能删除（请先归还）")
    if h.status == STATUS_SUSPENDED:
        raise HTTPException(400, "该册已停流通，不能删除；请先释放或注销")
    # 图书库以条码号为核心：不允许删掉某条记录的最后一条条码，
    # 否则会留下"有条目无条码"的记录，违反业务规则。
    remain = (db.query(HoldingORM)
              .filter(HoldingORM.record_id == h.record_id,
                      HoldingORM.id != holding_id).count())
    if remain == 0:
        raise HTTPException(400, "图书库要求每条记录至少保留一个条码；"
                                 "如需移除请直接删除该记录")
    record_id = h.record_id
    db.delete(h)
    db.flush()
    record_projection.rebuild_905(db, record_id)
    reservation_service.cancel_if_unavailable(
        db, {record_id}, "馆藏已全部移除")
    db.commit()
    return {"deleted": holding_id}


@router.post("/holdings/batch-delete")
@api(module="barcodes")
def batch_delete_holdings(payload: HoldingIdsIn, db: Session = Depends(get_db)):
    """批量删除馆藏，涉及记录的 905 各自重建。"""
    ids = payload.ids
    if not ids:
        raise HTTPException(400, "ids 不能为空")

    # 先整体校验：任何一条记录被删空 / 有册在借都要拒绝整批，避免删一半留一半
    targets = list(_lock_holdings(db, ids).values())
    on_loan = active_loan_holding_ids(db, [h.id for h in targets])
    if on_loan:
        sample = "、".join(h.barcode or str(h.id) for h in targets if h.id in on_loan)
        raise HTTPException(400, f"以下 {len(on_loan)} 册正在外借中，不能删除"
                                 f"（请先归还）：{sample[:200]}")
    suspended = [h for h in targets if h.status == STATUS_SUSPENDED]
    if suspended:
        raise HTTPException(
            400, f"以下 {len(suspended)} 册已停流通，不能删除；"
                 f"请先释放或注销")
    per_rec: dict[int, int] = {}
    for h in targets:
        per_rec[h.record_id] = per_rec.get(h.record_id, 0) + 1
    emptied = []
    for rid, n in per_rec.items():
        total = db.query(HoldingORM).filter(HoldingORM.record_id == rid).count()
        if total - n < 1:
            emptied.append(rid)
    if emptied:
        raise HTTPException(400, f"以下 {len(emptied)} 条记录删除后将不再有条码，"
                                 f"整批操作已取消：{emptied[:10]}")

    record_ids = set()
    deleted = 0
    for h in targets:
        record_ids.add(h.record_id)
        db.delete(h)
        deleted += 1
    db.flush()
    record_projection.rebuild_905_many(db, record_ids)
    reservation_service.cancel_if_unavailable(
        db, record_ids, "馆藏已全部移除")
    db.commit()
    return {"deleted": deleted}


@router.post("/holdings/batch-location")
@api(module="barcodes")
def batch_update_location(payload: HoldingBatchIn, db: Session = Depends(get_db)):
    """批量修改馆藏地点。"""
    ids = payload.ids
    location = (payload.location or "").strip() or None
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    record_ids = set()
    holdings = _lock_holdings(db, ids)
    for hid in ids:
        h = holdings.get(hid)
        if h is None:
            continue
        h.location = location
        record_ids.add(h.record_id)
    db.flush()
    record_projection.rebuild_905_many(db, record_ids)
    db.commit()
    return {"updated": len(ids)}
