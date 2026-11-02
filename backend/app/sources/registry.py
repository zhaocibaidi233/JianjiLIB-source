# -*- coding: utf-8 -*-
"""可用套录源的装配 + 检索分层常量。

**为什么单独一个模块**：装配逻辑（哪些源、什么顺序）是产品规则，
原先在 `api/cataloging.py` 和 `api/quick.py` 各写了一份——两份迟早会漂。
分层顺序这种"改动一次就要全局一致"的规则，必须只有一处定义。

检索顺序（`search_tiered` 按 tier 升序，**前一层命中就不查下一层**）：

| tier | 源 | 命中后的动作 |
|---|---|---|
| 0 | 图书库 | **弹窗二选一**：打开已有条目加复本 / 作为新记录另编一册（同 ISBN 的多卷书，见） |
| 1 | 本地资源库（含**外部 PostgreSQL 资源库**） | 新建记录（套录编辑 → 条码入库） |
| 2 | Z39.50 外部源 | 同上（兜底：慢、不稳，能不用就不用） |

tier 1 里有**两类**源：本系统 `res` schema 的 `PgResourceStore`（自动装配）与用户在
设置页手填连接串的 `PgExternalSource`（`SourceORM.type == "pg"`，见）。它们
都属"本地、快"这一层，同层会**一起被查到**（层是短路边界，不是"只查第一个"），
层内按 `priority` 排序。
"""
from __future__ import annotations

from ..db import SessionLocal
from ..models import ResourceLibrary, SourceORM
from .base import SourceConfig
from .library import LibrarySource
from .local import LocalLibrarySource
from .pg_external import PgExternalSource
from .pg_store import PgResourceStore
from .z3950 import Z3950Source

LIBRARY_SOURCE_NAME = "图书库"
RESOURCE_SOURCE_NAME = "本地资源库"

TIER_LIBRARY, TIER_RESOURCE, TIER_Z3950 = 0, 1, 2


def active_sources(db, library_id: int | None = None) -> list:
    """按检索分层构建可用源实例。

    `library_id` 把"图书库"这一层限定在当前图书库——同一本书在不同校
    可以各有一条记录，不该互相判成"已经编过"。
    """
    sources: list = []

    # tier 0：图书库。命中即"这个 ISBN 库里已有记录"，前端弹窗让编目员二选一
    # （打开已有条目 / 另编一册）——同 ISBN 的多卷书是多条记录，不能自动判定为重复。
    sources.append(LibrarySource(
        SourceConfig(type="library", name=LIBRARY_SOURCE_NAME,
                     tier=TIER_LIBRARY, priority=0),
        db, library_id))

    lib_ids = [l.id for l in db.query(ResourceLibrary)
               .filter(ResourceLibrary.enabled.is_(True)).all()]
    if lib_ids:
        sources.append(LocalLibrarySource(
            SourceConfig(type="local", name=RESOURCE_SOURCE_NAME,
                         tier=TIER_RESOURCE, priority=0),
            # 必须给 store **自管**的会话工厂，不能是 `lambda: db`（请求级会话）：
            # PgResourceStore 的方法用 `with self._sf() as db:` 包裹，Session 作上下文
            # 退出时会 close —— 那会把请求会话连带关掉（identity map 清空、
            # 未 flush 的待写对象丢失）。资源库检索是只读的，独立短会话没有代价。
            PgResourceStore(SessionLocal, library_ids=lib_ids)))

    # tier 1 之二：**外部 PostgreSQL 资源库**（用户在设置页手填连接串）。
    # 与上面那个"本系统 res schema"同层 —— 都是自有/本地数据，应当一起被查到；
    # 层内按 priority 排（默认 100，本地资源库 0 在前）。
    for orm in db.query(SourceORM).filter(SourceORM.enabled.is_(True),
                                          SourceORM.type == "pg").all():
        cfg = SourceConfig.from_dict(orm.config)
        cfg.tier = TIER_RESOURCE       # 外部库不插队到图书库之前，也不落到 Z39.50 兜底层
        # `enabled` 有**两处**：表列与 config 字典。以表列为准——上面的查询已经按列
        # 过滤过，这里必须同步，否则 config 里一个陈旧的 false 会让这个源在
        # `search_tiered` 里被静默跳过：界面上明明「已启用」，检索却永远不出结果。
        cfg.enabled = True
        sources.append(PgExternalSource(cfg))

    for orm in db.query(SourceORM).filter(SourceORM.enabled.is_(True),
                                          SourceORM.type == "z3950").all():
        cfg = SourceConfig.from_dict(orm.config)
        cfg.tier = TIER_Z3950       # Z39.50 一律兜底，不随配置插队
        cfg.enabled = True          # 同上：以表列为准，别让 config 里的旧值再过滤一遍
        sources.append(Z3950Source(cfg))

    return sources
