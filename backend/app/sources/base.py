# -*- coding: utf-8 -*-
"""套录源抽象：统一检索接口与候选记录模型。"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.marc import Record

log = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """套录源配置——UI 可编辑，换源不改代码。

    典型配置（实测记录见历史技术验证记录）：
      type="local",  name="某馆配商-示例学校"
      type="z3950",  host="z3950.example.com", port=2100, database="uc_bib",
                     user="<账号>", password="<口令>", charset="gbk"
    """
    type: str                          # library / local / z3950
    name: str
    host: str = ""
    port: int = 2100
    database: str = ""
    user: str = ""
    password: str = ""
    charset: str = "gbk"               # 记录传输字符集
    record_syntax: str = "unimarc"     # cnmarc 走 UNIMARC 族 OID
    priority: int = 100                # 同一 tier 内小者先检索
    # 检索分层：**前一层命中就不再查下一层**（见 search_tiered）。
    # 默认 100 = 兜底层，新增源类型忘了设 tier 也不会插到前面去。
    tier: int = 100
    enabled: bool = True
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "SourceConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


@dataclass
class Candidate:
    """套录检索命中的一条候选。"""
    source_name: str
    source_key: str                    # 源内唯一键（如结果集位置/内部 id）
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    year: str | None = None
    class_no: str | None = None
    isbn: str | None = None
    record: Record | None = None       # 部分源检索时即返回记录；否则经 fetch() 获取
    # 图书库源命中时的已入库记录 id；其余源为 None。
    # 前端靠它判断"这本书已经编过"→ 进入该条目的编辑模式，而不是再建一条重复记录。
    record_id: int | None = None
    # 图书库源命中时该记录的**种级卷册号**（上册/下册…）。同 ISBN 的多卷书在图书库里
    # 是**多条独立记录**，前端靠它让编目员分辨候选究竟是哪一册，再决定"打开已有条目
    # 加复本"还是"作为新记录另编一册"。其余源为 None。
    volume: str | None = None


class CatalogSource(ABC):
    """套录源统一接口。检索策略：ISBN 精确优先，题名+责任者模糊回退。"""

    def __init__(self, config: SourceConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    def search(self, isbn: str | None = None, title: str | None = None,
               author: str | None = None, limit: int = 10,
               offset: int = 0) -> list[Candidate]:
        """返回候选列表（未实现应抛 NotImplementedError 或返回空）。

        `offset` 是**本源的**结果偏移，用于翻页：返回 [offset, offset+limit) 这一段。
        各源按自己的顺序实现（SQL `OFFSET` / Z39.50 结果集位置），顺序必须在同一
        查询下稳定——否则翻页会重复或漏条。
        """

    @abstractmethod
    def fetch(self, candidate: Candidate) -> Record:
        """取回候选的完整 MARC 记录。"""

    def health(self) -> dict:
        return {"name": self.config.name, "type": self.config.type,
                "enabled": self.config.enabled, "status": "unknown"}


def _same_source_dedup_key(c: Candidate) -> str:
    """同一源内的去重键（空串 = 不去重）。

    **图书库候选按记录 id 去重，绝不按 ISBN**：同 ISBN 的多卷书（登记表 G4 上下册
    共用一个 ISBN 是真实情形）在图书库里是**多条独立记录**，按 ISBN 折叠会把第 2 册起
    全部丢掉——前端于是永远只看到第 1 册，"打开哪一册"根本无从谈起。
    外部源/资源库仍按归一 ISBN 折叠：同一本书被外部源重复返回（实测某外部源一条 ISBN
    返回 3 条）才是噪音，跨源不去重（各有溯源价值）。
    """
    if c.record_id is not None:
        return f"#{c.record_id}"
    from ..core import isbn as isbnlib
    return isbnlib.normalize(c.isbn or "")


def search_chained(sources: list[CatalogSource], isbn: str | None = None,
                   title: str | None = None, author: str | None = None,
                   limit: int = 10) -> list[Candidate]:
    """按优先级串联检索：取回全部源的候选并合并（调用方决定取舍）。

    同一源内的去重口径见 `_same_source_dedup_key`：图书库按记录 id、其余源按归一
    ISBN（外部源常对同一 ISBN 返回多条重复记录，实测某外部源一条 ISBN 返回 3 条）。
    跨源不去重：同一本书来自不同源各有溯源价值，由调用方展示来源区分。
    """
    candidates: list[Candidate] = []
    for src in sorted([s for s in sources if s.config.enabled], key=lambda s: s.config.priority):
        try:
            found = src.search(isbn=isbn, title=title, author=author, limit=limit)
        except Exception as e:  # 单源故障不影响其余源
            candidates.append(Candidate(source_name=src.name, source_key="-error-",
                                        title=f"[源错误：{e}]"))
            continue
        seen: set[str] = set()
        for c in found:
            key = _same_source_dedup_key(c)
            if key:
                if key in seen:
                    continue
                seen.add(key)
            candidates.append(c)
    return candidates


def _round_robin(groups: list[list[Candidate]]) -> list[Candidate]:
    """按"每轮各取一条"合并多源结果。

    为什么不是"一个源排完再排下一个"：翻页时每个源只能拿到自己那一段，若按源
    拼接，第一个源会占满每一页，其余源永远排在后面（而且它们的窗口也在跟着
    偏移，会出现整段被跳过）。轮转合并让每个源在每一页都有位置。
    """
    out: list[Candidate] = []
    depth = max((len(g) for g in groups), default=0)
    for i in range(depth):
        for g in groups:
            if i < len(g):
                out.append(g[i])
    return out


def search_tiered(sources: list[CatalogSource], isbn: str | None = None,
                  title: str | None = None, author: str | None = None,
                  limit: int = 10, page: int = 0,
                  all_tiers: bool = False) -> list[Candidate]:
    """按 `tier` 分层检索：**前一层完全没命中才查下一层**。

    产品规则（用户定）——套录 ISBN 的检索顺序是「图书库 → 资源库 → Z39.50」：

    | tier | 源 | 为什么在这个位置 |
    |---|---|---|
    | 0 | 图书库 | 命中说明**这本书已经编过**，应去改已有条目，而不是再建一条重复记录 |
    | 1 | 本地资源库 | 自有数据，快 |
    | 2 | Z39.50 外部源 | 兜底：慢、不稳，能不用就不用 |

    与 `search_chained` 的区别：那个是"全都查、合并给调用方挑"，这个是"短路"。
    短路既符合编目直觉（先看自己库里有没有），也省掉绝大多数 Z39.50 网络往返。

    `all_tiers=True` 时**不短路**，把各层命中合并返回。用于「库里已经编过，但我想
    看看外部源怎么著录的」——比如库内那条是多年前手编的、字段残缺，想拿 Z39.50
    的规范著录覆盖。默认关闭，因为它意味着每次都要等 Z39.50 的网络往返。

    ## 翻页（`page` 从 0 起）

    每层可能挂了多个源（tier 2 常见 3 个 Z39.50 目标）。页大小 `limit` 按**该层的
    源数**分摊成 `stride`，每个源取自己 `[page*stride, (page+1)*stride)` 那一段，
    再轮转合并、截到 `limit` 条。

    为什么不是"每个源都取 `[page*limit, ...)`"：那样第一个源会占满整页，其余源
    永远看不到；而且各源窗口一起前移，会整段跳过后面源的前几页。分摊 + 轮转是
    唯一"每页都有各源的份、且不重不漏"的做法。

    某一层的源**全部报错**时不算命中，继续往下一层找——不能因为外部源挂了
    就当"这本书没有"，那样会误导编目员重复编目。全部层都没命中时，把源错误透出来。

    ⚠️ 图书库层命中时，**同 ISBN 的多条记录都要原样返回**（多卷书共用 ISBN 是真实
    情形）——去重口径见 `_same_source_dedup_key`，别在这里按 ISBN 折叠。
    """
    tiers: dict[int, list[CatalogSource]] = {}
    for src in [s for s in sources if s.config.enabled]:
        tiers.setdefault(src.config.tier, []).append(src)

    errors: list[Candidate] = []
    hits: list[Candidate] = []
    page = max(0, int(page or 0))
    limit = max(1, int(limit or 10))

    for tier in sorted(tiers):
        group = sorted(tiers[tier], key=lambda s: s.config.priority)
        stride = max(1, -(-limit // max(1, len(group))))   # ceil(limit / 源数)
        offset = page * stride
        per_source: list[list[Candidate]] = []
        for src in group:
            try:
                found = src.search(isbn=isbn, title=title, author=author,
                                   limit=stride, offset=offset)
            except Exception as e:  # 单源故障不影响同层其余源，也不阻断往下一层
                # 必须留一行日志：这一层只要有任何一个源命中，`errors` 就不会被返回，
                # 挂掉的源会被静默吞掉——「配了三个源只出一个结果」查起来毫无线索。
                log.warning("[source] %s 检索失败：%s", src.name, e)
                errors.append(Candidate(source_name=src.name, source_key="-error-",
                                        title=f"[源错误：{e}]"))
                continue
            seen: set[str] = set()
            uniq: list[Candidate] = []
            for c in found:
                key = _same_source_dedup_key(c)
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
                uniq.append(c)
            per_source.append(uniq)
        merged = _round_robin(per_source)[:limit]
        if merged:
            if not all_tiers:
                return merged      # 本层命中 → 短路，不再查后面的层
            hits.extend(merged)    # 全层检索：继续往下找，最后合并
    if hits:
        return hits[:limit]
    return errors
