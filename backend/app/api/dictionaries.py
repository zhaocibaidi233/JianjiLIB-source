# -*- coding: utf-8 -*-
"""统一字典中心接口。

系统内置的 7 类基础数据，统一在这里查看与修改：
馆藏地点 / 货币类型 / 文献类型 / 获得方式 / 出版物语种 / 付款方式 / 责任方式。

存储与容错口径见 `services/dictionaries.py`（内置默认来自随包 JSON，
用户增删结果存 `settings['dictionaries']`，**不新建表、无需迁移**）。

⚠️ 本路由的路径只有两种形态：`/{key}`（单段）与 `/currencies/default`（两段固定）。
**刻意不用 `/default-currency` 这种单段固定路径** —— 那会与 `GET /{key}` 撞车，
必须靠"注册顺序"才能保证命中，而路由顺序正是本项目踩过的坑（见实现说明）。
两段路径天然不会与单段的 `/{key}` 产生歧义。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import dictionaries as dicts
from ..core.route_meta import api

router = APIRouter(prefix="/dictionaries", tags=["dictionaries"])


class DictItemsIn(BaseModel):
    """条目形态随类型而变（simple = 字符串；coded = {code, name}），故此处只收原始数组，
    由 `services.dictionaries.normalize_items` 按类型做**唯一一份**校验。"""

    items: list


class DefaultCurrencyIn(BaseModel):
    """本馆默认币种（定价 `010$d` 的货币码）。"""

    code: str


def _currency_name(db: Session, code: str) -> str:
    for c in dicts.effective(db, "currencies"):
        if isinstance(c, dict) and c.get("code") == code:
            return str(c.get("name") or "")
    return ""


def _meta_payload(db: Session, key: str) -> dict:
    t = dicts.type_meta(key)
    items = dicts.effective(db, key)
    return {
        "key": t["key"],
        "label": t["label"],
        "kind": t["kind"],
        "items": items,
        "count": len(items),
        "builtin_count": len(dicts._builtin(key)),
        "customized": dicts.is_customized(db, key),
    }


@router.get("")
@api(module="dictionaries")
def list_dictionaries(db: Session = Depends(get_db)):
    """7 类字典一次性取回（含生效列表）。界面据此渲染，不硬编码任何一份清单。"""
    types = []
    for t in dicts.DICT_TYPES:
        payload = _meta_payload(db, t["key"])
        payload["count"] = len(payload["items"])
        types.append(payload)
    return {"types": types}


@router.get("/{key}")
@api(module="dictionaries")
def get_dictionary(key: str, db: Session = Depends(get_db)):
    """取某一类字典（含生效列表）。未知类型 → 404。"""
    if key not in dicts.known_keys():
        raise HTTPException(404, "未知字典类型")
    return _meta_payload(db, key)


@router.put("/{key}")
@api(module="dictionaries")
def put_dictionary(key: str, payload: DictItemsIn, db: Session = Depends(get_db)):
    """整体保存某一类字典（覆盖式）。内容非法 → 400，消息可直接展示给用户。"""
    if key not in dicts.known_keys():
        raise HTTPException(404, "未知字典类型")
    try:
        items = dicts.save_type(db, key, payload.items)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"saved": True, "key": key, "items": items, "count": len(items),
            "customized": True}


@router.post("/{key}/reset")
@api(module="dictionaries")
def reset_dictionary(key: str, db: Session = Depends(get_db)):
    """恢复该类的内置默认（丢弃用户那份）。"""
    if key not in dicts.known_keys():
        raise HTTPException(404, "未知字典类型")
    items = dicts.reset_type(db, key)
    return {"saved": True, "key": key, "items": items, "count": len(items),
            "customized": False}


# ---------------------------------------------------------------------------
#  本馆默认币种（定价 `010$d` 的货币码）
#
#  路径是**两段**的 `/currencies/default`：与单段的 `/{key}` 不会撞车，
#  不必依赖注册顺序（见模块开头说明）。
# ---------------------------------------------------------------------------

@router.get("/currencies/default")
@api(module="dictionaries")
def get_default_currency(db: Session = Depends(get_db)):
    """本馆默认币种。没设过 / 设过的码已被删掉 → 回退到货币列表第一条。"""
    code = dicts.default_currency(db)
    return {"code": code, "name": _currency_name(db, code)}


@router.put("/currencies/default")
@api(module="dictionaries")
def put_default_currency(payload: DefaultCurrencyIn, db: Session = Depends(get_db)):
    """设置本馆默认币种；码必须来自「货币类型」字典，否则 400。"""
    try:
        code = dicts.set_default_currency(db, payload.code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"saved": True, "code": code, "name": _currency_name(db, code)}
