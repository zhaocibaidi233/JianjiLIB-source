# -*- coding: utf-8 -*-
"""快速录入：ISBN 一次请求完成 查重→套录→联机回存→临时副本。

新架构（用户约定，参照旧版系统双库模式）：
  套录命中不直接写图书库（records）——图书库只存编目校验好的记录。
  本接口返回本地化改写后的临时副本，前端校对面板编辑，完成时 POST /records
  （质检门槛：无 ERROR 才入库）。Z39.50 命中自动回存资源库。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai.fields import autofill_fields
from ..core import isbn as isbnlib
from ..db import get_db
from ..models import RecordORM, ResourceRecordORM
from ..services import class_no_rule, localize_record

from ..services.resource_cache import (get_or_create_online_library,
                                       upsert_resource_record)
from ..core.route_meta import api

router = APIRouter(prefix="/quick-entry", tags=["quick-entry"])


class QuickEntryIn(BaseModel):
    isbn: str
    agency: str = "LOCAL"
    library_id: int | None = None   # 查重范围（同书异校不算重）


@router.post("")
@api(module="records")
def quick_entry(payload: QuickEntryIn, db: Session = Depends(get_db)):
    forms = isbnlib.all_forms(payload.isbn)
    if not forms:
        raise HTTPException(400, f"ISBN 无法解析：{payload.isbn}")

    # 1) 查重：目标图书库已有 → 已编目（同书异校不算重）
    q = db.query(RecordORM)
    if payload.library_id:
        q = q.filter(RecordORM.library_id == payload.library_id)
    existing = q.filter(RecordORM.isbn_norm.in_(forms)).first()
    if existing is not None:
        # 命中已编目：**不只回一句"已有"**，把这条记录的完整数据一并带回去，
        # 让前端能给出和通用套录框一样的三个选择（套录这条 / 打开这条 /
        # 同号不同书继续新增）。
        #
        # 为什么必须带 record：同 ISBN 在真实馆藏里可能是**另一本书**（同号不同书：
        # 出版社复用号、套装书共用一个号、数据源错号），也可能是**同一本书的
        # 另一册/另一卷**。两种情况编目员要的事完全不同——前者要"另编一册并改字段"，
        # 后者只是"加复本"。只给一个"已编目"的死路，等于把判断成本全推给人。
        from ..services import orm_to_record

        rec = orm_to_record(existing)
        return {
            "status": "exists", "record_id": existing.id,
            "detail": {"id": existing.id, "title": existing.title_norm,
                       "isbn": existing.isbn_norm},
            # 与 /cataloging/search 的图书库候选同构，前端直接塞进同一个候选弹窗
            "candidate": {
                "source": "图书库", "key": str(existing.id),
                "title": existing.title_norm, "author": existing.author_norm,
                "publisher": rec.first_value("210", "c") or None,
                "year": existing.year, "class_no": existing.class_no,
                "isbn": existing.isbn_norm,
                "record_id": existing.id,
                "volume": rec.first_value("200", "h") or None,
                "isbn_key": isbnlib.canonical(existing.isbn_norm)
                if existing.isbn_norm else None,
                "record": {"leader": rec.leader, "fields": json.loads(rec.to_json())},
            },
            "auto_applied": [],
        }

    # 2) 套录：图书库 → 本地资源库 → Z39.50，分层短路（前一层命中就不查下一层）。
    #    图书库在第 1 步已查过（命中就 return 了），所以这里通常直接落到资源库。
    rec = None
    hit_source = None
    from ..sources.base import search_tiered
    from ..sources.registry import active_sources
    cands = search_tiered(active_sources(db, payload.library_id),
                          isbn=payload.isbn, limit=1)
    for c in cands:
        if c.record is not None and not c.title.startswith("[源错误"):
            rec = c.record
            hit_source = c.source_name
            break

    if rec is None:
        return {"status": "miss", "isbn": payload.isbn,
                "hint": "全部源未命中：可手工编目（新建记录）或转 AI 原始编目"}

    # 3) 联机源命中 → 自动回存资源库（同 ISBN 更新，资源库随套录增值）
    resource_action = None
    if hit_source != "本地资源库":
        lib = get_or_create_online_library(db)
        # 停用的资源库只停止参与检索与回存；命中记录仍可正常载入编目。
        if lib.enabled:
            resource_action = upsert_resource_record(db, rec, lib.id, source_tag=hit_source)
            lib.record_count = (db.query(ResourceRecordORM)
                                .filter(ResourceRecordORM.library_id == lib.id).count())
            db.commit()

    # 4) 本地化改写 + 规则层补全 → 临时副本（图书库只存校验好的，完成时 POST /records）
    #    套录时按全局设置截短过长分类号（690$a）；库里已有记录不受影响。
    rec, stats = localize_record(rec, agency=payload.agency,
                                 class_rule=class_no_rule(db))
    applied = autofill_fields(rec)

    return {
        "status": "ok",
        "hit_source": hit_source,
        "resource_action": resource_action,
        # volume 缺省 = 200$h（分册号）：与 /cataloging/prepare 同一约定——
        # 同套多卷（囧成语 $h1..6）套录下来 volume 全空，编辑页没有"第几册"可看。
        "record": {"leader": rec.leader, "source_name": hit_source,
                   "fields": json.loads(rec.to_json()), "mrk": rec.to_mrk(),
                   "volume": rec.first_value("200", "h") or None},
        "auto_applied": applied,
    }
