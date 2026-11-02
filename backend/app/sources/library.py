# -*- coding: utf-8 -*-
"""图书库套录源：把"已入库记录"也当成一个检索源，排在检索顺序最前面。

产品规则（用户定）：套录检索顺序是 **图书库 → 资源库 → Z39.50**。
命中图书库说明**这本书已经编过**——所以这一层放 tier 0，命中即由 `search_tiered` 短路。

本层支持两种命中：
  - **ISBN**（等价形态一起查）—— 主路径；
  - **题名完全相等** —— 录入栏那个通用框允许直接输入题名，若库里已有同名记录却搜不到，
    编目员就会编出一条重复记录。**只做完全相等，不做模糊**（模糊会把近似题名都判成
    "已经编过"，是本层最怕的误判）。

⚠️ 但"已经编过"**不等于"不该再编"**：同 ISBN 的多卷书（上下册共用一个 ISBN 是真实
情形）在图书库里是多条独立记录，第 2 册仍要另编一条。所以本层只负责
**把命中的全部记录（连同各自的卷册号）如实返回**，由前端弹窗让编目员二选一：
「打开已有条目（加复本）」或「作为新记录编目（同书不同卷册/版本）」。

与 `LocalLibrarySource` 的区别：
  - 资源库（res schema）是"外部拿来的书目底稿"，只读，命中后**新建**记录；
  - 图书库（records 表）是"本馆编目成品"，命中后由编目员决定**改已有**还是**另编一册**。

按图书库隔离：同一本书在不同校可以各有一条记录，检索只看当前图书库。
"""
from __future__ import annotations

from ..core.marc import Record
from .base import Candidate, CatalogSource


class LibrarySource(CatalogSource):
    """检索已入库记录（按 `library_id` 隔离）。"""

    def __init__(self, config, db, library_id: int | None = None):
        super().__init__(config)
        self._db = db
        self._library_id = library_id

    @staticmethod
    def _isbn_forms(isbn: str) -> list[str]:
        """ISBN 的全部等价形态 + 规范形态。

        `records.isbn_norm` 存的是 `canonical()`（可转换时统一为 13 位），
        而用户输入可能是带连字符的 ISBN-10/13——所以三种形态一起查，避免漏命中。
        """
        from ..core import isbn as isbnlib
        forms = set(isbnlib.all_forms(isbn))
        canon = isbnlib.canonical(isbn)
        if canon:
            forms.add(canon)
        return sorted(forms)

    def search(self, isbn=None, title=None, author=None, limit=10,
               offset=0) -> list[Candidate]:
        # ISBN 精确命中优先；**没有 ISBN 时按"题名完全相等"兜一层** —— 录入栏那个
        # 通用框可以输入书名，若库里已有同名记录却搜不到，编目员就会编出一条重复记录。
        #
        # ⚠️ 这里刻意用**完全相等**而不是模糊匹配：模糊匹配会把近似题名都判成
        #    "已经编过"（本层最怕的误判，见模块 docstring），而完全相等是强重复信号。
        forms = self._isbn_forms(isbn) if isbn else []
        exact_title = None
        if not forms and title:
            from ..core import clean as cl
            exact_title = cl.normalize_title(title)
            if not exact_title:
                return []
        if not forms and not exact_title:
            return []

        from ..models import HoldingORM, RecordORM
        from ..services.convert import orm_to_record

        q = self._db.query(RecordORM)
        if self._library_id:
            q = q.filter(RecordORM.library_id == self._library_id)
        if forms:
            q = q.filter(RecordORM.isbn_norm.in_(forms))
        else:
            q = q.filter(RecordORM.title_norm == exact_title)

        out: list[Candidate] = []
        # ⚠️ 不要在这里按 ISBN 折叠：同 ISBN 的多卷书是多条独立记录，全部返回，
        # 由前端弹窗让编目员分辨是哪一册（卷册号随 Candidate.volume 出去）。
        # 翻页：按记录 id 排序（稳定），`offset` 直接下推给 SQL。
        # 排序必须固定——不排序时 PostgreSQL 不保证行序，翻页会重复或漏条。
        rows = (q.order_by(RecordORM.id).offset(max(0, int(offset or 0)))
                .limit(limit).all())
        volume_by_rec: dict[int, str] = {}
        if rows:
            for rid, vol in (self._db.query(HoldingORM.record_id, HoldingORM.volume)
                             .filter(HoldingORM.record_id.in_([x.id for x in rows]))
                             .order_by(HoldingORM.id).all()):
                if vol:
                    volume_by_rec.setdefault(rid, vol)
        for orm in rows:
            rec = orm_to_record(orm)
            out.append(Candidate(
                source_name=self.name, source_key=str(orm.id), record=rec,
                record_id=orm.id,                      # ← 前端靠它跳转到该条目编辑页
                volume=(volume_by_rec.get(orm.id)
                        or rec.first_value("200", "h")
                        or rec.first_value("200", "i") or None),  # 同 ISBN 多卷册的区分项
                title=orm.title_norm or rec.title(),
                author=orm.author_norm or rec.first_value("200", "f"),
                publisher=rec.first_value("210", "c"),
                year=str(orm.year) if orm.year else rec.first_value("210", "d"),
                class_no=orm.class_no or rec.first_value("690", "a"),
                isbn=rec.first_value("010", "a"),
            ))
        return out

    def fetch(self, candidate: Candidate) -> Record:
        assert candidate.record is not None
        return candidate.record
