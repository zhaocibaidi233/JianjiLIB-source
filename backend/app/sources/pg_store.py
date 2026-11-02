# -*- coding: utf-8 -*-
"""PostgreSQL 资源库存储：ResourceStore 协议的正式实现。

模糊检索走 pg_trgm（实证：GIN 索引、毫秒级）。
"""
from __future__ import annotations

from sqlalchemy import func

from ..core.marc import Record
from ..db import like_escape
from ..models import ResourceRecordORM
from .local import ResourceStore


class PgResourceStore(ResourceStore):
    """跨全部启用资源库的检索（按需可加 library_id 过滤）。"""

    def __init__(self, session_factory, library_ids: list[int] | None = None):
        self._sf = session_factory
        self._library_ids = library_ids

    def all_records(self) -> list[Record]:
        with self._sf() as db:
            rows = db.query(ResourceRecordORM).all()
            from ..services.convert import orm_to_record
            return [orm_to_record(r) for r in rows]

    def search_isbn(self, isbn_forms: set[str]) -> list[tuple[str, Record]]:
        with self._sf() as db:
            q = db.query(ResourceRecordORM).filter(ResourceRecordORM.isbn_norm.in_(isbn_forms))
            if self._library_ids:
                q = q.filter(ResourceRecordORM.library_id.in_(self._library_ids))
            rows = q.limit(20).all()
            from ..services.convert import orm_to_record
            return [(f"rr-{r.id}", orm_to_record(r)) for r in rows]

    def search_title(self, title_norm: str, limit: int = 10) -> list[tuple[str, Record]]:
        from ..services.convert import orm_to_record
        with self._sf() as db:
            q = db.query(ResourceRecordORM)
            if self._library_ids:
                q = q.filter(ResourceRecordORM.library_id.in_(self._library_ids))
            # pg_trgm 相似度优先；索引不可用时退化为 ilike
            try:
                sim = func.similarity(ResourceRecordORM.title_norm, title_norm)
                rows = (q.filter(ResourceRecordORM.title_norm.op("%")(title_norm))
                         .order_by(sim.desc()).limit(limit).all())
                if rows:
                    return [(f"rr-{r.id}", orm_to_record(r)) for r in rows]
            except Exception:
                db.rollback()
            rows = (q.filter(ResourceRecordORM.title_norm.ilike(f"%{like_escape(title_norm)}%", escape="\\"))
                     .limit(limit).all())
            return [(f"rr-{r.id}", orm_to_record(r)) for r in rows]

    def search_author(self, author_norm: str, limit: int = 10) -> list[tuple[str, Record]]:
        """按责任者检索（与 `search_title` 同一套路：pg_trgm 优先、ilike 兜底）。

        索引 `ix_rr_author_trgm` 见 `scripts/migrate_author_trgm.py`；
        没建索引时 `%` 退化成全表扫描但**仍能出结果**，不会报错。
        """
        from ..services.convert import orm_to_record
        if not (author_norm or "").strip():
            return []
        with self._sf() as db:
            q = db.query(ResourceRecordORM)
            if self._library_ids:
                q = q.filter(ResourceRecordORM.library_id.in_(self._library_ids))
            try:
                sim = func.similarity(ResourceRecordORM.author_norm, author_norm)
                rows = (q.filter(ResourceRecordORM.author_norm.op("%")(author_norm))
                         .order_by(sim.desc()).limit(limit).all())
                if rows:
                    return [(f"rr-{r.id}", orm_to_record(r)) for r in rows]
            except Exception:
                db.rollback()
            rows = (q.filter(ResourceRecordORM.author_norm.ilike(f"%{like_escape(author_norm)}%", escape="\\"))
                     .limit(limit).all())
            return [(f"rr-{r.id}", orm_to_record(r)) for r in rows]
