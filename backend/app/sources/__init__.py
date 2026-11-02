# -*- coding: utf-8 -*-
"""套录源适配层：统一检索接口 + 可编辑源配置。

架构：所有套录源实现 CatalogSource 接口，检索点统一为
ISBN/题名/责任者；源配置（host/port/db/账号/优先级/启停）全部可编辑，
换源不改代码。Z39.50 账号实测记录见历史技术验证记录。
"""
from .base import Candidate, CatalogSource, SourceConfig, search_chained, search_tiered
from .library import LibrarySource
from .local import InMemoryStore, LocalLibrarySource
from .pg_external import PgExternalSource
from .z3950 import Z3950Source

__all__ = [
    "Candidate", "CatalogSource", "SourceConfig", "search_chained", "search_tiered",
    "LibrarySource", "LocalLibrarySource", "InMemoryStore", "PgExternalSource",
    "Z3950Source",
]
