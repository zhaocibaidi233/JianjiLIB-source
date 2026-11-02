# -*- coding: utf-8 -*-
"""图书推荐：馆员精选 + 热门借阅 + 新到图书。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..db import get_db
from ..models import RecommendationORM, RecordORM
from ..services.recommendations import (has_shelf_copy, next_sort_order,
                                        page_data)
from .book_libraries import resolve_library
from ..core.route_meta import api

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class AddIn(BaseModel):
    record_id: int
    note: str | None = Field(None, max_length=256)
    sort_order: int | None = Field(None, ge=0, le=9999)


class UpdateIn(BaseModel):
    note: str | None = Field(None, max_length=256)
    sort_order: int | None = Field(None, ge=0, le=9999)


class ReorderIn(BaseModel):
    ids: list[int]


@router.get("")
@api(module="circulation")
def get_recommendations(library_id: int | None = None,
                        db: Session = Depends(get_db)):
    library = resolve_library(db, library_id)
    return page_data(db, library)


@router.post("")
@api(module="circulation")
def add_recommendation(payload: AddIn, request: Request,
                       db: Session = Depends(get_db)):
    rec = db.get(RecordORM, payload.record_id)
    if rec is None:
        raise HTTPException(404, "书目记录不存在")
    if not has_shelf_copy(db, rec.id):
        raise HTTPException(400, "该记录没有在架馆藏，不能加入推荐")
    note = (payload.note or "").strip()[:256] or None
    existing = (db.query(RecommendationORM)
                .filter(RecommendationORM.library_id == rec.library_id,
                        RecommendationORM.record_id == rec.id)
                .first())
    if existing is not None:
        if payload.note is not None:
            existing.note = note
        if payload.sort_order is not None:
            existing.sort_order = payload.sort_order
        db.commit()
        return {"id": existing.id, "message": "该书已在推荐中，已更新推荐语"}
    row = RecommendationORM(
        library_id=rec.library_id, record_id=rec.id, note=note,
        sort_order=(payload.sort_order if payload.sort_order is not None
                    else next_sort_order(db, rec.library_id)),
        created_by=operator_of(request),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "message": "已加入馆员推荐"}


@router.post("/reorder")
@api(module="circulation")
def reorder_recommendations(payload: ReorderIn,
                            db: Session = Depends(get_db)):
    ids = [int(x) for x in payload.ids]
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "ids 不能重复")
    rows = (db.query(RecommendationORM)
            .filter(RecommendationORM.id.in_(ids)).all())
    if len(rows) != len(ids):
        raise HTTPException(404, "推荐记录不存在")
    libraries = {x.library_id for x in rows}
    if len(libraries) != 1:
        raise HTTPException(400, "不能跨图书库排序")
    by_id = {x.id: x for x in rows}
    for index, rec_id in enumerate(ids):
        by_id[rec_id].sort_order = index
    db.commit()
    return {"message": "推荐顺序已保存"}


@router.put("/{recommendation_id}")
@api(module="circulation")
def update_recommendation(recommendation_id: int, payload: UpdateIn,
                          db: Session = Depends(get_db)):
    row = db.get(RecommendationORM, recommendation_id)
    if row is None:
        raise HTTPException(404, "推荐记录不存在")
    if payload.note is not None:
        row.note = payload.note.strip()[:256] or None
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    db.commit()
    return {"message": "推荐语已保存"}


@router.delete("/{recommendation_id}")
@api(module="circulation")
def delete_recommendation(recommendation_id: int,
                          db: Session = Depends(get_db)):
    row = db.get(RecommendationORM, recommendation_id)
    if row is None:
        raise HTTPException(404, "推荐记录不存在")
    db.delete(row)
    db.commit()
    return {"message": "已从推荐中移除（不会删除书目）"}
