# -*- coding: utf-8 -*-
"""图书库解析（默认库回落）。

## 为什么在 services 层

`services/batch.py` 的 ISO 导入要解析落点库，原先反向 import
`api/book_libraries.py` —— 分层倒挂（`services → api`）。搬到这儿之后
`api → services` 单向，导入逻辑可以脱离 FastAPI 单独复用与测试。

## 错误类型

这里抛 `ValueError` 而不是 `HTTPException`：**services 层不该知道 HTTP**。
API 层负责把 `ValueError` 翻译成 404/400（见 `api/book_libraries.py`）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import BookLibrary

#: 没有指定库、库里也一个都没有时自动建的名字
DEFAULT_LIBRARY_NAME = "默认图书库"


def get_or_create_default(db: Session) -> BookLibrary:
    """取第一个图书库；一个都没有就建一个（**会 commit**）。"""
    lib = db.query(BookLibrary).order_by(BookLibrary.id).first()
    if lib is None:
        lib = BookLibrary(name=DEFAULT_LIBRARY_NAME, note="系统自动创建")
        db.add(lib)
        db.commit()
        db.refresh(lib)
    return lib


def resolve_library(db: Session, library_id: int | None) -> BookLibrary:
    """请求未指定库时落到默认库；指定的库不存在 → `ValueError`。"""
    if library_id:
        lib = db.get(BookLibrary, library_id)
        if lib is None:
            raise ValueError(f"图书库不存在：{library_id}")
        return lib
    return get_or_create_default(db)
