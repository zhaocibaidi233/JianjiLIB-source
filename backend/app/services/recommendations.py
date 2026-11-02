# -*- coding: utf-8 -*-
"""图书推荐。

三块数据：
- **馆员精选**：`recommendations` 表，手工排序，随书目删除级联消失；
- **热门借阅**：近 90 天外借次数，实时聚合，不落表；
- **新到图书**：近 90 天入藏、且有可借馆藏，实时查询。

卡片只显示**可借册数**（在架且没有 ACTIVE 借阅）——推荐一本全部外借的书，
学生点开也借不到。馆员精选不按可借数过滤，避免“刚推荐就被借走”导致书单变空。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (LOAN_ACTIVE, STATUS_AVAILABLE, HoldingORM, LoanORM,
                      RecommendationORM, RecordORM)

POPULAR_DAYS = 90
NEW_DAYS = 90
CARD_LIMIT = 12


def _subfield(fields, tag: str, code: str) -> str | None:
    """从 JSONB 字段里取一个子字段值（取第一个非空）。"""
    for field in fields or []:
        if not isinstance(field, dict) or str(field.get("tag")) != tag:
            continue
        for pair in field.get("sf") or []:
            if (isinstance(pair, (list, tuple)) and len(pair) >= 2
                    and pair[0] == code):
                value = str(pair[1] or "").strip()
                if value:
                    return value
    return None


def _loan_counts(db: Session, library_id: int,
                 record_ids: list[int]) -> dict[int, int]:
    """近 90 天每种书的外借次数（没有借阅记录的书不在结果里）。"""
    if not record_ids:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=POPULAR_DAYS)
    rows = (db.query(LoanORM.record_id, func.count())
            .filter(LoanORM.library_id == library_id,
                    LoanORM.record_id.in_(record_ids),
                    LoanORM.loaned_at >= since)
            .group_by(LoanORM.record_id).all())
    return {rid: int(n) for rid, n in rows}


def cards_for_records(db: Session, records: list[RecordORM], *,
                      extras: dict[int, dict] | None = None) -> list[dict]:
    """把一批记录变成推荐卡片（含可借册数 / 索书号 / 出版者）。"""
    if not records:
        return []
    ids = [r.id for r in records]
    holdings = (db.query(HoldingORM.id, HoldingORM.record_id,
                         HoldingORM.call_no, HoldingORM.status,
                         HoldingORM.shelf_no)
                .filter(HoldingORM.record_id.in_(ids)).all())
    holding_ids = [h.id for h in holdings]
    active_ids: set[int] = set()
    if holding_ids:
        active_ids = {hid for (hid,) in
                      db.query(LoanORM.holding_id)
                      .filter(LoanORM.holding_id.in_(holding_ids),
                              LoanORM.status == LOAN_ACTIVE).all()}

    active_count: dict[int, int] = {}
    available_count: dict[int, int] = {}
    call_no: dict[int, str] = {}
    available_call_no: dict[int, str] = {}
    shelf_no: dict[int, str] = {}
    for hid, rid, cn, status, shelf in holdings:
        if status == STATUS_AVAILABLE:
            active_count[rid] = active_count.get(rid, 0) + 1
            if hid not in active_ids:
                available_count[rid] = available_count.get(rid, 0) + 1
                if cn:
                    available_call_no.setdefault(rid, cn)
        if cn:
            call_no.setdefault(rid, cn)
        if shelf:
            shelf_no.setdefault(rid, shelf)

    out: list[dict] = []
    for rec in records:
        card = {
            "record_id": rec.id,
            "title": rec.title_norm,
            "author": rec.author_norm,
            "publisher": _subfield(rec.fields, "210", "c"),
            "year": rec.year,
            "class_no": rec.class_no,
            "isbn": rec.isbn_norm,
            "call_no": available_call_no.get(rec.id) or call_no.get(rec.id),
            "shelf_no": shelf_no.get(rec.id, ""),
            "active_count": active_count.get(rec.id, 0),
            "available_count": available_count.get(rec.id, 0),
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
        }
        if extras and rec.id in extras:
            card.update(extras[rec.id])
        out.append(card)
    return out


def curated_cards(db: Session, library_id: int) -> list[dict]:
    rows = (db.query(RecommendationORM, RecordORM)
            .join(RecordORM, RecordORM.id == RecommendationORM.record_id)
            .filter(RecommendationORM.library_id == library_id)
            .order_by(RecommendationORM.sort_order, RecommendationORM.id)
            .all())
    records = [rec for _, rec in rows]
    counts = _loan_counts(db, library_id, [rec.id for rec in records])
    extras = {
        rec.id: {"recommendation_id": row.id, "note": row.note,
                 "sort_order": row.sort_order,
                 "loan_count": counts.get(rec.id, 0)}
        for row, rec in rows
    }
    return cards_for_records(db, records, extras=extras)


def popular_cards(db: Session, library_id: int) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=POPULAR_DAYS)
    counts = (db.query(LoanORM.record_id, func.count().label("n"))
              .filter(LoanORM.library_id == library_id,
                      LoanORM.loaned_at >= since)
              .group_by(LoanORM.record_id)
              .order_by(func.count().desc())
              .limit(CARD_LIMIT * 4).all())
    if not counts:
        return []
    count_by_id = {rid: int(n) for rid, n in counts}
    ids = [rid for rid, _ in counts]
    by_id = {r.id: r for r in
             db.query(RecordORM).filter(RecordORM.id.in_(ids)).all()}
    records = [by_id[rid] for rid in ids if rid in by_id]
    cards = cards_for_records(
        db, records, extras={rid: {"loan_count": n}
                             for rid, n in count_by_id.items()})
    # 全部借出的书不上热门推荐（学生看到也借不到）
    return [c for c in cards if c["available_count"] > 0][:CARD_LIMIT]


def new_arrival_cards(db: Session, library_id: int) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=NEW_DAYS)
    records = (db.query(RecordORM)
               .filter(RecordORM.library_id == library_id,
                       RecordORM.created_at >= since)
               .order_by(RecordORM.created_at.desc(), RecordORM.id.desc())
               .limit(CARD_LIMIT * 4).all())
    counts = _loan_counts(db, library_id, [r.id for r in records])
    cards = cards_for_records(
        db, records,
        extras={r.id: {"loan_count": counts.get(r.id, 0)} for r in records})
    return [c for c in cards if c["available_count"] > 0][:CARD_LIMIT]


def page_data(db: Session, library) -> dict:
    return {
        "library_id": library.id,
        "library_name": library.name,
        "curated": curated_cards(db, library.id),
        "popular": popular_cards(db, library.id),
        "new_arrivals": new_arrival_cards(db, library.id),
    }


def has_shelf_copy(db: Session, record_id: int) -> bool:
    """是否还有“在架状态”的册（外借也算）。推荐可以推一本暂时借完的书，
    但不能推整条都已注销 / 遗失的记录。"""
    return (db.query(HoldingORM.id)
            .filter(HoldingORM.record_id == record_id,
                    HoldingORM.status == STATUS_AVAILABLE).first()) is not None


def next_sort_order(db: Session, library_id: int) -> int:
    current = (db.query(func.max(RecommendationORM.sort_order))
               .filter(RecommendationORM.library_id == library_id)
               .scalar())
    return int(current or 0) + 1
