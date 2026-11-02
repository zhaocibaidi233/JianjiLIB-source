# -*- coding: utf-8 -*-
"""读者档案：增改查、状态、Excel 导入/模板。

**读者不是登录账号**：绝不写 `users` 表（那会让 `auth_enabled()` 误判、整站
被迫登录）。读者只有状态（正常 / 暂停 / 注销），**没有硬删除**。
"""
from __future__ import annotations

import io
from datetime import date

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Request, UploadFile)
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..db import get_db, like_escape
from ..models import (LOAN_ACTIVE, LOAN_LOST, LOAN_RETURNED, READER_ACTIVE,
                      READER_STATUSES, READER_TYPES, LoanORM, ReaderORM)
from ..services import reservations as reservation_service
from ..services import portal_auth, reader_privacy
from ..services.circulation import (active_loans_of_reader, loan_dict,
                                    overdue_count, reader_compensation_summary,
                                    reader_dict, reader_fine_summary,
                                    rules_value, type_rule)
from ..services.sorting import apply_sort
from ..services.paging import paginate
from ..core.route_meta import api
from ..schemas import ReaderPage
from .book_libraries import resolve_library
from .common import xlsx_response

router = APIRouter(prefix="/readers", tags=["readers"])

MAX_IMPORT_ROWS = 5000
MAX_IMPORT_BYTES = 10 * 1024 * 1024

READER_SORTS = {
    "card_no": ReaderORM.card_no,
    "name": ReaderORM.name,
    "reader_type": ReaderORM.reader_type,
    "grade": ReaderORM.grade,
    "class_name": ReaderORM.class_name,
    "status": ReaderORM.status,
    "created_at": ReaderORM.created_at,
    "id": ReaderORM.id,
}


def _require_type(value: str | None) -> str:
    v = (value or "student").strip()
    if v not in READER_TYPES:
        raise HTTPException(400, f"读者类型只能是 {' / '.join(READER_TYPES)}")
    return v


def _require_status(value: str | None) -> str:
    v = (value or READER_ACTIVE).strip().upper()
    if v not in READER_STATUSES:
        raise HTTPException(400, f"读者状态只能是 {' / '.join(READER_STATUSES)}")
    return v


def _clean(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v[:max_len] or None


def _check_card_no(db: Session, library_id: int, card_no: str,
                   exclude_id: int | None = None) -> None:
    q = db.query(ReaderORM).filter(ReaderORM.library_id == library_id,
                                   ReaderORM.card_no == card_no)
    if exclude_id is not None:
        q = q.filter(ReaderORM.id != exclude_id)
    if q.first() is not None:
        raise HTTPException(409, f"借书证号已存在（本库）：{card_no}")


class ReaderIn(BaseModel):
    library_id: int | None = None
    card_no: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    reader_type: str = "student"
    grade: str | None = Field(None, max_length=32)
    class_name: str | None = Field(None, max_length=64)
    phone: str | None = Field(None, max_length=32)
    status: str = READER_ACTIVE
    note: str | None = Field(None, max_length=256)


class ReaderUpdate(BaseModel):
    card_no: str | None = Field(None, min_length=1, max_length=32)
    name: str | None = Field(None, min_length=1, max_length=64)
    reader_type: str | None = None
    grade: str | None = Field(None, max_length=32)
    class_name: str | None = Field(None, max_length=64)
    phone: str | None = Field(None, max_length=32)
    status: str | None = None
    note: str | None = Field(None, max_length=256)


class ReaderStatusIn(BaseModel):
    status: str


class ReaderAccountIn(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class ReaderBatchStatusIn(BaseModel):
    ids: list[int]
    status: str


class ConsentIn(BaseModel):
    consent: bool = True
    by: str | None = None


class AnonymizeIn(BaseModel):
    by: str | None = None


@router.get("", response_model=ReaderPage)
@api(module="readers")
def list_readers(
    library_id: int | None = None,
    q: str | None = None,
    reader_type: str | None = None,
    status: str | None = None,
    grade: str | None = None,
    class_name: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str | None = None, order: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(ReaderORM)
    if library_id:
        query = query.filter(ReaderORM.library_id == library_id)
    if reader_type:
        query = query.filter(ReaderORM.reader_type == reader_type)
    if status and status != "all":
        query = query.filter(ReaderORM.status == status)
    if grade:
        query = query.filter(ReaderORM.grade == grade)
    if class_name:
        query = query.filter(ReaderORM.class_name == class_name)
    if q:
        s = like_escape(q.strip())
        query = query.filter(or_(
            ReaderORM.card_no.ilike(f"%{s}%", escape="\\"),
            ReaderORM.name.ilike(f"%{s}%", escape="\\"),
            ReaderORM.class_name.ilike(f"%{s}%", escape="\\")))
    total, rows = paginate(apply_sort(query, READER_SORTS, sort, order,
                                      default="created_at", default_order="desc",
                                      tiebreak=ReaderORM.id),
                           page, page_size)
    rules = rules_value(db)
    ids = [r.id for r in rows]
    active_counts: dict[int, int] = {}
    overdue_counts: dict[int, int] = {}
    if ids:
        active_counts = dict(
            db.query(LoanORM.reader_id, func.count())
            .filter(LoanORM.reader_id.in_(ids),
                    LoanORM.status == LOAN_ACTIVE)
            .group_by(LoanORM.reader_id).all())
        overdue_counts = dict(
            db.query(LoanORM.reader_id, func.count())
            .filter(LoanORM.reader_id.in_(ids),
                    LoanORM.status == LOAN_ACTIVE,
                    LoanORM.due_on < date.today())
            .group_by(LoanORM.reader_id).all())
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [{**reader_dict(r, rules),
                   "active_count": active_counts.get(r.id, 0),
                   "overdue_count": overdue_counts.get(r.id, 0)}
                  for r in rows],
    }


@router.get("/facets")
@api(module="readers")
def reader_facets(library_id: int | None = None, db: Session = Depends(get_db)):
    """筛选下拉：年级 / 班级 / 各类型与状态计数。"""
    base = db.query(ReaderORM)
    if library_id:
        base = base.filter(ReaderORM.library_id == library_id)

    def _distinct(col):
        return sorted({v for (v,) in base.with_entities(col).distinct().all() if v})

    type_counts = dict(base.with_entities(ReaderORM.reader_type, func.count())
                       .group_by(ReaderORM.reader_type).all())
    status_counts = dict(base.with_entities(ReaderORM.status, func.count())
                         .group_by(ReaderORM.status).all())
    labels = {k: v["label"] for k, v in rules_value(db)["types"].items()}
    return {
        "grades": _distinct(ReaderORM.grade),
        "classes": _distinct(ReaderORM.class_name),
        "types": [{"value": t, "label": labels.get(t, t),
                   "count": type_counts.get(t, 0)} for t in READER_TYPES],
        "statuses": status_counts,
        "total": base.count(),
    }


@router.get("/import/template")
@api(module="readers")
def import_template():
    """下载读者导入模板。第一行表头，第二个 sheet 是填写说明。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "读者名单"
    headers = ["借书证号", "姓名", "类型", "年级", "班级", "电话", "备注"]
    ws.append(headers)
    for i, width in enumerate([16, 12, 10, 10, 16, 16, 24], start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"
    help_ws = wb.create_sheet("填写说明")
    for row in [
        ["填写说明"],
        ["1. 借书证号与姓名必填；借书证号在同一个图书库内不能重复。"],
        ["2. 类型可填：学生 / 教师 / 职工 / 其他（留空按学生处理）。"],
        ["3. 年级、班级、电话、备注可留空。"],
        ["4. 重复的借书证号会更新已有读者（不会重复创建）。"],
        ["5. 单次最多导入 5000 行，仅支持 .xlsx 文件。"],
    ]:
        help_ws.append(row)
    return xlsx_response(wb, "reader_template.xlsx")


@router.post("/import")
@api(module="readers")
def import_readers(file: UploadFile = File(...), library_id: int | None = Form(None),
                   db: Session = Depends(get_db)):
    """Excel 导入读者（按借书证号 upsert）。逐行报错，能导的照导。"""
    lib = resolve_library(db, library_id)
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "文件超过 10 MB，请分批导入")
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "仅支持 .xlsx 文件（可先下载模板）")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(400, f"无法读取 Excel：{e}")
    ws = wb.active

    aliases = {
        "借书证号": "card_no", "证号": "card_no", "读者证号": "card_no",
        "card_no": "card_no", "cardno": "card_no",
        "姓名": "name", "name": "name",
        "类型": "reader_type", "读者类型": "reader_type", "type": "reader_type",
        "年级": "grade", "班级": "class_name",
        "电话": "phone", "手机号": "phone", "手机": "phone", "phone": "phone",
        "备注": "note", "note": "note",
    }
    type_aliases = {
        "学生": "student", "student": "student",
        "教师": "teacher", "老师": "teacher", "teacher": "teacher",
        "职工": "staff", "教工": "staff", "staff": "staff",
        "其他": "other", "other": "other",
    }

    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not header_row:
        raise HTTPException(400, "Excel 为空（第一行应为表头）")
    columns: dict[int, str] = {}
    for i, raw in enumerate(header_row):
        key = aliases.get(str(raw or "").strip().lower())
        if key:
            columns[i] = key
    if "card_no" not in columns.values() or "name" not in columns.values():
        raise HTTPException(400, "表头必须包含「借书证号」和「姓名」（请下载模板）")

    created = updated = 0
    errors: list[dict] = []
    seen: set[str] = set()
    existing = {r.card_no: r for r in
                db.query(ReaderORM).filter(ReaderORM.library_id == lib.id).all()}
    rows_read = 0
    for row_no, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if rows_read >= MAX_IMPORT_ROWS:
            errors.append({"row": row_no, "message": f"超过单次 {MAX_IMPORT_ROWS} 行上限，"
                                                     f"其余行未导入"})
            break
        values = {columns[i]: row[i] for i in columns if i < len(row)}
        if not any(v is not None and str(v).strip() for v in values.values()):
            continue
        rows_read += 1
        card_no = str(values.get("card_no") or "").strip()[:32]
        name = str(values.get("name") or "").strip()[:64]
        if not card_no or not name:
            errors.append({"row": row_no, "message": "借书证号与姓名必填"})
            continue
        if card_no in seen:
            errors.append({"row": row_no, "message": f"文件内借书证号重复：{card_no}"})
            continue
        seen.add(card_no)
        raw_type = str(values.get("reader_type") or "").strip().lower()
        reader_type = type_aliases.get(raw_type, "student" if not raw_type else "")
        if not reader_type:
            errors.append({"row": row_no,
                           "message": f"读者类型无法识别：{values.get('reader_type')}（"
                                      f"可填 学生 / 教师 / 职工 / 其他）"})
            continue
        fields = {
            "name": name, "reader_type": reader_type,
            "grade": _clean(str(values.get("grade") or ""), 32),
            "class_name": _clean(str(values.get("class_name") or ""), 64),
            "phone": _clean(str(values.get("phone") or ""), 32),
            "note": _clean(str(values.get("note") or ""), 256),
        }
        r = existing.get(card_no)
        if r is None:
            r = ReaderORM(library_id=lib.id, card_no=card_no,
                          status=READER_ACTIVE, **fields)
            db.add(r)
            existing[card_no] = r
            created += 1
        else:
            # 导入不覆盖读者状态：被停用/注销的读者不会因为重新导入而复活
            for k, v in fields.items():
                setattr(r, k, v)
            updated += 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "导入与现有读者冲突，请重试")
    return {"created": created, "updated": updated, "failed": len(errors),
            "total": rows_read, "errors": errors[:200],
            "errors_truncated": len(errors) > 200, "library_id": lib.id}


@router.post("")
@api(module="readers")
def create_reader(payload: ReaderIn, db: Session = Depends(get_db)):
    lib = resolve_library(db, payload.library_id)
    card_no = payload.card_no.strip()
    name = payload.name.strip()
    if not card_no or not name:
        raise HTTPException(400, "借书证号与姓名不能为空")
    _check_card_no(db, lib.id, card_no)
    r = ReaderORM(
        library_id=lib.id, card_no=card_no, name=name,
        reader_type=_require_type(payload.reader_type),
        status=_require_status(payload.status),
        grade=_clean(payload.grade, 32),
        class_name=_clean(payload.class_name, 64),
        phone=_clean(payload.phone, 32),
        note=_clean(payload.note, 256))
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"借书证号已存在（本库）：{card_no}")
    db.refresh(r)
    return reader_dict(r, rules_value(db))


@router.post("/batch-status")
@api(module="readers")
def batch_status(payload: ReaderBatchStatusIn, db: Session = Depends(get_db)):
    ids = [int(i) for i in payload.ids]
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    if len(ids) > 500:
        raise HTTPException(400, "单次最多处理 500 位读者")
    status = _require_status(payload.status)
    rows = (db.query(ReaderORM).filter(ReaderORM.id.in_(ids))
            .order_by(ReaderORM.id).populate_existing().with_for_update().all())
    for r in rows:
        r.status = status
    if status != READER_ACTIVE:
        reservation_service.cancel_active_for_readers(
            db, [r.id for r in rows], "读者状态已变更")
    db.commit()
    missing = sorted(set(ids) - {r.id for r in rows})
    return {"updated": len(rows), "missing": missing, "status": status}


@router.get("/{reader_id}")
@api(module="readers")
def get_reader(reader_id: int, db: Session = Depends(get_db)):
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    rules = rules_value(db)
    active = active_loans_of_reader(db, r.id)
    # 借阅历史含「已归还」与「已遗失」：丢书也是一条要留档的借阅结局。
    history = (db.query(LoanORM)
               .filter(LoanORM.reader_id == r.id,
                       LoanORM.status.in_((LOAN_RETURNED, LOAN_LOST)))
               .order_by(func.coalesce(LoanORM.returned_at, LoanORM.lost_at).desc(),
                         LoanORM.id.desc())
               .limit(100).all())
    total = db.query(LoanORM).filter(LoanORM.reader_id == r.id).count()
    rule = type_rule(rules, r.reader_type)
    return {
        **reader_dict(r, rules),
        "rules": {"max_items": rule["max_items"],
                  "loan_days": rule["loan_days"],
                  "max_renew": rule["max_renew"]},
        "stats": {"total_loans": total, "active": len(active),
                  "overdue": overdue_count(db, r.id)},
        "active_loans": [loan_dict(x) for x in active],
        "history": [loan_dict(x) for x in history],
        "fines": reader_fine_summary(db, r.id, rules),
        "compensations": reader_compensation_summary(db, r.id),
    }


@router.put("/{reader_id}")
@api(module="readers")
def update_reader(reader_id: int, payload: ReaderUpdate,
                  db: Session = Depends(get_db)):
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    if payload.card_no is not None:
        card_no = payload.card_no.strip()
        if not card_no:
            raise HTTPException(400, "借书证号不能为空")
        if card_no != r.card_no:
            _check_card_no(db, r.library_id, card_no, exclude_id=r.id)
            r.card_no = card_no
            account = portal_auth.account_for(db, r.id)
            if account is not None:
                account.username = card_no.lower()
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "姓名不能为空")
        r.name = name
    if payload.reader_type is not None:
        r.reader_type = _require_type(payload.reader_type)
    if payload.status is not None:
        r.status = _require_status(payload.status)
    if payload.grade is not None:
        r.grade = _clean(payload.grade, 32)
    if payload.class_name is not None:
        r.class_name = _clean(payload.class_name, 64)
    if payload.phone is not None:
        r.phone = _clean(payload.phone, 32)
    if payload.note is not None:
        r.note = _clean(payload.note, 256)
    try:
        if payload.status is not None and r.status != READER_ACTIVE:
            reservation_service.cancel_active_for_reader(db, r.id, "读者状态已变更")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"借书证号已存在（本库）：{payload.card_no}")
    db.refresh(r)
    return reader_dict(r, rules_value(db))


@router.post("/{reader_id}/status")
@api(module="readers")
def set_reader_status(reader_id: int, payload: ReaderStatusIn,
                      db: Session = Depends(get_db)):
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    r.status = _require_status(payload.status)
    db.commit()
    return reader_dict(r, rules_value(db))


@router.post("/{reader_id}/consent")
@api(module="readers", capability="reader.write")
def set_reader_consent(reader_id: int, payload: ConsentIn, request: Request,
                       db: Session = Depends(get_db)):
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    by = (payload.by or operator_of(request) or "").strip() or None
    row = reader_privacy.set_guardian_consent(
        db, r, consent=payload.consent, by=by)
    db.commit()
    return {"reader_id": row.id,
            "guardian_consent_at": (row.guardian_consent_at.isoformat()
                                    if row.guardian_consent_at else None),
            "guardian_consent_by": row.guardian_consent_by}


@router.post("/{reader_id}/anonymize")
@api(module="readers", capability="reader.anonymize")
def anonymize_reader(reader_id: int, payload: AnonymizeIn, request: Request,
                     db: Session = Depends(get_db)):
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    try:
        result = reader_privacy.anonymize_reader(
            db, r, operator=payload.by or operator_of(request))
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    return result


@router.get("/{reader_id}/account")
@api(module="readers")
def get_reader_account(reader_id: int, db: Session = Depends(get_db)):
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    return {"account": portal_auth.account_info(db, reader_id)}


@router.post("/{reader_id}/account")
@api(module="readers")
def provision_reader_account(reader_id: int, payload: ReaderAccountIn,
                             db: Session = Depends(get_db)):
    """开通或重置读者门户账号；初始登录名为借书证号。"""
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    try:
        portal_auth.provision_account(db, r, payload.password)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    return {"account": portal_auth.account_info(db, reader_id),
            "message": "读者账号已开通，密码已重置"}


@router.delete("/{reader_id}/account")
@api(module="readers")
def disable_reader_account(reader_id: int, db: Session = Depends(get_db)):
    r = db.get(ReaderORM, reader_id)
    if r is None:
        raise HTTPException(404, "读者不存在")
    if not portal_auth.disable_account(db, reader_id):
        raise HTTPException(404, "读者尚未开通门户账号")
    db.commit()
    return {"message": "读者账号已停用"}
