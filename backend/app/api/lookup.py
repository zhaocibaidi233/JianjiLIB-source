# -*- coding: utf-8 -*-
"""字典与反查接口：出版者 / 出版地 / 语种 / 责任方式 / 文献类型 等。

内置字典数据以 JSON 文件随包提供（`app/data/vendor_*.json`）——它们是**参考数据**，
不是用户数据，所以不进数据库，也不受迁移影响。

⚠️ 自统一字典中心起，**语种 / 责任方式 / 文献类型 /
获得方式 / 付款方式**这几类的**生效列表**改由 `services/dictionaries.effective()`
提供 —— 也就是「内置默认 + 用户在字典中心的增删」。本文件的 URL 与响应结构**没变**，
所以调用方（编目页）无感，但用户改了字典就会立刻反映到候选里。

用途：
  - `GET /lookup/publisher?isbn=` 编目页输入 ISBN 后自动带出出版者与出版地
  - `GET /lookup/publishers` / `places` / `languages` 简编表单的候选下拉
  - `GET /lookup/dicts` 责任方式（$4）等小字典一次性取回
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core import publisher as pub
from ..db import get_db
from ..services import dictionaries as dicts
from ..core.route_meta import api

router = APIRouter(prefix="/lookup", tags=["lookup"])


@router.get("/publisher")
@api(module="records")
def lookup_publisher(isbn: str = Query("", description="ISBN（10/13 位，可带连字符）")):
    """按 ISBN 反查出版者与出版地（输入 ISBN 后自动带出出版者和出版地）。

    定位是**预填建议**，不是校验：前缀表不完整，也可能被更短的出版者代码抢中。
    调用方必须在字段已有值且与建议不一致时提示，而不是直接覆盖。
    """
    hit = pub.by_isbn(isbn)
    if hit is None:
        message = ("未提供出版者前缀数据包；请使用套录或手工填写"
                   if pub.size() == 0 else "出版者前缀表中没有该出版者代码")
        return {"found": False, "isbn": isbn, "message": message}
    return {"found": True, "isbn": isbn, **hit}


@router.get("/place")
@api(module="records")
def lookup_place(publisher: str = Query("", description="出版者名称（精确匹配）")):
    """按出版者名反查出版地（编目页填完出版者后自动补出版地用）。"""
    place = pub.place_of(publisher)
    return {"found": bool(place), "publisher": publisher, "place": place}


@router.get("/publishers")
@api(module="records")
def lookup_publishers(q: str | None = None, limit: int = Query(30, ge=1, le=200)):
    """出版者候选（按名称片段或 ISBN 前缀片段检索）。"""
    items = pub.search(q, limit)
    return {"total": pub.size(), "shown": len(items), "items": items}


@router.get("/places")
@api(module="records")
def lookup_places(limit: int = Query(100, ge=1, le=500)):
    """出版地候选（从出版者表里去重；按出现频次降序）。"""
    from collections import Counter
    counter = Counter(v for v in pub.geo_map().values() if v)
    top = [p for p, _ in counter.most_common(limit)]
    return {"total": len(counter), "items": top}


@router.get("/languages")
@api(module="records")
def lookup_languages(q: str | None = None, limit: int = Query(50, ge=1, le=300),
                     db: Session = Depends(get_db)):
    """语种候选（供 101$a 选择；code 是 ISO 639 三字母码，存进 MARC 的就是它）。

    `total` 是**生效列表**的条数（字典中心改过就是改后的数）。
    """
    rows = dicts.effective(db, "languages")
    key = (q or "").strip().lower()
    if key:
        rows = [r for r in rows
                if key in str(r.get("name", "")).lower() or key in str(r.get("code", "")).lower()]
    return {"total": len(dicts.effective(db, "languages")), "shown": min(len(rows), limit),
            "items": rows[:limit]}


@router.get("/dicts")
@api(module="records")
def lookup_dicts(db: Session = Depends(get_db)):
    """小字典一次性取回：责任方式（$4）+ 文献类型 / 获得方式 / 付款方式 / 阅览室。

    前四类走字典中心的生效列表；**阅览室**不属基础字典的 7 类
    （只在 `sys_Dictionary` 里），故仍读随包默认、不进字典中心。
    """
    return {
        "responsibility": dicts.effective(db, "responsibility"),
        "item_types": dicts.effective(db, "item_types"),
        "acquisition": dicts.effective(db, "acquisition"),
        "payment": dicts.effective(db, "payment"),
        "readrooms": dicts.sys_dict("阅览室"),
    }


@router.get("/stats")
@api(module="records")
def lookup_stats():
    """数据规模自述（设置页显示用）。

    ⚠️ 这里刻意报**随包数据的规模**（不是生效列表）——它是"我们带了多少参考数据"的自述，
    与用户在字典中心增删多少无关；也避免测试之间互相耦合。
    """
    d = dicts._vendor_dicts()
    return {
        "publishers": pub.size(),
        "places": len({v for v in pub.geo_map().values() if v}),
        "languages": len(dicts.sys_dict("出版物语种")),
        "responsibility": len(d.get("responsibility", [])),
    }
