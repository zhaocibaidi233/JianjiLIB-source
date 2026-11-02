# -*- coding: utf-8 -*-
"""本地资源库套录源（MVP 主路径）。

ResourceStore 协议解耦存储实现：MVP 用 InMemoryStore（测试/单机），
正式版实现 PgResourceStore（pg_trgm 模糊检索，方案已在 验证）。
"""
from __future__ import annotations

import re

from ..core.marc import Record
from .base import Candidate, CatalogSource, SourceConfig


class ResourceStore:
    """资源库存储协议。"""

    def all_records(self) -> list[Record]:
        raise NotImplementedError

    def search_isbn(self, isbn_forms: set[str]) -> list[tuple[str, Record]]:
        """按 ISBN 全部等价形态精确检索，返回 [(key, record)]。"""
        raise NotImplementedError

    def search_title(self, title_norm: str, limit: int = 10) -> list[tuple[str, Record]]:
        raise NotImplementedError

    def search_author(self, author_norm: str, limit: int = 10) -> list[tuple[str, Record]]:
        """按责任者检索（录入栏「套录」框允许直接输责任者名）。"""
        raise NotImplementedError


class InMemoryStore(ResourceStore):
    """内存资源库：加载 ISO 批次后做 ISBN/题名检索（测试与小批量用）。"""

    def __init__(self):
        self._records: dict[str, Record] = {}

    def add(self, key: str, record: Record) -> None:
        self._records[key] = record

    def all_records(self) -> list[Record]:
        return list(self._records.values())

    def search_isbn(self, isbn_forms: set[str]) -> list[tuple[str, Record]]:
        from ..core import isbn as isbnlib
        hits = []
        for key, rec in self._records.items():
            isbns = set()
            for f in rec.get_fields("010"):
                for v in f.all("a"):
                    d = isbnlib.normalize(v)
                    if d:
                        isbns.add(d)
            if isbns & isbn_forms:
                hits.append((key, rec))
        return hits

    def search_title(self, title_norm: str, limit: int = 10) -> list[tuple[str, Record]]:
        def norm(s: str) -> str:
            return re.sub(r"[\s：:，,./·．\-—()（）]", "", s)

        target = norm(title_norm)
        scored = []
        for key, rec in self._records.items():
            t = rec.title() or ""
            tn = norm(t)
            if not tn:
                continue
            # 简单相似度：公共前缀长度 / 目标长度（生产版换 pg_trgm）
            common = 0
            for a, b in zip(target, tn):
                if a != b:
                    break
                common += 1
            score = common / max(len(target), 1)
            if score >= 0.5 or target in tn:
                scored.append((score, key, rec))
        scored.sort(key=lambda x: -x[0])
        return [(k, r) for _, k, r in scored[:limit]]

    def search_author(self, author_norm: str, limit: int = 10) -> list[tuple[str, Record]]:
        def norm(s: str) -> str:
            return re.sub(r"[\s：:，,./·．\-—()（）]", "", s)

        target = norm(author_norm)
        if not target:
            return []
        scored = []
        for key, rec in self._records.items():
            a = rec.first_value("200", "f") or ""
            an = norm(a)
            if not an:
                continue
            # 责任者写法差异大（`（清）曹雪芹著` vs `曹雪芹`），用"互相包含"判定；
            # 命中多个时按名字长度接近程度排序（越接近越可能是同一个人）。
            if target in an or an in target:
                scored.append((abs(len(an) - len(target)), key, rec))
        scored.sort(key=lambda x: x[0])
        return [(k, r) for _, k, r in scored[:limit]]


class LocalLibrarySource(CatalogSource):
    """本地资源库源：只读资源，检索命中后由调用方经本地化改写进入编目库。"""

    def __init__(self, config: SourceConfig, store: ResourceStore):
        super().__init__(config)
        self.store = store

    def search(self, isbn=None, title=None, author=None, limit=10,
               offset=0) -> list[Candidate]:
        """ISBN 精确 → 题名相似 → 责任者，**前者命中就不再往后查**。

        三条路与 `cataloging_search` 的通用检索词 `q` 配套：一个输入框吃 ISBN / 题名 / 责任者，
        后端先当题名、没命中再当责任者，这里就按同样的顺序找。

        翻页：`ResourceStore` 的接口只有 `limit`，所以这里**多取 offset 条再切片**。
        资源库是本机数据（内存或本库 PG），多取几十条的成本可以忽略；为此去改
        store 接口（两个实现）不划算。
        """
        offset = max(0, int(offset or 0))
        want = limit + offset
        hits: list[tuple[str, Record]] = []
        if isbn:
            from ..core import isbn as isbnlib
            forms = isbnlib.all_forms(isbn)
            hits = self.store.search_isbn(forms) if forms else []
        if not hits and title:
            hits = self.store.search_title(title, limit=want)
        if not hits and author:
            hits = self.store.search_author(author, limit=want)
        out = []
        for key, rec in hits[offset:offset + limit]:
            out.append(Candidate(
                source_name=self.name, source_key=key, record=rec,
                title=rec.title(),
                author=rec.first_value("200", "f"),
                publisher=rec.first_value("210", "c"),
                year=rec.first_value("210", "d"),
                class_no=rec.first_value("690", "a"),
                isbn=rec.first_value("010", "a"),
            ))
        return out

    def fetch(self, candidate: Candidate) -> Record:
        assert candidate.record is not None
        return candidate.record
