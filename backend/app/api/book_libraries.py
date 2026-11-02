# -*- coding: utf-8 -*-
"""图书库管理（每校/每馆一个；编目成品按库分区，书次号按库独立）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BookLibrary, RecordORM, Setting
from ..core.route_meta import api

router = APIRouter(prefix="/book-libraries", tags=["book-libraries"])


# 排架号开关的设置键后缀（· C2 /）。
# 按**图书库**隔离，与条码 `lib{id}|__barcode__`、注销号 `lib{id}|__withdraw__`、
# 书次号 `lib{id}|{class_no}` 是同一套 `lib{id}|` 前缀约定。
SHELF_NO_SETTING_SUFFIX = "shelf_no"


def shelf_no_key(library_id: int) -> str:
    """排架号开关的设置键（按图书库隔离）。"""
    return f"lib{library_id}|{SHELF_NO_SETTING_SUFFIX}"


def shelf_no_enabled(db: Session, library_id: int | None) -> bool:
    """读取开关；未配置（行不存在）一律 false —— 排架号默认关闭。

    开关的语义是「这个库要不要在录入界面上看到排架号」，**不是**「这个库的数据里
    有没有排架号」：关闭时后端仍接受 `shelf_no` 入参、905 `$c` 仍输出历史值。
    """
    lib = resolve_library(db, library_id)
    row = db.get(Setting, shelf_no_key(lib.id))
    return bool(row and (row.value or {}).get("enabled", False))


# 解析与默认库回落已下沉到 `services/book_libraries.py`（分层倒挂修复：
# `services/batch.py` 曾反向 import 本模块）。这里 re-export 保持老调用点可用。
from ..services.book_libraries import (  # noqa: F401  (re-export)
    DEFAULT_LIBRARY_NAME,
    get_or_create_default,
)
from ..services.book_libraries import resolve_library as _resolve_library


def resolve_library(db: Session, library_id: int | None):
    """`services.resolve_library` 的 HTTP 包装：不存在时 404。

    services 层抛 `ValueError`（它不该知道 HTTP）；本模块的 8 个调用点都期望
    「库不存在 → 404」，所以在这里统一翻译，调用点不用改。
    """
    try:
        return _resolve_library(db, library_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class BookLibraryIn(BaseModel):
    name: str
    note: str | None = None


@router.get("")
@api(module="settings", capability="admin.settings.read")
def list_libraries(db: Session = Depends(get_db)):
    libs = db.query(BookLibrary).order_by(BookLibrary.id).all()
    counts = dict(db.query(RecordORM.library_id, func_count()).group_by(RecordORM.library_id).all())
    return [{"id": l.id, "name": l.name, "note": l.note,
             "record_count": counts.get(l.id, 0)} for l in libs]


def func_count():
    from sqlalchemy import func
    return func.count(RecordORM.id)


@router.post("")
@api(module="settings")
def create_library(payload: BookLibraryIn, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if db.query(BookLibrary).filter(BookLibrary.name == name).first():
        raise HTTPException(409, "同名图书库已存在")
    lib = BookLibrary(name=name, note=payload.note)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return {"id": lib.id, "name": lib.name}


# ===== 排架号开关（按图书库）=====
# ⚠️ 注册顺序：**必须**在 `PUT /{library_id}` 与 `DELETE /{library_id}` 之前。
# 这是本项目踩过的坑：静态路径被动态路径抢先匹配。别指望"看路径段数不会冲突"。
@router.get("/settings/shelf-no")
@api(module="settings", capability="admin.settings.read")
def get_shelf_no_setting(library_id: int | None = None, db: Session = Depends(get_db)):
    """读某个图书库的排架号开关（未指定 → 默认图书库）。行不存在即 false。"""
    lib = resolve_library(db, library_id)
    return {"library_id": lib.id, "enabled": shelf_no_enabled(db, lib.id)}


class ShelfNoSettingIn(BaseModel):
    library_id: int | None = None
    enabled: bool


@router.put("/settings/shelf-no")
@api(module="settings")
def put_shelf_no_setting(payload: ShelfNoSettingIn, db: Session = Depends(get_db)):
    """写排架号开关。value 留 JSON 壳 `{"enabled": bool}`，P1 可扩展（如校验模板）。"""
    lib = resolve_library(db, payload.library_id)
    key = shelf_no_key(lib.id)
    value = {"enabled": bool(payload.enabled)}
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()
    return {"saved": True, "library_id": lib.id, "enabled": bool(payload.enabled)}


@router.put("/{library_id}")
@api(module="settings")
def update_library(library_id: int, payload: BookLibraryIn, db: Session = Depends(get_db)):
    lib = db.get(BookLibrary, library_id)
    if lib is None:
        raise HTTPException(404, "图书库不存在")
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    dup = db.query(BookLibrary).filter(BookLibrary.name == name,
                                       BookLibrary.id != library_id).first()
    if dup:
        raise HTTPException(409, "同名图书库已存在")
    lib.name, lib.note = name, payload.note
    db.commit()
    return {"id": lib.id, "name": lib.name}


@router.delete("/{library_id}")
@api(module="settings")
def delete_library(library_id: int, db: Session = Depends(get_db)):
    lib = db.get(BookLibrary, library_id)
    if lib is None:
        raise HTTPException(404, "图书库不存在")
    if db.query(BookLibrary).count() <= 1:
        raise HTTPException(400, "至少保留一个图书库")
    n = db.query(RecordORM).filter(RecordORM.library_id == library_id).count()
    if n:
        raise HTTPException(400, f"该库还有 {n} 条记录，不能删除（请先删除或迁移记录）")
    db.delete(lib)
    db.commit()
    return {"deleted": library_id}