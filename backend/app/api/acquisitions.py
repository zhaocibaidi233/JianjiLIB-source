# -*- coding: utf-8 -*-
"""荐购 / 受赠登记与审核（教材〔2021〕2号；契约 D17）。

Recommendation 是读者侧的展示位，不是采购/准入。本模块记录**来源**并走
「初选—审议—确认—备案」：捐赠未做来源审核不得确认；确认/备案留审批文号。
不产生书目或馆藏，避免把“登记”误当“已入藏”。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..core.route_meta import api
from ..db import get_db, like_escape
from ..models import (ACQ_APPROVED, ACQ_DONATION, ACQ_FILED, ACQ_PROPOSED,
                      ACQ_REJECTED, ACQ_REVIEWED, ACQ_SOURCE_LABELS,
                      ACQ_SOURCE_TYPES, ACQ_STAGE_LABELS, ACQ_STAGES,
                      AcquisitionRequestORM)
from .book_libraries import resolve_library

router = APIRouter(prefix="/acquisitions", tags=["acquisitions"])

#: 阶段只能向前：初选 → 审议 → 确认 → 备案；任一阶段可“不予采纳”。
_ALLOWED_NEXT = {
    ACQ_PROPOSED: {ACQ_REVIEWED, ACQ_REJECTED},
    ACQ_REVIEWED: {ACQ_APPROVED, ACQ_REJECTED},
    ACQ_APPROVED: {ACQ_FILED},
    ACQ_FILED: set(),
    ACQ_REJECTED: set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: str | None, max_len: int) -> str | None:
    v = (value or "").strip()
    return v[:max_len] or None


def _require_source(value: str | None) -> str:
    v = (value or "").strip().upper()
    if v not in ACQ_SOURCE_TYPES:
        raise HTTPException(400, f"来源只能是 {' / '.join(ACQ_SOURCE_TYPES)}")
    return v


def _row_dict(row: AcquisitionRequestORM) -> dict:
    return {
        "id": row.id, "library_id": row.library_id,
        "title": row.title, "author": row.author, "isbn": row.isbn,
        "publisher": row.publisher,
        "source_type": row.source_type,
        "source_type_label": ACQ_SOURCE_LABELS.get(row.source_type, row.source_type),
        "source_name": row.source_name, "contact": row.contact,
        "quantity": row.quantity, "note": row.note,
        "stage": row.stage,
        "stage_label": ACQ_STAGE_LABELS.get(row.stage, row.stage),
        "source_checked": row.source_checked,
        "source_checked_at": (row.source_checked_at.isoformat()
                              if row.source_checked_at else None),
        "source_checked_by": row.source_checked_by,
        "source_check_note": row.source_check_note,
        "review_note": row.review_note, "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "approval_ref": row.approval_ref,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class AcqIn(BaseModel):
    library_id: int | None = None
    title: str = Field(min_length=1, max_length=256)
    author: str | None = Field(None, max_length=128)
    isbn: str | None = Field(None, max_length=32)
    publisher: str | None = Field(None, max_length=128)
    source_type: str = "READER"
    source_name: str | None = Field(None, max_length=128)
    contact: str | None = Field(None, max_length=64)
    quantity: int = Field(1, ge=1, le=999)
    note: str | None = Field(None, max_length=512)


class AcqUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    author: str | None = Field(None, max_length=128)
    isbn: str | None = Field(None, max_length=32)
    publisher: str | None = Field(None, max_length=128)
    source_type: str | None = None
    source_name: str | None = Field(None, max_length=128)
    contact: str | None = Field(None, max_length=64)
    quantity: int | None = Field(None, ge=1, le=999)
    note: str | None = Field(None, max_length=512)


class StageIn(BaseModel):
    stage: str
    note: str | None = Field(None, max_length=512)
    approval_ref: str | None = Field(None, max_length=128)


class SourceCheckIn(BaseModel):
    checked: bool = True
    note: str | None = Field(None, max_length=256)


@router.get("")
@api(module="circulation")
def list_requests(library_id: int | None = None,
                  stage: str | None = None,
                  source_type: str | None = None,
                  q: str | None = None,
                  page: int = Query(1, ge=1),
                  page_size: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db)):
    library = resolve_library(db, library_id)
    query = (db.query(AcquisitionRequestORM)
             .filter(AcquisitionRequestORM.library_id == library.id))
    if stage and stage != "all":
        if stage not in ACQ_STAGES:
            raise HTTPException(400, f"阶段只能是 {' / '.join(ACQ_STAGES)}")
        query = query.filter(AcquisitionRequestORM.stage == stage)
    if source_type:
        query = query.filter(
            AcquisitionRequestORM.source_type == _require_source(source_type))
    if q:
        s = like_escape(q.strip())
        query = query.filter(or_(
            AcquisitionRequestORM.title.ilike(f"%{s}%", escape="\\"),
            AcquisitionRequestORM.author.ilike(f"%{s}%", escape="\\"),
            AcquisitionRequestORM.isbn.ilike(f"%{s}%", escape="\\"),
            AcquisitionRequestORM.source_name.ilike(f"%{s}%", escape="\\")))
    total = query.count()
    rows = (query.order_by(AcquisitionRequestORM.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    counts = dict(db.query(AcquisitionRequestORM.stage, func.count())
                  .filter(AcquisitionRequestORM.library_id == library.id)
                  .group_by(AcquisitionRequestORM.stage).all())
    return {
        "library": {"id": library.id, "name": library.name},
        "total": total, "page": page, "page_size": page_size,
        "counts": {s: counts.get(s, 0) for s in ACQ_STAGES},
        "stage_labels": ACQ_STAGE_LABELS,
        "source_labels": ACQ_SOURCE_LABELS,
        "items": [_row_dict(row) for row in rows],
    }


def _get(db: Session, request_id: int) -> AcquisitionRequestORM:
    row = db.get(AcquisitionRequestORM, request_id)
    if row is None:
        raise HTTPException(404, "荐购/受赠记录不存在")
    return row


@router.post("")
@api(module="circulation")
def create_request(payload: AcqIn, request: Request,
                   db: Session = Depends(get_db)):
    library = resolve_library(db, payload.library_id)
    source_type = _require_source(payload.source_type)
    source_name = _clean(payload.source_name, 128)
    if source_type == ACQ_DONATION and not source_name:
        raise HTTPException(400, "捐赠必须填写捐赠方（来源）")
    row = AcquisitionRequestORM(
        library_id=library.id,
        title=payload.title.strip(),
        author=_clean(payload.author, 128),
        isbn=_clean(payload.isbn, 32),
        publisher=_clean(payload.publisher, 128),
        source_type=source_type,
        source_name=source_name,
        contact=_clean(payload.contact, 64),
        quantity=payload.quantity,
        note=_clean(payload.note, 512),
        stage=ACQ_PROPOSED,
        created_by=operator_of(request),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_dict(row)


@router.put("/{request_id}")
@api(module="circulation")
def update_request(request_id: int, payload: AcqUpdate,
                   db: Session = Depends(get_db)):
    row = _get(db, request_id)
    if row.stage not in (ACQ_PROPOSED, ACQ_REVIEWED):
        raise HTTPException(400, "只有初选/审议阶段可以修改登记内容")
    if payload.title is not None:
        row.title = payload.title.strip()
    if payload.author is not None:
        row.author = _clean(payload.author, 128)
    if payload.isbn is not None:
        row.isbn = _clean(payload.isbn, 32)
    if payload.publisher is not None:
        row.publisher = _clean(payload.publisher, 128)
    if payload.source_type is not None:
        row.source_type = _require_source(payload.source_type)
    if payload.source_name is not None:
        row.source_name = _clean(payload.source_name, 128)
    if payload.contact is not None:
        row.contact = _clean(payload.contact, 64)
    if payload.quantity is not None:
        row.quantity = payload.quantity
    if payload.note is not None:
        row.note = _clean(payload.note, 512)
    if row.source_type == ACQ_DONATION and not row.source_name:
        db.rollback()
        raise HTTPException(400, "捐赠必须填写捐赠方（来源）")
    db.commit()
    db.refresh(row)
    return _row_dict(row)


@router.post("/{request_id}/source-check")
@api(module="circulation")
def check_source(request_id: int, payload: SourceCheckIn, request: Request,
                 db: Session = Depends(get_db)):
    """登记捐赠来源审核结论；未审核通过的捐赠不能进入确认/备案。"""
    row = _get(db, request_id)
    if row.stage in (ACQ_FILED,):
        raise HTTPException(400, "已备案的记录不能再改来源审核")
    row.source_checked = payload.checked
    row.source_checked_at = _now() if payload.checked else None
    row.source_checked_by = operator_of(request) if payload.checked else None
    row.source_check_note = _clean(payload.note, 256)
    db.commit()
    db.refresh(row)
    return _row_dict(row)


@router.post("/{request_id}/stage")
@api(module="circulation")
def move_stage(request_id: int, payload: StageIn, request: Request,
               db: Session = Depends(get_db)):
    row = _get(db, request_id)
    target = (payload.stage or "").strip().upper()
    if target not in ACQ_STAGES:
        raise HTTPException(400, f"阶段只能是 {' / '.join(ACQ_STAGES)}")
    if target not in _ALLOWED_NEXT[row.stage]:
        raise HTTPException(
            400, f"不能从「{ACQ_STAGE_LABELS[row.stage]}」直接到「{ACQ_STAGE_LABELS[target]}」")
    if target == ACQ_REJECTED and not (payload.note or "").strip():
        raise HTTPException(400, "不予采纳必须填写审议意见")
    if target == ACQ_APPROVED and row.source_type == ACQ_DONATION and not row.source_checked:
        raise HTTPException(400, "捐赠来源未审核通过，不能确认")
    if target == ACQ_FILED and not (payload.approval_ref or "").strip():
        raise HTTPException(400, "备案必须填写审批/备案文号")
    row.stage = target
    if target in (ACQ_REVIEWED, ACQ_APPROVED, ACQ_REJECTED):
        row.reviewed_by = operator_of(request)
        row.reviewed_at = _now()
        if payload.note is not None:
            row.review_note = _clean(payload.note, 512)
    if payload.approval_ref is not None:
        row.approval_ref = _clean(payload.approval_ref, 128)
    db.commit()
    db.refresh(row)
    return _row_dict(row)
