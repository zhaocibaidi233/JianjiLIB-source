# -*- coding: utf-8 -*-
"""MARC 文件浏览：上传一次、分页看字段。

为什么要有服务端缓存：一个 较大的 ISO 文件的 ISO 解析要 3 秒左右，而浏览时
用户会来回翻页。每翻一页重解析一次，翻十页就是半分钟——所以按**内容哈希**
缓存解析结果，翻页只从内存切片。

缓存刻意压得很小（最多 2 个文件、合计 120 MB 原始字节）：这是"看一眼"的工具，
不是资料库。超过 30 分钟没人碰就过期，再进来重新解析。
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from ..core.marc import Record
from ..io.iso import iso_field_stats, read_iso, record_rows_json
from ..core.route_meta import api
from .import_export import read_upload

router = APIRouter(prefix="/marc", tags=["marc-browse"])

TTL_SECONDS = 30 * 60
MAX_FILES = 2
MAX_TOTAL_BYTES = 120 * 1024 * 1024
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass
class _Cached:
    filename: str
    size: int
    records: list[Record]
    parse_errors: list[dict]
    stats: list[dict]
    opened_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)


_cache: "OrderedDict[str, _Cached]" = OrderedDict()
_lock = threading.Lock()


def _brief(rec: Record) -> dict:
    """列表行要显示的著录项（与导入预览同一套取值口径）。"""
    return {
        "title": rec.first_value("200", "a") or "",
        "isbn": rec.first_value("010", "a") or "",
        "author": (rec.first_value("200", "f") or rec.first_value("701", "a")
                   or rec.first_value("702", "a") or ""),
        "publisher": rec.first_value("210", "c") or "",
        "year": rec.first_value("210", "d") or "",
        "class_no": rec.first_value("690", "a") or "",
        "barcode": _first_barcode(rec),
    }


def _first_barcode(rec: Record) -> str:
    """记录自带的第一个条码。口径与 `services/batch.py::_record_barcodes` 一致：
    **逐字段**先 `$h`（旧布局）再 `$b`（新布局），没有才看 995。

    逐字段而不是"整条记录二选一"：混合布局的文件里，用 `$h` 的字段与用 `$b` 的
    字段可以并存（已修正）。
    """
    for f in rec.get_fields("905"):
        v = (f.get("h") or f.get("b") or "").strip()
        if v:
            return v
    for f in rec.get_fields("995"):
        for code in ("f", "h", "a"):
            v = (f.get(code) or "").strip()
            if v:
                return v
    return ""


def _page(entry: _Cached, page: int, page_size: int) -> dict:
    total = len(entry.records)
    start = (page - 1) * page_size
    chunk = entry.records[start:start + page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "records": [
            {"index": start + i + 1, **{k: v for k, v in _brief(rec).items()},
             # 一次把整页的字段都带上：展开某一行时不必再请求一次，
             # 20 条 × 30 个字段的 JSON 也就几十 KB。
             "rows": record_rows_json(rec)}
            for i, rec in enumerate(chunk)
        ],
    }


def _summary(entry: _Cached) -> dict:
    return {
        "filename": entry.filename,
        "size": entry.size,
        "total": len(entry.records),
        "parse_errors": entry.parse_errors[:20],
        "parse_error_count": len(entry.parse_errors),
        "field_stats": entry.stats,
    }


def _evict_locked() -> None:
    """清过期 + 超出上限的条目。调用方必须已持有 `_lock`。"""
    now = time.time()
    for key in [k for k, v in _cache.items() if now - v.touched_at > TTL_SECONDS]:
        _cache.pop(key, None)
    while len(_cache) > MAX_FILES:
        _cache.popitem(last=False)
    while len(_cache) > 1 and sum(v.size for v in _cache.values()) > MAX_TOTAL_BYTES:
        _cache.popitem(last=False)


def _lookup(token: str) -> _Cached:
    with _lock:
        entry = _cache.get(token)
        if entry is None:
            raise HTTPException(
                404, "文件浏览已过期（超过 30 分钟），请重新上传")
        if time.time() - entry.touched_at > TTL_SECONDS:
            _cache.pop(token, None)
            raise HTTPException(404, "浏览记录已过期，请重新上传")
        entry.touched_at = time.time()
        _cache.move_to_end(token)
        return entry


@router.post("/browse")
@api(module="records")
def open_marc_file(file: UploadFile = File(...),
                   page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)):
    """解析一个 MARC 文件并缓存，返回首页。同一个文件重复上传不会重复解析。"""
    data = read_upload(file)
    if not data:
        raise HTTPException(400, "文件是空的")
    token = hashlib.sha256(data).hexdigest()[:32]

    with _lock:
        cached = _cache.get(token)
        if cached is not None and time.time() - cached.touched_at <= TTL_SECONDS:
            cached.touched_at = time.time()
            _cache.move_to_end(token)
            entry = cached
        else:
            entry = None

    if entry is None:
        records, parse_errors = read_iso(data)
        if not records:
            raise HTTPException(400, f"未解析到任何记录：{parse_errors[:3]}")
        entry = _Cached(filename=file.filename or "未命名",
                        size=len(data), records=records,
                        parse_errors=parse_errors, stats=iso_field_stats(records))
        with _lock:
            _cache[token] = entry
            _cache.move_to_end(token)
            _evict_locked()

    return {"token": token, **_summary(entry), **_page(entry, 1, page_size)}


@router.get("/browse/{token}")
@api(module="records")
def read_marc_page(token: str, page: int = Query(1, ge=1),
                   page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)):
    """翻页。只从内存切片，不再碰文件。"""
    entry = _lookup(token)
    if (page - 1) * page_size >= len(entry.records):
        raise HTTPException(404, "页码超出范围")
    return {"token": token, **_page(entry, page, page_size)}


@router.delete("/browse/{token}")
@api(module="records")
def close_marc_file(token: str):
    """主动释放缓存（关掉浏览面板时调用）。不存在也返回 200，幂等。"""
    with _lock:
        _cache.pop(token, None)
    return {"ok": True}
