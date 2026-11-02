# -*- coding: utf-8 -*-
"""CNMARC 字段字典接口（只读）。

编辑器（记录编辑页的「全字段 MARC」）与字段查看界面用它把**字段号翻译成中文名**，
不再各自硬编码一份字段中文名。数据由 `services/marc_fields.py` 从随包字典读取。

⚠️ 归属「字典中心」模块（`core/perm.py`）：它是"读一份规范数据"，与其他字典同类。
路径 `/api/marc-fields` **刻意不放在 `/api/dictionaries/` 下** —— 那个路由有
`GET /{key}` 单段动态路径，`/api/dictionaries/marc-fields` 会被它抢走（必须靠注册
顺序区分），而路由顺序正是本项目踩过的坑（见 `api/dictionaries.py` 模块 docstring）。
独立前缀没有这个歧义。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..services import marc_fields as mf
from ..core.route_meta import api

router = APIRouter(prefix="/marc-fields", tags=["marc-fields"])


@router.get("")
@api(module="dictionaries")
def list_marc_fields():
    """返回 CNMARC 字段字典：`{count, note, fields:[{tag,name,required,control,subfields}]}`。

    只读、无参数。`note` 是字段字典的来源说明。
    """
    return {"count": mf.count(), "note": mf.source_note(), "fields": mf.all_fields()}
