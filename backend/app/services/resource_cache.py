# -*- coding: utf-8 -*-
"""资源库缓存服务：联机套录下载自动回存（参照旧版系统的 res 资源库 资源库）。

Z39.50 命中的记录自动 upsert 进"联机套录下载"资源库：同 ISBN 已存在则更新
记录与时间戳，不存在则新增——资源库随套录使用持续增值。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..core import clean as cl
from ..core import isbn as isbnlib
from ..core.marc import MarcWriteError, Record, record_to_iso
from ..models import ResourceLibrary, ResourceRecordORM
from ..services import compute_search_columns

ONLINE_LIBRARY_NAME = "联机套录下载"


def get_or_create_online_library(db: Session) -> ResourceLibrary:
    lib = db.query(ResourceLibrary).filter(ResourceLibrary.name == ONLINE_LIBRARY_NAME).first()
    if lib is None:
        lib = ResourceLibrary(name=ONLINE_LIBRARY_NAME,
                              origin="Z39.50 联机套录自动下载缓存", enabled=True)
        db.add(lib)
        db.flush()
    return lib


def _iso_bytes(rec: Record) -> bytes:
    try:
        return record_to_iso(rec, encoding="gbk")
    except MarcWriteError:
        return record_to_iso(rec, encoding="utf-8")


def upsert_resource_record(db: Session, rec: Record, library_id: int,
                           source_tag: str | None = None) -> str:
    """按 ISBN upsert 资源库记录（同库内 ISBN-10/13 跨形态匹配）。

    返回 'inserted' / 'updated'；无 ISBN 的记录直接入库、不去重（也返回 'inserted'）。
    """
    from ..models import Setting  # noqa: F401  (确保模型注册)
    import json

    cl.clean_record(rec, migrate_pinyin=True)
    cols = compute_search_columns(rec)
    raw = _iso_bytes(rec)

    existing = None
    if cols["isbn_norm"]:
        # 必须限定在目标库内匹配：资源库按来源相互独立（某馆配商/某外部源/手工导入），
        # 不加 library_id 会把别的库的同 ISBN 记录"更新"掉，数据直接错位。
        # 同时按等价形态匹配（ISBN-10/13 互转），避免同一本书因形态不同重复入库；
        # 命中旧形态记录时下面的 setattr 会顺带把它统一成 ISBN-13。
        forms = isbnlib.all_forms(cols["isbn_norm"]) or {cols["isbn_norm"]}
        existing = (db.query(ResourceRecordORM)
                    .filter(ResourceRecordORM.library_id == library_id,
                            ResourceRecordORM.isbn_norm.in_(forms))
                    .first())
    if existing is not None:
        existing.marc_raw = raw
        existing.leader = rec.leader
        existing.fields = json.loads(rec.to_json())
        existing.source_tag = source_tag or existing.source_tag
        for k, v in cols.items():
            setattr(existing, k, v)
        return "updated"

    db.add(ResourceRecordORM(library_id=library_id, marc_raw=raw, leader=rec.leader,
                             fields=json.loads(rec.to_json()), source_tag=source_tag, **cols))
    # **必须 flush**：本项目的 Session 是 `autoflush=False`（见 db.py），
    # `db.add()` 之后待写对象对后续查询不可见。批量导入是"边查边插"的循环，
    # 不 flush 的话同一批里第 2 条同 ISBN 的记录查不到第 1 条（还在待写队列里），
    # 于是又插一条——跨批导入因为前一批已 commit 所以看不出问题，
    # 只有**同批内重复**才暴露（实测同 ISBN 与 ISBN-10/13 都会重复落库）。
    db.flush()
    return "inserted"


def bump_library_count(db: Session, library_id: int, delta: int) -> None:
    from ..models import ResourceLibrary
    lib = db.get(ResourceLibrary, library_id)
    if lib is not None:
        lib.record_count = max(0, (lib.record_count or 0) + delta)


DEFAULT_IMPORT_LIBRARY_NAME = "手工导入"


def resolve_resource_library(db: Session, library_id: int | None) -> ResourceLibrary:
    """按 id 取资源库；不传 → 「手工导入」库（缺则建）。传了不存在的 id → ValueError。

    与 `api.book_libraries.resolve_library` 对称：**指定了就不许静默落到默认库**，
    否则用户以为导进了 A 库、实际进了默认库，事后很难查。
    """
    if library_id:
        lib = db.get(ResourceLibrary, library_id)
        if lib is None:
            raise ValueError(f"资源库不存在：{library_id}")
        return lib
    lib = (db.query(ResourceLibrary)
           .filter(ResourceLibrary.name == DEFAULT_IMPORT_LIBRARY_NAME).first())
    if lib is None:
        lib = ResourceLibrary(name=DEFAULT_IMPORT_LIBRARY_NAME,
                              origin="ISO 手工导入", enabled=True)
        db.add(lib)
        db.flush()
    return lib


def import_iso_records_to_library(db: Session, records: list[Record],
                                  library_id: int | None, source_tag: str) -> dict:
    """把一批 Record **按 ISBN upsert** 进资源库（资源库是"原样原料"，不做本地化改写）。

    ISO 导入资源库的**唯一实现**——同步接口（`/import/iso?target=resource`、
    `/resource-libraries/{id}/import-iso`）与后台任务都走它，避免三处各写一遍。
    """
    lib = resolve_resource_library(db, library_id)
    added = updated = no_isbn = 0
    for rec in records:
        action = upsert_resource_record(db, rec, lib.id, source_tag=source_tag)
        if action == "updated":
            updated += 1
        else:
            added += 1
            if not compute_search_columns(rec)["isbn_norm"]:
                no_isbn += 1
    # record_count 以表内实况为准，避免 "+added" 口径随重复导入越漂越偏
    lib.record_count = (db.query(ResourceRecordORM)
                        .filter(ResourceRecordORM.library_id == lib.id).count())
    return {"target": "resource", "library": lib.name, "library_id": lib.id,
            "added": added, "updated": updated, "no_isbn": no_isbn,
            "record_count": lib.record_count}
