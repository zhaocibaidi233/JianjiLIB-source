# -*- coding: utf-8 -*-
"""书目记录 CRUD + 查重 + 质检。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, literal_column, select, union
from sqlalchemy.orm import Session

from ..core import clean as cl
from ..core import isbn as isbnlib
from ..core.marc import MarcWriteError
from ..core.operator import operator_of
from ..core.validators import LEVEL_ERROR, validate
from ..db import get_db, like_escape
from ..models import HoldingORM, RecordORM
from ..api.book_libraries import resolve_library
from ..core.volume import (SHELF_NO_MAX_LEN, VOLUME_MAX_LEN, call_no_base,
                           derive_volume_seq)
from ..schemas import RecordPage
from ..services import (record_from_payload, record_to_orm_values, orm_to_record,
                        record_projection)
from ..services import reservations as reservation_service
from ..services.circulation import active_loan_record_ids, lock_record
from ..services.batch import (apply_volume_to_holdings, create_holdings,
                              set_book_no)
from ..services.sorting import apply_sort
from ..services.paging import paginate
from ..core.route_meta import api

router = APIRouter(tags=["records"])

# 列表**可排序的列**（白名单，`?sort=` 只能取这里的键 —— 见 `services/sorting.py`）。
# 键与前端表格列的 `prop` 对齐。⚠️ 索书号不在其中：它由 `holdings.call_no` 拼出来，
# 不是 records 的列，排它要 join 馆藏（代价大）；按分类号排已经够用。
# 条码同理不是 records 的列：按该记录**最小条码**（列表里展示的就是这一条）排序。
_first_barcode = (
    select(func.min(HoldingORM.barcode))
    .where(HoldingORM.record_id == RecordORM.id)
    .correlate(RecordORM)
    .scalar_subquery()
)
# 卷册/排架已归 Item；列表排序用“第一册的非空值”作展示投影。
_first_volume = (
    select(HoldingORM.volume)
    .where(HoldingORM.record_id == RecordORM.id,
           HoldingORM.volume.isnot(None), HoldingORM.volume != "")
    .order_by(HoldingORM.id).limit(1)
    .correlate(RecordORM)
    .scalar_subquery()
)
_first_shelf_no = (
    select(HoldingORM.shelf_no)
    .where(HoldingORM.record_id == RecordORM.id,
           HoldingORM.shelf_no.isnot(None), HoldingORM.shelf_no != "")
    .order_by(HoldingORM.id).limit(1)
    .correlate(RecordORM)
    .scalar_subquery()
)
# 复本数 = 该记录名下有条码的馆藏册数（与列表 ×N 的口径一致）。
_copy_count = (
    select(func.count(HoldingORM.id))
    .where(HoldingORM.record_id == RecordORM.id,
           HoldingORM.barcode.isnot(None), HoldingORM.barcode != "")
    .correlate(RecordORM)
    .scalar_subquery()
)
def _first_subfield(fields, tag: str, code: str) -> str | None:
    """从库里存的 MARC JSON 取某个子字段的第一个非空值。

    为什么不去构造完整的 `Record`：列表只用到 215$a / 215$d 两个值，
    为几十条记录各建一遍 Field/Subfield 对象纯属浪费。这里的 `fields` 就是
    记录行上已经加载好的 JSONB，不额外查库。

    215$a 是页数（"267页"），215$d 是尺寸（"21cm"）——两者都可能缺失，
    缺失时返回 None，前端显示「—」。
    """
    for item in fields or []:
        if item.get("tag") != tag:
            continue
        for pair in item.get("sf") or []:
            if len(pair) == 2 and pair[0] == code:
                value = (pair[1] or "").strip()
                if value:
                    return value
    return None


def _marc_sort_expr(tag: str, code: str):
    """排序用的 MARC 子字段表达式（SQL 侧，只出现在 ORDER BY 里）。

    为什么不用 `jsonb_path_query_first` 的过滤写法：`$[*] ? (@.tag == "215")
    .sf[*] ? (@[0] == "a")` 在这里返回的是**子字段代码本身**（"a"），拿不到
    「代码 + 值」这一对，后面再取下标就是空的。用 LATERAL 把 `sf` 展开成行，
    语义清楚、结果可验证。

    为什么取**开头**的数字而不是把串里所有数字拼起来：真实数据里有
    `215$a = "8 光盘 (CD) (57, 51, 52, 52, 49, ...)"` 这种脏值，拼起来会得到
    一个天文数字，排到最前面。只认开头那段数字，脏值自然沉到末尾（NULL）。

    代价：排序时每行算一次子查询，开销可接受。
    """
    value = (
        "(SELECT e.value->>1 FROM jsonb_array_elements(records.fields) AS f "
        "CROSS JOIN LATERAL jsonb_array_elements(f.value->'sf') AS e "
        f"WHERE f.value->>'tag' = '{tag}' AND e.value->>0 = '{code}' LIMIT 1)"
    )
    return literal_column(
        f"CAST(NULLIF(substring({value} from '^[0-9]+'), '') AS BIGINT)")


RECORD_SORTS = {
    "id": RecordORM.id,
    "title": RecordORM.title_norm,
    "isbn": RecordORM.isbn_norm,
    "author": RecordORM.author_norm,
    "year": RecordORM.year,
    "class_no": RecordORM.class_no,
    # 排架号/分卷号已归 Item，排序用第一册的非空值投影；页数/尺寸埋在 MARC 215 里
    "shelf_no": _first_shelf_no,
    "volume": _first_volume,
    "pages": _marc_sort_expr("215", "a"),
    "size": _marc_sort_expr("215", "d"),
    "created_by": RecordORM.created_by,
    "source": RecordORM.source,
    "barcode": _first_barcode,
    "copies": _copy_count,
}


def _norm(v: str | None) -> str | None:
    """落库归一：'' / 全空白 -> NULL；其余 strip 后落库。"""
    return (v or "").strip() or None


def _iso_utc(dt) -> str | None:
    """`updated_at` 的**比较用**表示：统一成 UTC ISO（秒精度、无时区后缀）。

    详情接口回给前端的是 `str(orm.updated_at)`（形如 `2026-10-25 03:12:41.123456+08:00`），
    这里必须与它同源才能比较 —— 所以**不重新格式化**，只在必要时补时区：
    naive 时间按 UTC 处理（DB 列是 timestamptz，正常不会是 naive）。
    """
    if dt is None:
        return None
    return str(dt)


def _check_extra_len(volume: str | None, shelf_no: str | None) -> None:
    """长度硬上限（列宽）。**禁止静默截断**，超长直接 400。"""
    if volume is not None and len(volume) > VOLUME_MAX_LEN:
        raise HTTPException(400, f"卷册号最多 {VOLUME_MAX_LEN} 个字符")
    if shelf_no is not None and len(shelf_no) > SHELF_NO_MAX_LEN:
        raise HTTPException(400, f"排架号最多 {SHELF_NO_MAX_LEN} 个字符")


def _detail(orm: RecordORM, db: Session | None = None) -> dict:
    rec = orm_to_record(orm)
    shelf_no = volume = None
    if db is not None:
        rows = (db.query(HoldingORM.volume, HoldingORM.shelf_no)
                .filter(HoldingORM.record_id == orm.id)
                .order_by(HoldingORM.id).all())
        volume = next((v for v, _s in rows if v), None)
        shelf_no = next((s for _v, s in rows if s), None)
    out = {
        "id": orm.id,
        "library_id": orm.library_id,
        "leader": orm.leader,
        "fields": orm.fields,
        "source": orm.source,
        "created_by": orm.created_by,
        "created_at": str(orm.created_at) if orm.created_at else None,
        "updated_at": str(orm.updated_at) if orm.updated_at else None,
        # 排架号/卷册号：详情页读第一册的非空值（Item 为真源）
        "shelf_no": shelf_no,
        "volume": volume,
        "volume_seq": orm.volume_seq,
        "item_type": orm.item_type,
        "reader_level": orm.reader_level,
        "search": {
            "isbn_norm": orm.isbn_norm, "title_norm": orm.title_norm,
            "title_pinyin_initials": orm.title_pinyin_initials,
            "author_norm": orm.author_norm, "year": orm.year, "class_no": orm.class_no,
        },
        "findings": [f.__dict__ for f in validate(rec)],
        "mrk": rec.to_mrk(),
    }
    if db is not None:
        out["barcodes"] = [b for (b,) in
                           db.query(HoldingORM.barcode)
                           .filter(HoldingORM.record_id == orm.id)
                           .order_by(HoldingORM.barcode).all() if b]
    return out


class RecordIn(BaseModel):
    leader: str | None = None
    fields: list[dict]
    source: str = "local"
    force: bool = False         # 兜底：质检 ERROR 时强制入库
    library_id: int | None = None   # 归属图书库（缺省=默认图书库）
    # 图书库以条码号为核心：新建记录必须同时给出条码（套录/编目时人工填写）。
    # 记录与馆藏一次事务写入，避免出现"有条目无条码"的中间态。
    barcodes: list[str] | None = None
    location: str | None = None     # 随记录新建的馆藏的馆藏地点
    book_no: str | None = None      # 手动书次号（索书号 "分类号/书次号" 的后半段）
    # 排架号与卷册号（种级，全部可选）。None = 未提供；"" = 显式清空。
    volume: str | None = None
    volume_seq: int | None = None
    shelf_no: str | None = None
    # 文献类型 / 读者对象（见模型注释）。
    # `reader_level` 会被写进 `100$a` 第 17-19 位（标准位置），列只是它的缓存。
    item_type: str | None = None
    reader_level: str | None = None


class RecordPatch(BaseModel):
    leader: str | None = None
    fields: list[dict] | None = None
    force: bool = False
    book_no: str | None = None      # 手动书次号：改已有记录时覆盖其全部馆藏的索书号
    # 与 RecordIn 同名同义，但 **None = 本次不修改**（区别于 "" = 清空）
    volume: str | None = None
    volume_seq: int | None = None
    shelf_no: str | None = None
    item_type: str | None = None
    reader_level: str | None = None
    # 乐观锁：详情里的 updated_at 原样带回。
    # 与库里不一致说明别人在编辑期间保存过 → 409，避免"后保存者的旧载荷覆盖
    # 前者改的题名/分类号"。**可选**：不传就不校验（脚本 / 兼容旧客户端）。
    expected_updated_at: str | None = None


# `100$a` 里「读者对象」的三位代码位置（0-based 半开区间，即第 17-19 位）
READER_LEVEL_SLICE = (17, 20)


def _apply_reader_level(rec, level: str | None) -> None:
    """把「读者对象」写进 `100$a` 第 17-19 位（CNMARC 的标准位置）。

    ⚠️ **`None` 与 `''` 都是「不改」** —— 这里**有意不做"空串=清空"**，
    与 `item_type`（`_norm('')` → NULL 即清空）**不一样**。原因是安全：

    `extra.readerLevel` 只有"打开已入库记录"时才是可靠的（从详情回灌）；
    **套录/资源库临时副本那条分支根本不灌它**（`load()` 顶部把它清成 `''`，
    `isTemp` 分支在 `populate` 之前就 `return`）。若把 `''` 当清空，
    那么「套录一条带 `em ` 的记录 → 直接保存」就会**静默抹掉**来源的读者对象。
    空串在这里的真实含义是"**未知**"，不是"用户删了它"。
    ⇒ 代价是**界面清不空这一格**（要清走「全字段 MARC」直接改 `100$a`），
    这是刻意的取舍：宁可清不掉，也不许静默丢数据。

    其余：`level` 归一成 `strip()` 后补/截成 **3 位**写入（`c` → `c  `），
    **长度不变**（36 位）；`100` 不存在时**不凭空造**（交给「自动规范」
    `ai/fields.py::fill_100`），这里只改已存在的那条。
    """
    if not (level or "").strip():
        return
    f = rec.get_first("100")
    if f is None:
        return
    a = f.get("a") or ""
    lo, hi = READER_LEVEL_SLICE
    if len(a) < hi:
        return
    f.set("a", a[:lo] + level.strip().ljust(3)[:3] + a[hi:])


def _check_gate(rec) -> dict:
    """图书库入库门槛：质检无 ERROR 才放行（用户约定：图书库只存校验好的数据）。"""
    findings = validate(rec)
    errors = [f.__dict__ for f in findings if f.level == LEVEL_ERROR]
    return {"findings": [f.__dict__ for f in findings], "errors": errors}


def _barcode_subquery(db: Session, barcode: str):
    """条码片段 -> 记录 id 子查询（条码是图书库的主检索入口之一）。"""
    return (db.query(HoldingORM.record_id)
            .filter(HoldingORM.barcode.ilike(f"%{like_escape(barcode.strip())}%", escape="\\")))


def _q_match_ids(q: str):
    """单一搜索框 -> 命中记录 id 的 **UNION**（题名/作者/ISBN/条码）。

    为什么不是 `or_(标题 ilike, 作者 ilike, ISBN like, id IN (条码子查询))`：
    OR 里的 `IN (子查询)` 是一个子计划，PG 无法把整个 OR 组成 BitmapOr，只能对
    records 顺表扫描 —— title 的 trigram GIN 等于白建（author/ISBN/条码原先更是
    连 trigram 都没有）。拆成 UNION 后每个分支各自走索引，命中集合与原 OR 完全
    一致（并集、去重），`total`/排序/翻页口径都不变。

    LIKE 转义与各分支的取词方式与拆分前逐字一致（条码腿沿用 `strip()`）。
    """
    title_term = f"%{like_escape(q)}%"
    isbn_term = f"%{like_escape(q.replace('-', ''))}%"
    barcode_term = f"%{like_escape(q.strip())}%"
    return union(
        select(RecordORM.id).where(
            RecordORM.title_norm.ilike(title_term, escape="\\")),
        select(RecordORM.id).where(
            RecordORM.author_norm.ilike(title_term, escape="\\")),
        select(RecordORM.id).where(
            RecordORM.isbn_norm.like(isbn_term, escape="\\")),
        select(HoldingORM.record_id).where(
            HoldingORM.barcode.ilike(barcode_term, escape="\\")),
    )


def _active_holding_subq():
    """有**在架**馆藏的子查询（用于"只显示还有书的记录"）。"""
    from ..models import STATUS_AVAILABLE
    return (select(HoldingORM.record_id)
            .where(HoldingORM.record_id == RecordORM.id,
                   HoldingORM.status == STATUS_AVAILABLE))


def _parse_day(v: str | None, field: str) -> date | None:
    """`YYYY-MM-DD` → date；非法直接 400（不静默忽略，否则筛选条件被悄悄丢掉）。"""
    if not v:
        return None
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        raise HTTPException(400, f"{field} 需为 YYYY-MM-DD 格式：{v}")


def _record_query(db: Session, *, library_id: int | None = None, q: str | None = None,
                  isbn: str | None = None, title: str | None = None,
                  author: str | None = None, barcode: str | None = None,
                  created_by: str | None = None, source: str | None = None,
                  class_no: str | None = None, no_barcode: bool = False,
                  active_only: bool = False, created_from: str | None = None,
                  created_to: str | None = None):
    """记录筛选的**唯一真源**：列表与「按筛选批量删除」都走它。

    为什么必须共用：如果「列表看到的 N 条」和「按筛选删掉的 M 条」用两套拼装逻辑，
    两边迟早会分叉（少一个 where 就会多删一批）—— 这是删数据，不能有第二次实现。
    """
    from datetime import datetime, time, timedelta
    query = db.query(RecordORM)
    if library_id:
        query = query.filter(RecordORM.library_id == library_id)
    if created_by:
        query = query.filter(RecordORM.created_by == created_by)
    if source:
        query = query.filter(RecordORM.source == source)
    if class_no:
        # 分类号按**前缀**匹配（I247 / K825 这种是实际筛选习惯，不是精确等号）
        query = query.filter(RecordORM.class_no.ilike(f"{like_escape(class_no.strip())}%",
                                                      escape="\\"))
    if active_only:
        query = query.filter(RecordORM.id.in_(_active_holding_subq()))
    if no_barcode:
        from sqlalchemy import exists
        query = query.filter(~exists().where(
            HoldingORM.record_id == RecordORM.id,
            HoldingORM.barcode.isnot(None), HoldingORM.barcode != ""))
    d_from = _parse_day(created_from, "created_from")
    if d_from:
        query = query.filter(RecordORM.created_at >= datetime.combine(d_from, time.min))
    d_to = _parse_day(created_to, "created_to")
    if d_to:
        # 含当天：用「次日 0 点」开区间，免得用户以为当天漏了
        query = query.filter(RecordORM.created_at <
                             datetime.combine(d_to + timedelta(days=1), time.min))
    if isbn:
        forms = isbnlib.all_forms(isbn)
        query = query.filter(RecordORM.isbn_norm.in_(forms) if forms else RecordORM.isbn_norm.is_(None))
    if title:
        query = query.filter(RecordORM.title_norm.ilike(f"%{like_escape(title)}%", escape="\\"))
    if author:
        query = query.filter(RecordORM.author_norm.ilike(f"%{like_escape(author)}%", escape="\\"))
    if barcode:
        query = query.filter(RecordORM.id.in_(_barcode_subquery(db, barcode)))
    if q:
        if q.isascii() and q.replace(" ", "").isalpha():
            query = query.filter(RecordORM.title_pinyin_initials.like(f"%{q.replace(' ', '').lower()}%"))
        else:
            # 单一搜索框：题名、作者、ISBN 与条码都能直接定位记录。
            # 并集拆成 UNION 而不是 or_：见 `_q_match_ids` 的说明（索引可用性）。
            query = query.filter(RecordORM.id.in_(_q_match_ids(q)))
    return query


@router.get("/records/facets")
@api(module="records")
def record_facets(library_id: int | None = None, db: Session = Depends(get_db)):
    """筛选项候选值（来源 / 编目员），供列表页的下拉用。

    单开一个端点而不是塞进列表响应：列表每次翻页都会拉一遍，而候选值基本不变。
    """
    q = db.query(RecordORM.source).filter(RecordORM.source.isnot(None))
    if library_id:
        q = q.filter(RecordORM.library_id == library_id)
    sources = [s for (s,) in q.distinct().order_by(RecordORM.source).all() if s]
    q2 = db.query(RecordORM.created_by).filter(RecordORM.created_by.isnot(None))
    if library_id:
        q2 = q2.filter(RecordORM.library_id == library_id)
    operators = [s for (s,) in q2.distinct().order_by(RecordORM.created_by).all() if s]
    return {"sources": sources, "operators": operators}


@router.get("/records", response_model=RecordPage)
@api(module="records")
def list_records(
    isbn: str | None = None, title: str | None = None, author: str | None = None,
    q: str | None = None, barcode: str | None = None, library_id: int | None = None,
    created_by: str | None = None, source: str | None = None, class_no: str | None = None,
    no_barcode: bool = False,
    created_from: str | None = None, created_to: str | None = None,
    active_only: bool = False,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
    sort: str | None = None, order: str | None = None,
    db: Session = Depends(get_db),
):
    """书目列表。

    `sort` / `order`：**点击表头排序**用（可排序列见 `RECORD_SORTS` 白名单，
    白名单外的值一律回退 `id desc`，不报错）。排序**挂在共用筛选口径之后** ——
    与 `POST /records/batch-delete {filter}` 看到的是同一批记录。

    `active_only=true` 只返回"至少还有一册在架馆藏"的记录——剔旧后整条记录都
    注销掉的书默认不该出现在编目工作台里。默认关闭，避免静默改变既有行为。

    筛选口径与 `POST /records/batch-delete {filter}` **共用同一份实现**
    （`_record_query`），否则「筛选看到的」与「按筛选删掉的」会不一致。
    """
    query = _record_query(db, library_id=library_id, q=q, isbn=isbn, title=title,
                          author=author, barcode=barcode, created_by=created_by,
                          source=source, class_no=class_no, no_barcode=no_barcode,
                          active_only=active_only, created_from=created_from,
                          created_to=created_to)
    total, rows = paginate(apply_sort(query, RECORD_SORTS, sort, order,
                                      default="id", default_order="desc",
                                      tiebreak=RecordORM.id),
                           page, page_size)

    ids = [r.id for r in rows]
    by_rec: dict[int, list[str]] = defaultdict(list)
    active_by_rec: dict[int, int] = {}
    callno_by_rec: dict[int, str] = {}
    volume_by_rec: dict[int, str] = {}
    shelf_by_rec: dict[int, str] = {}
    if ids:
        from ..models import STATUS_AVAILABLE
        for rid, bc, cn, st, vol, sh in (
                db.query(HoldingORM.record_id, HoldingORM.barcode,
                         HoldingORM.call_no, HoldingORM.status,
                         HoldingORM.volume, HoldingORM.shelf_no)
                .filter(HoldingORM.record_id.in_(ids))
                .order_by(HoldingORM.barcode).all()):
            if bc:
                by_rec[rid].append(bc)
            # 列表要显示完整索书号（分类号/书次号[:卷册号]）。索书号同种共用
            # （README 规则 5），任一册都能代表；优先取在架册的值。
            if cn and (rid not in callno_by_rec or st == STATUS_AVAILABLE):
                callno_by_rec[rid] = cn
            if vol and rid not in volume_by_rec:
                volume_by_rec[rid] = vol
            if sh and rid not in shelf_by_rec:
                shelf_by_rec[rid] = sh
        active_by_rec = dict(
            db.query(HoldingORM.record_id, func.count())
            .filter(HoldingORM.record_id.in_(ids), HoldingORM.status == STATUS_AVAILABLE)
            .group_by(HoldingORM.record_id).all())
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{"id": r.id, "title": r.title_norm, "isbn": r.isbn_norm,
                       "author": r.author_norm, "year": r.year, "class_no": r.class_no,
                       "source": r.source, "created_by": r.created_by,
                       "created_at": r.created_at.isoformat() if r.created_at else None,
                       "volume": volume_by_rec.get(r.id),
                       "shelf_no": shelf_by_rec.get(r.id),
                       "pages": _first_subfield(r.fields, "215", "a"),
                       "size": _first_subfield(r.fields, "215", "d"),
                       "item_type": r.item_type, "reader_level": r.reader_level,
                       "call_no": callno_by_rec.get(r.id),
                       "barcodes": by_rec.get(r.id, []),
                       # 在架册数 < 总册数 说明有注销/遗失；全为 0 说明整条已剔旧
                       "active_count": active_by_rec.get(r.id, 0),
                       "total_count": len(by_rec.get(r.id, []))}
                      for r in rows]}


@router.post("/records")
@api(module="records")
def create_record(payload: RecordIn, request: Request, db: Session = Depends(get_db)):
    rec = record_from_payload(payload.model_dump())
    if not rec.fields:
        raise HTTPException(400, "fields 不能为空")
    cl.clean_record(rec, migrate_pinyin=True)
    # 没有 801 且配置了本馆机构代码时补一条（GB/T 33286 7.10）。
    # 放在质检**之前**：补完再判，用户才不会看到"缺少 801"的提示。
    from ..services.settings import org_code
    from ..services.source_801 import ensure_source_801
    ensure_source_801(rec, org_code(db))
    gate = _check_gate(rec)
    if gate["errors"] and not payload.force:
        raise HTTPException(422, detail={
            "message": "质检未通过，请修正后重试，或改用强制保存",
            "errors": gate["errors"], "findings": gate["findings"]})

    barcodes = [b.strip() for b in (payload.barcodes or []) if b and b.strip()]
    if not barcodes:
        raise HTTPException(422, detail={
            "message": "图书库以条码为核心，保存时必须至少填写一个条码",
            "errors": [{"rule_id": "BARCODE_REQUIRED", "level": "ERROR", "tag": "905",
                        "message": "缺少条码：请在保存前填写条码"}],
            "findings": gate["findings"]})

    _check_extra_len(payload.volume, payload.shelf_no)
    _apply_reader_level(rec, payload.reader_level)   # 写进 100$a 位 17-19
    lib = resolve_library(db, payload.library_id)
    try:
        values = record_to_orm_values(rec, source=payload.source)
    except MarcWriteError as e:
        raise HTTPException(400, str(e))
    orm = RecordORM(**values, library_id=lib.id, created_by=operator_of(request))
    orm.item_type = _norm(payload.item_type)
    db.add(orm)
    db.flush()                      # 先拿到 id，馆藏要引用它
    # 排架号与卷册号归 Item：创建时作为本批册的默认值传入，不再落 record。
    volume = _norm(payload.volume)
    shelf_no = _norm(payload.shelf_no)
    orm.volume_seq = (payload.volume_seq
                      if payload.volume_seq is not None
                      else derive_volume_seq(volume))
    try:
        create_holdings(db, orm, len(barcodes), location=payload.location,
                        barcodes=barcodes, book_no=payload.book_no,
                        volume=volume, shelf_no=shelf_no)
    except ValueError as e:         # 条码重复 / 缺 690 等
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(orm)
    return _detail(orm, db)


@router.get("/records/{record_id}")
@api(module="records")
def get_record(record_id: int, db: Session = Depends(get_db)):
    orm = db.get(RecordORM, record_id)
    if orm is None:
        raise HTTPException(404, "记录不存在")
    return _detail(orm, db)


@router.put("/records/{record_id}")
@api(module="records")
def update_record(record_id: int, payload: RecordPatch, db: Session = Depends(get_db)):
    orm = db.get(RecordORM, record_id, with_for_update=True, populate_existing=True)
    if orm is None:
        raise HTTPException(404, "记录不存在")
    # 乐观锁：调用方带回了打开编辑器时的 updated_at，而库里已经变了 → 说明期间
    # 有人保存过。行锁只保证"同时写"串行，拦不住"先读后写"的旧载荷覆盖
    # 。比对放在最前，别等改了一半再报错。
    if payload.expected_updated_at:
        current = _iso_utc(orm.updated_at)
        if current and current != payload.expected_updated_at.strip():
            raise HTTPException(409, detail={
                "message": "这条书目在你编辑期间已被修改，保存已取消",
                "hint": "请刷新后重新编辑。为避免覆盖别人的修改，本次没有写入任何内容。",
                "current_updated_at": current,
            })
    # 改分类号同步用的旧值必须在这里取 —— 下面的 setattr 会把 orm.class_no 覆盖成新值
    old_class = (orm.class_no or "").strip()
    _check_extra_len(payload.volume, payload.shelf_no)   # 先卡长度，别动过数据再报错
    persisted_rec = orm_to_record(orm)
    rec = orm_to_record(orm)
    if payload.fields is not None:
        rec = record_from_payload({"leader": payload.leader or orm.leader,
                                   "fields": payload.fields})
    elif payload.leader:
        rec.leader = payload.leader
    cl.clean_record(rec, migrate_pinyin=True)
    _apply_reader_level(rec, payload.reader_level)   # 写进 100$a 位 17-19
    from ..services.settings import org_code
    from ..services.source_801 import ensure_source_801
    ensure_source_801(rec, org_code(db))
    gate = _check_gate(rec)
    if gate["errors"] and not payload.force:
        raise HTTPException(422, detail={
            "message": "质检未通过，请修正后重试，或改用强制保存",
            "errors": gate["errors"], "findings": gate["findings"]})
    try:
        values = record_to_orm_values(rec, source=orm.source)
    except MarcWriteError as e:
        raise HTTPException(400, str(e))
    for k, v in values.items():
        setattr(orm, k, v)
    db.flush()

    # 排架号与卷册号归 Item；记录级编辑视为“批量改全部册”。
    new_volume = None
    if payload.volume is not None:
        new_volume = _norm(payload.volume)
        orm.volume_seq = (payload.volume_seq
                          if payload.volume_seq is not None
                          else derive_volume_seq(new_volume))
    elif payload.volume_seq is not None:
        orm.volume_seq = payload.volume_seq
    if payload.shelf_no is not None:
        new_shelf = _norm(payload.shelf_no)
        for h in (db.query(HoldingORM)
                  .filter(HoldingORM.record_id == orm.id).all()):
            h.shelf_no = new_shelf
    if payload.item_type is not None:
        orm.item_type = _norm(payload.item_type)
    db.flush()
    # 规则 14：autoflush=False —— 不 flush，后面的查询与 905 重建会读到旧值
    volume_changed = payload.volume is not None
    book_no_given = payload.book_no is not None
    new_class = (rec.first_value("690", "a") or "").strip()
    class_changed = (not book_no_given and bool(new_class)
                     and new_class != old_class)
    if book_no_given:
        # 手动书次号：按更新后的 690 重算索书号，并同步该记录全部馆藏与 905
        if not new_class:
            raise HTTPException(400, "记录缺少分类号，无法生成索书号")
        if not db.query(HoldingORM).filter(HoldingORM.record_id == orm.id).first():
            raise HTTPException(400, "该记录尚无条码，无法设置书次号")
        try:
            set_book_no(db, orm, new_class, payload.book_no)
        except ValueError as e:
            raise HTTPException(400, str(e))
    elif class_changed:
        # 只改了分类号（没给书次号）：馆藏索书号与 905 必须跟着走，否则书标 / 导出
        # 一直用旧号（静默漂移，实测过）。书次号沿用旧索书号里的那段 —— 附加区分号
        # （I210.4/L824/2 的 "L824/2"）原样保留；纯数字时 set_book_no 会把计数器
        # 抬到新分类号下对应的位置，后续自动编号不撞号。
        h = (db.query(HoldingORM)
             .filter(HoldingORM.record_id == orm.id, HoldingORM.call_no.isnot(None))
             .first())
        if h is not None and "/" in (h.call_no or ""):
            base = call_no_base(h.call_no, h.volume)
            old_book_no = base.split("/", 1)[1].strip()
            if old_book_no:
                try:
                    set_book_no(db, orm, new_class, old_book_no)
                except ValueError as e:
                    raise HTTPException(400, str(e))
    if volume_changed:
        apply_volume_to_holdings(db, orm, new_volume)
    # 905 的受管理值只信持久馆藏，不信载荷（题名更新、删除或伪造 905 也一样）。
    # 旧记录里的登记扩展不能因载荷省略 905 而丢失；重建后不再把局部 rec 写回 orm。
    # 载荷**显式**给了排架号/卷册号（含空串 = 清空）时不从旧 905 回填 —— 否则用户
    # 清空后会被旧值复活，永远清不掉（见 services.record_projection._rebuild_905）。
    db.flush()
    record_projection.rebuild_905(db, orm.id, extra_source=persisted_rec)
    db.commit()
    db.refresh(orm)
    return _detail(orm, db)


@router.delete("/records/{record_id}")
@api(module="records")
def delete_record(record_id: int, db: Session = Depends(get_db)):
    lock_record(db, record_id)
    orm = db.get(RecordORM, record_id)
    if orm is None:
        raise HTTPException(404, "记录不存在")
    if active_loan_record_ids(db, {record_id}):
        raise HTTPException(400, "该记录下有图书正在外借中，不能删除（请先归还）")
    reservation_service.cancel_active_for_record(db, record_id, "书目记录已删除")
    db.delete(orm)
    db.commit()
    return {"deleted": record_id}


class RecordFilterIn(BaseModel):
    """记录筛选条件（与列表页的下拉/输入一一对应）。"""
    library_id: int | None = None
    q: str | None = None
    isbn: str | None = None
    title: str | None = None
    author: str | None = None
    barcode: str | None = None
    created_by: str | None = None
    source: str | None = None
    class_no: str | None = None
    no_barcode: bool = False
    active_only: bool = False
    created_from: str | None = None
    created_to: str | None = None


class RecordDeleteIn(BaseModel):
    """批量删除载荷。两种用法二选一：

    - `ids`：勾选删除（界面上一页页勾）；
    - `filter` + `expected_total`：**按筛选条件整批删**（清掉一次错误导入的整批数据）。
    """
    ids: list[int] = []
    filter: RecordFilterIn | None = None
    # 前端把"页面上显示的条数"回传过来；与后端实际匹配数不一致 → 拒绝执行
    expected_total: int | None = None


# 单次按筛选删除的上限：`expected_total` 已经防了"筛选传错"，这里再兜一道，
# 免得一次误操作把整库抹掉（真要删更多就分几次，或先导出留底）。
MAX_BULK_DELETE = 20000


@router.post("/records/batch-delete")
@api(module="records")
def batch_delete_records(payload: RecordDeleteIn, db: Session = Depends(get_db)):
    """批量删除记录。记录下的馆藏（条码）由数据库外键级联一并删除。

    注意：图书库以条码为核心，删记录必然连带删条码，前端需二次确认。

    `filter` 分支**必须**回传 `expected_total`（前端筛选结果条数），不一致就 409 ——
    防的是"筛选条件拼错/期间数据变了"导致删掉远超预期的一批。宁可让用户刷新重来。
    """
    if payload.filter is not None:
        if payload.expected_total is None:
            raise HTTPException(400, "按筛选删除必须回传 expected_total（页面上显示的条数）")
        query = _record_query(db, **payload.filter.model_dump())
        matched = query.count()
        if matched != payload.expected_total:
            raise HTTPException(409, (
                f"筛选结果与预期不一致（页面显示 {payload.expected_total} 条，"
                f"当前实际 {matched} 条）。数据已发生变化，请刷新后重试。"))
        if matched == 0:
            return {"deleted": 0, "missing": [], "total_matched": 0}
        if matched > MAX_BULK_DELETE:
            raise HTTPException(400, f"单次最多删除 {MAX_BULK_DELETE} 条，请缩小筛选范围后分批删除")
        rows = query.all()
        for rid in sorted(orm.id for orm in rows):
            lock_record(db, rid)
        on_loan = active_loan_record_ids(db, {orm.id for orm in rows})
        if on_loan:
            sample = "、".join(str(i) for i in sorted(on_loan)[:10])
            raise HTTPException(400, f"以下 {len(on_loan)} 条记录有图书外借，"
                                     f"不能删除（请先归还）：{sample}")
        for orm in rows:
            reservation_service.cancel_active_for_record(
                db, orm.id, "书目记录已删除")
            db.delete(orm)
        db.commit()
        return {"deleted": len(rows), "missing": [], "total_matched": matched}

    ids = payload.ids
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    rows = db.query(RecordORM).filter(RecordORM.id.in_(ids)).all()
    for rid in sorted(orm.id for orm in rows):
        lock_record(db, rid)
    on_loan = active_loan_record_ids(db, {orm.id for orm in rows})
    if on_loan:
        sample = "、".join(str(i) for i in sorted(on_loan)[:10])
        raise HTTPException(400, f"以下 {len(on_loan)} 条记录有图书外借，"
                                 f"不能删除（请先归还）：{sample}")
    for orm in rows:
        reservation_service.cancel_active_for_record(
            db, orm.id, "书目记录已删除")
        db.delete(orm)              # 逐条删，保证 ORM 层与级联行为一致
    db.commit()
    missing = sorted(set(ids) - {r.id for r in rows})
    return {"deleted": len(rows), "missing": missing, "total_matched": len(ids)}


@router.post("/records/{record_id}/validate")
@api(module="records", level="read")
def validate_record(record_id: int, db: Session = Depends(get_db)):
    orm = db.get(RecordORM, record_id)
    if orm is None:
        raise HTTPException(404, "记录不存在")
    return {"id": record_id, "findings": [f.__dict__ for f in validate(orm_to_record(orm))]}


@router.get("/dedup")
@api(module="records")
def dedup(isbn: str | None = None, title: str | None = None,
          library_id: int | None = None, db: Session = Depends(get_db)):
    """编目查重：ISBN 全形态精确 + 题名模糊（可按图书库过滤——同书异校不算重）。"""
    hits = []
    query = db.query(RecordORM)
    if library_id:
        query = query.filter(RecordORM.library_id == library_id)
    if isbn:
        forms = isbnlib.all_forms(isbn)
        if forms:
            for orm in query.filter(RecordORM.isbn_norm.in_(forms)).all():
                hits.append({"id": orm.id, "title": orm.title_norm, "isbn": orm.isbn_norm,
                             "library_id": orm.library_id})
    elif title:
        for orm in query.filter(RecordORM.title_norm.ilike(f"%{like_escape(title)}%", escape="\\")).limit(20):
            hits.append({"id": orm.id, "title": orm.title_norm, "isbn": orm.isbn_norm,
                         "library_id": orm.library_id})
    return {"duplicates": hits, "count": len(hits)}
