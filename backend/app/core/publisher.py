# -*- coding: utf-8 -*-
"""出版者反查：ISBN 出版者前缀 → 出版者 + 出版地。

可选数据包 `app/data/publisher_prefixes.json`；未提供时相关查询返回空，
不影响 Z39.50 套录和手工编目。该文件不随公开包提供。

**前缀的形态**：`7-01` / `7-1002` / `988-98498`，即「国家（语言）组 + 出版者代码」，
不含书序号与校验位。匹配时把连字符去掉、**取最长命中**（`7-100` 商务印书馆 优先于
更短的 `7-1…`）。

**局限（务必知情）**：这是一张前缀表，不是完整的 ISBN 出版者代码范围表。
若某书的出版者代码本身不在表里、而它的**前缀**恰好命中，会给出错误出版者
（例如 `978-7-1000-xxxxx` 的代码若是 `1000`，会被 `7-100` 抢中）。
所以本模块的定位是**预填建议**，不是校验——界面必须允许编目员直接改，
且只在字段为空或与建议值不一致时提示，绝不静默覆盖已有值。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# 本文件在 backend/app/core/ 下，所以 parents[1] 就是 backend/app/
DATA = Path(__file__).resolve().parents[1] / "data/publisher_prefixes.json"

_DIGITS = re.compile(r"\D")
# 前缀最长 8 位（去连字符后）；最短 3 位，再短就没有区分度了
_MIN_PREFIX = 3
_MAX_PREFIX = 8


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, dict], dict[str, str]]:
    """返回 (前缀 -> 记录, 出版者 -> 出版地)。文件缺失时返回空表，不抛异常。"""
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    by_prefix: dict[str, dict] = {}
    place_of: dict[str, str] = {}
    for item in raw:
        prefix = _DIGITS.sub("", str(item.get("isbn_prefix") or ""))
        name = (item.get("publisher") or "").strip()
        place = (item.get("place") or "").strip()
        if not prefix or not name:
            continue
        # 同一前缀保留首次出现（来源表已按前缀排序，稳定）
        by_prefix.setdefault(prefix, {"prefix": prefix, "publisher": name, "place": place})
        if place:
            place_of.setdefault(name, place)
    return by_prefix, place_of


def _bodies(isbn: str) -> list[str]:
    """把 ISBN 归一成若干个「可用来匹配前缀」的数字串，按优先级排列。

    - ISBN-13：去掉 `978`/`979` 的 EAN 前缀后剩下的部分
    - ISBN-10：去掉末位校验位
    - 兜底：原样（容错输入了半截号、或号本身不带 EAN）
    """
    digits = _DIGITS.sub("", isbn or "")
    out: list[str] = []
    if len(digits) == 13 and digits[:3] in ("978", "979"):
        out.append(digits[3:])
    if len(digits) == 10:
        out.append(digits[:9])
    out.append(digits)
    return out


def by_isbn(isbn: str | None) -> dict | None:
    """按 ISBN 反查出版者与出版地；未命中返回 None。"""
    by_prefix, _ = _tables()
    if not by_prefix or not isbn:
        return None
    for body in _bodies(isbn):
        top = min(_MAX_PREFIX, len(body))
        for length in range(top, _MIN_PREFIX - 1, -1):
            hit = by_prefix.get(body[:length])
            if hit:
                return hit
    return None


def place_of(publisher: str | None) -> str | None:
    """按出版者名反查出版地（`autofill_fields` 的「出版地对照」用它）。"""
    _, place_of_map = _tables()
    return place_of_map.get((publisher or "").strip())


def geo_map() -> dict[str, str]:
    """整张「出版者 -> 出版地」对照表（供批量填充）。"""
    _, place_of_map = _tables()
    return dict(place_of_map)


def search(q: str | None = None, limit: int = 30) -> list[dict]:
    """按出版者名 / 前缀片段检索候选（供下拉选择）。"""
    by_prefix, _ = _tables()
    items = list(by_prefix.values())
    key = (q or "").strip()
    if key:
        low = key.lower()
        items = [x for x in items
                 if low in x["publisher"].lower() or x["prefix"].startswith(_DIGITS.sub("", key))]
    items.sort(key=lambda x: (len(x["prefix"]), x["publisher"]))
    return items[:limit]


def size() -> int:
    by_prefix, _ = _tables()
    return len(by_prefix)
