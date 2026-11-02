# -*- coding: utf-8 -*-
"""统计报表：总览与分维度统计（供总括登记账 / 财产登记表使用）。

口径约定（写进报表页脚，避免与上级报表口径打架）：
  - 种数（title_count）：书目记录条数
  - 册数（copy_count）：**在架**馆藏条数（已注销 / 已遗失的册不计入）
  - 有馆藏种数（titles_with_holding）：至少有一册**在架**馆藏的书目数
  - 注销册数（withdrawn_copy_count）：已注销 / 已遗失的馆藏条数
  - 码洋：取 `records.price`（`010$d` 的解析结果，写入时落冗余列，解析规则见
      `core/pricing.py`），且**只累加本馆默认币种**（`price_currency` 相符）的部分。
      amount      = 记录定价之和（种口径，按记录去重）
      copy_amount = Σ(定价 × 该记录**在架**馆藏册数)（册口径，即实际藏书码洋）
    非默认币种与无法解析的定价按 0 计，不影响册数统计
    （不同币种不能相加，1 美元 + 1 元人民币 ≠ 2 元）。

> 注销册不进 905、不进册数与册码洋——这是"账实相符"的基本要求；
> 注销情况由 `dim=withdrawn` 单列，不混进馆藏统计。

**聚合方式（改造）**：全部走 SQL 聚合，不再把全库 `fields`（JSONB）拉进内存
逐条正则。改造前每打开一次统计页都要搬几十 MB JSONB 再全量重算，十万级根本撑不住；
现在记录侧只碰 `price` / `price_currency` 两个小列，Python 侧只做"分组标签、排序、
取前 N、算合计"这类轻活。金额一律在 SQL 里 `SUM(CASE WHEN price_currency = 默认币种 ...)`，
因此**改默认币种是换一个过滤条件**，不需要回填、不会让历史记录算错。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, case, cast, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (INACTIVE_STATUSES, STATUS_AVAILABLE, BookLibrary, CLCClass,
                      HoldingORM, RecordORM)
from ..services import dictionaries as dicts
from ..core.route_meta import api
from .common import xlsx_response

router = APIRouter(prefix="/stats", tags=["stats"])

UNCLASSIFIED = "(未分类)"
DIMENSIONS = ("classify", "location", "year", "source", "withdrawn")


# ---------------------------------------------------------------------------
#  聚合表达式
# ---------------------------------------------------------------------------

def _amt(price, currency, cur: str, mult=None):
    """`SUM(CASE WHEN currency = 默认币种 THEN price [× mult] ELSE 0 END)`。

    非默认币种按 0 计（口径见模块说明），`price` 为 NULL（无 010$d）时 CASE 落到
    ELSE 0，所以不需要额外 COALESCE 金额列 —— 但外层仍套一层 COALESCE，防止
    "整个分组一行都没有"（不该发生）时返回 NULL 让调用方拿到 None。
    """
    value = price if mult is None else price * mult
    return func.coalesce(func.sum(case((currency == cur, value), else_=0)), 0)


def _active_copies_subq(library_id: int | None):
    """每记录的**在架**册数子查询（记录侧聚合统一 LEFT JOIN 它）。

    LEFT JOIN 而不是 JOIN：图书库要求"没有条码的记录不允许存在"，但已注销到一册不剩的
    记录仍然在库里（`active_only` 就是为它准备的），这种记录要出在种数里、册数为 0。
    """
    q = (select(HoldingORM.record_id.label("rid"), func.count().label("n"))
         .where(HoldingORM.status == STATUS_AVAILABLE)
         .group_by(HoldingORM.record_id))
    if library_id:
        q = q.where(HoldingORM.library_id == library_id)
    return q.subquery("h")


def _round2(value) -> float:
    """金额收口：`Decimal`/`None` -> 两位小数的 float（避免 958.6999999999999 这类浮点尾巴）。"""
    return round(float(value or 0), 2)


def _level1(db: Session) -> dict[str, str]:
    return {c.class_no: c.title
            for c in db.query(CLCClass).filter(CLCClass.level == 1).all()}


def _class_key(letter: str | None, level1: dict[str, str]) -> tuple[str, str]:
    """分类首字母 -> (分组键, 一级类名)。非字母（空/数字）一律归「(未分类)」。

    传进来的 `letter` 已经是 SQL 侧 `upper(left(btrim(class_no),1))` 的结果；
    保留 Python 这一层判定是为了不改变既有行为（`str.isalpha()` 认 Unicode 字母，
    照搬到 SQL 里做不到逐字一致），代价只是几个分组在 Python 里合并一次。
    """
    letter = (letter or "").strip()[:1].upper()
    if not letter.isalpha():
        return UNCLASSIFIED, UNCLASSIFIED
    return letter, level1.get(letter, letter)


def _blank() -> dict:
    return {"title_count": 0, "copy_count": 0, "titles_with_holding": 0,
            "amount": 0.0, "copy_amount": 0.0, "withdrawn_copy_count": 0}


# ---------------------------------------------------------------------------
#  总览
# ---------------------------------------------------------------------------

def _overview(db: Session, library_id: int | None, cur: str,
              names: dict[int, str]) -> dict:
    """各图书库的种数 / 册数 / 码洋 + 注销册数，附全馆合计。

    两条聚合：① 记录侧（种数 / 有馆藏种数 / 码洋）；② 馆藏侧（册数 / 注销册数）。
    册数**以馆藏表为准**（不是从记录侧算），与改造前逐字一致。
    """
    h = _active_copies_subq(library_id)
    rq = (select(RecordORM.library_id.label("lib"),
                 func.count().label("title_count"),
                 func.count().filter(h.c.n > 0).label("titles_with_holding"),
                 _amt(RecordORM.price, RecordORM.price_currency, cur).label("amount"),
                 _amt(RecordORM.price, RecordORM.price_currency, cur,
                      func.coalesce(h.c.n, 0)).label("copy_amount"))
          .select_from(RecordORM)
          .outerjoin(h, h.c.rid == RecordORM.id))
    if library_id:
        rq = rq.where(RecordORM.library_id == library_id)
    rq = rq.group_by(RecordORM.library_id)

    agg: dict[int, dict] = {}
    for lib, titles, with_holding, amount, copy_amount in db.execute(rq):
        agg[lib] = {"title_count": titles,
                    "titles_with_holding": with_holding,
                    "amount": _round2(amount),
                    "copy_amount": _round2(copy_amount),
                    "copy_count": 0, "withdrawn_copy_count": 0}

    hq = (select(HoldingORM.library_id.label("lib"),
                 func.count().filter(HoldingORM.status == STATUS_AVAILABLE).label("copy_count"),
                 func.count().filter(HoldingORM.status.in_(INACTIVE_STATUSES))
                 .label("withdrawn_copy_count"))
          .group_by(HoldingORM.library_id))
    if library_id:
        hq = hq.where(HoldingORM.library_id == library_id)
    for lib, copies, withdrawn in db.execute(hq):
        a = agg.setdefault(lib, _blank())
        a["copy_count"] = copies
        a["withdrawn_copy_count"] = withdrawn

    libs = []
    for lib_id in sorted(agg):
        a = dict(agg[lib_id])
        libs.append({"id": lib_id, "name": names.get(lib_id, f"库{lib_id}"), **a})
    total = {"title_count": sum(x["title_count"] for x in libs),
             "copy_count": sum(x["copy_count"] for x in libs),
             "titles_with_holding": sum(x["titles_with_holding"] for x in libs),
             "amount": round(sum(x["amount"] for x in libs), 2),
             "copy_amount": round(sum(x["copy_amount"] for x in libs), 2),
             "withdrawn_copy_count": sum(x["withdrawn_copy_count"] for x in libs)}
    return {"currency": cur, "libraries": libs, "total": total}


@router.get("/overview")
@api(module="stats")
def stats_overview(library_id: int | None = None, db: Session = Depends(get_db)):
    """总览：各图书库的种数/册数/码洋（在架口径）+ 注销册数，附全馆合计。"""
    return _overview(db, library_id, dicts.default_currency(db),
                     {l.id: l.name for l in db.query(BookLibrary).order_by(BookLibrary.id).all()})


# ---------------------------------------------------------------------------
#  分维度
# ---------------------------------------------------------------------------

def _record_dim_expr(dim: str):
    """classify / year / source 三个"以记录为行"的维度的分组表达式。

    分类维度**只取首字母**在 SQL 里分组，标签与"非字母归未分类"仍在 Python 侧判定
    （见 `_class_key`）—— 两种写法对空值/非字母的处理不同，别在这里重复实现。
    """
    if dim == "classify":
        return func.upper(func.left(func.btrim(func.coalesce(RecordORM.class_no, "")), 1))
    if dim == "year":
        return func.coalesce(cast(RecordORM.year, String), "(未知)")
    return func.coalesce(
        func.nullif(func.btrim(func.coalesce(RecordORM.source, "")), ""), "(未标注)")


def _record_dim_rows(db: Session, dim: str, library_id: int | None,
                     cur: str) -> list[tuple]:
    """以记录为行的维度聚合：`[(原始分组值, 种数, 册数, 种码洋, 册码洋)]`。"""
    h = _active_copies_subq(library_id)
    key = _record_dim_expr(dim).label("k")
    q = (select(key,
                func.count().label("titles"),
                func.coalesce(func.sum(h.c.n), 0).label("copies"),
                _amt(RecordORM.price, RecordORM.price_currency, cur).label("amount"),
                _amt(RecordORM.price, RecordORM.price_currency, cur,
                     func.coalesce(h.c.n, 0)).label("copy_amount"))
         .select_from(RecordORM)
         .outerjoin(h, h.c.rid == RecordORM.id)
         .group_by(key))
    if library_id:
        q = q.where(RecordORM.library_id == library_id)
    return list(db.execute(q))


def _holding_dim_rows(db: Session, dim: str, library_id: int | None,
                      cur: str) -> list[tuple]:
    """以**馆藏为行**的维度聚合：location（在架）与 withdrawn（非在架）。

    为什么分两层 GROUP BY：这两个维度的"种码洋"要按**记录去重**（同一条记录在该组里
    可能有多册），而册数/册码洋要**按册累加**。先按 (组, 记录) 收一层，
    再按组收第二层，两个口径就都能一句 SQL 算准。
    """
    if dim == "location":
        key = func.coalesce(func.nullif(func.btrim(func.coalesce(HoldingORM.location, "")), ""),
                            "(未指定)")
        where = [HoldingORM.status == STATUS_AVAILABLE]
    else:   # withdrawn：按记录的中图法大类归并
        key = func.upper(func.left(func.btrim(func.coalesce(RecordORM.class_no, "")), 1))
        where = [HoldingORM.status.in_(INACTIVE_STATUSES)]

    # LEFT JOIN 记录：理论上不该有孤儿馆藏（holdings.record_id 有外键），
    # 但改造前的实现遇到孤儿时"照计册数、不计金额"，这里逐字保留该行为。
    # 按库过滤记录放在 ON 里（不是 WHERE）：放 WHERE 会把孤儿馆藏整行滤掉，册数就变了。
    join_on = HoldingORM.record_id == RecordORM.id
    if library_id:
        join_on = and_(join_on, RecordORM.library_id == library_id)

    inner = (select(key.label("k"),
                    HoldingORM.record_id.label("rid"),
                    func.count().label("copies"),
                    RecordORM.price.label("price"),
                    RecordORM.price_currency.label("crcy"))
             .select_from(HoldingORM)
             .outerjoin(RecordORM, join_on)
             .where(*where))
    if library_id:
        inner = inner.where(HoldingORM.library_id == library_id)
    inner = inner.group_by(key, HoldingORM.record_id,
                           RecordORM.price, RecordORM.price_currency).subquery("g")

    q = (select(inner.c.k,
                func.count().label("titles"),
                func.coalesce(func.sum(inner.c.copies), 0).label("copies"),
                _amt(inner.c.price, inner.c.crcy, cur).label("amount"),
                _amt(inner.c.price, inner.c.crcy, cur, inner.c.copies).label("copy_amount"))
         .group_by(inner.c.k))
    return list(db.execute(q))


def _breakdown(db: Session, library_id: int | None, cur: str, level1: dict[str, str],
               dim: str, limit: int) -> dict:
    rows = (_holding_dim_rows(db, dim, library_id, cur) if dim in ("location", "withdrawn")
            else _record_dim_rows(db, dim, library_id, cur))

    groups: dict[str, dict] = {}

    def bucket(key: str, label: str) -> dict:
        return groups.setdefault(key, {"key": key, "label": label,
                                       "title_count": 0, "copy_count": 0,
                                       "amount": 0.0, "copy_amount": 0.0})

    for raw, titles, copies, amount, copy_amount in rows:
        if dim == "classify":
            key, label = _class_key(raw, level1)
        elif dim == "withdrawn":
            key, label = _class_key(raw, level1)          # 同样按中图法大类归并
        else:                                             # location / year / source
            key = label = raw
        b = bucket(key, label)
        b["title_count"] += titles
        b["copy_count"] += copies
        b["amount"] += float(amount or 0)
        b["copy_amount"] += float(copy_amount or 0)

    ordered = sorted(groups.values(),
                     key=lambda x: (-x["title_count"], -x["copy_count"], x["key"]))
    for r in ordered:
        r["amount"] = _round2(r["amount"])
        r["copy_amount"] = _round2(r["copy_amount"])
    # 合计始终基于**全量分组**：若按截断后的 rows 求和，前端显示的分项合计
    # 会与总览对不上（实测 classify 曾出现 23 ≠ 26）。
    return {
        "dim": dim,
        "currency": cur,
        "rows": ordered[:limit],
        "group_count": len(ordered),
        "truncated": len(ordered) > limit,
        "total": {
            "title_count": sum(r["title_count"] for r in ordered),
            "copy_count": sum(r["copy_count"] for r in ordered),
            "amount": round(sum(r["amount"] for r in ordered), 2),
            "copy_amount": round(sum(r["copy_amount"] for r in ordered), 2),
        },
    }


@router.get("/breakdown")
@api(module="stats")
def stats_breakdown(
    dim: str = Query("classify", description="classify | location | year | source | withdrawn"),
    library_id: int | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """分维度统计。返回按种数降序的分组明细。

    `dim=withdrawn` 是**注销统计**（对标旧版系统「图书注销分类统计」）：
    按中图法大类给出注销/遗失册数，口径与其他维度相反（只算非在架册）。
    """
    if dim not in DIMENSIONS:
        raise HTTPException(400, f"不支持的统计维度：{dim}（可选 {', '.join(DIMENSIONS)}）")
    return _breakdown(db, library_id, dicts.default_currency(db), _level1(db), dim, limit)


DIM_TITLES = {"classify": "分类统计", "location": "馆藏地点统计",
              "year": "出版年统计", "source": "来源统计",
              "withdrawn": "注销统计"}


# ---------------------------------------------------------------------------
#  编目工作量（对标旧版系统「图书编目分类统计」——按编目员 × 时间段）
# ---------------------------------------------------------------------------

def _workload_rows(db: Session, from_: str | None, to: str | None,
                   library_id: int | None) -> tuple[list[dict], dict, str]:
    """按编目员聚合种数/册数/码洋。册数只算**在架**册。

    返回 `(rows, total, currency)` —— 币种一并带出来，导出表头要标它。
    编目员只有几个人到几十个人，分组数很少：SQL 聚合后就地排序即可。
    """
    from ..core.operator import UNSIGNED

    cur = dicts.default_currency(db)
    h = _active_copies_subq(None)      # 册数按记录取在架册，**不再按图书库过滤**（同旧实现）
    op = func.coalesce(func.nullif(func.btrim(func.coalesce(RecordORM.created_by, "")), ""),
                       UNSIGNED).label("op")

    q = (select(op,
                func.count().label("titles"),
                func.coalesce(func.sum(h.c.n), 0).label("copies"),
                _amt(RecordORM.price, RecordORM.price_currency, cur).label("amount"),
                _amt(RecordORM.price, RecordORM.price_currency, cur,
                     func.coalesce(h.c.n, 0)).label("copy_amount"))
         .select_from(RecordORM)
         .outerjoin(h, h.c.rid == RecordORM.id)
         .group_by(op))
    if library_id:
        q = q.where(RecordORM.library_id == library_id)
    if from_:
        q = q.where(RecordORM.created_at >= f"{from_} 00:00:00+08")
    if to:
        q = q.where(RecordORM.created_at <= f"{to} 23:59:59+08")

    rows = [{"operator": operator, "title_count": titles, "copy_count": copies,
             "amount": _round2(amount), "copy_amount": _round2(copy_amount)}
            for operator, titles, copies, amount, copy_amount in db.execute(q)]
    rows.sort(key=lambda x: (-x["title_count"], x["operator"]))
    total = {
        "title_count": sum(r["title_count"] for r in rows),
        "copy_count": sum(r["copy_count"] for r in rows),
        "amount": round(sum(r["amount"] for r in rows), 2),
        "copy_amount": round(sum(r["copy_amount"] for r in rows), 2),
        "operator_count": len(rows),
    }
    return rows, total, cur


@router.get("/workload")
@api(module="stats")
def stats_workload(
    # 原生 date 类型：非法日期由 FastAPI 422，不再 DataError 500
    from_: date | None = Query(None, alias="from", description="起始日期 YYYY-MM-DD"),
    to: date | None = Query(None, description="结束日期 YYYY-MM-DD（含当天）"),
    library_id: int | None = None,
    db: Session = Depends(get_db),
):
    """编目工作量：按编目员给出种数 / 册数 / 码洋（对标旧版系统的编目分类统计）。

    编目员标识取自请求头 `X-Operator`（见 core/operator.py）；没署名归入「(未署名)」。
    时间区间按记录**创建时间**，两端都是闭区间（含 `to` 当天）。
    """
    rows, total, cur = _workload_rows(db, from_, to, library_id)
    return {"from": from_, "to": to, "library_id": library_id,
            "currency": cur, "rows": rows, "total": total}


@router.get("/workload/export")
@api(module="stats")
def export_workload(
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    library_id: int | None = None,
    db: Session = Depends(get_db),
):
    """导出编目工作量报表（供工作量考评/上报）。"""
    import openpyxl

    rows, total, cur = _workload_rows(db, from_, to, library_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "编目工作量"
    span = f"{from_ or '起始'} ~ {to or '至今'}"
    ws.append([f"编目工作量统计（{span}）"])
    ws.append(["编目员", "种数", "册数(在架)", f"种码洋({cur})", f"册码洋({cur})"])
    for r in rows:
        ws.append([r["operator"], r["title_count"], r["copy_count"],
                   r["amount"], r["copy_amount"]])
    ws.append(["合计", total["title_count"], total["copy_count"],
               total["amount"], total["copy_amount"]])

    return xlsx_response(wb, f"编目工作量报表-{span.replace(' ', '')}.xlsx",
                         count=len(rows))


@router.get("/export")
@api(module="stats")
def export_stats(
    dim: str = Query("classify"),
    library_id: int | None = None,
    db: Session = Depends(get_db),
):
    """导出统计报表为 Excel（供上报/存档）。

    改造前这里要"全量只 `_load` 一次"给 overview 与 breakdown 共用，免得全库 JSONB
    被装载两次；现在两边各是一句 SQL 聚合，不再需要那套共享装载（见）。
    """
    import openpyxl

    if dim not in DIMENSIONS:
        raise HTTPException(400, f"不支持的统计维度：{dim}（可选 {', '.join(DIMENSIONS)}）")
    cur = dicts.default_currency(db)
    names = {l.id: l.name for l in db.query(BookLibrary).order_by(BookLibrary.id).all()}
    overview = _overview(db, library_id, cur, names)
    data = _breakdown(db, library_id, cur, _level1(db), dim, limit=1000)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = DIM_TITLES.get(dim, "统计")[:31]
    cur = data["currency"]
    ws.append([DIM_TITLES.get(dim, dim), "种数", "册数", f"种码洋({cur})", f"册码洋({cur})"])
    for r in data["rows"]:
        ws.append([r["label"], r["title_count"], r["copy_count"],
                   r["amount"], r["copy_amount"]])
    t = data["total"]
    ws.append(["合计", t["title_count"], t["copy_count"], t["amount"], t["copy_amount"]])

    ws2 = wb.create_sheet("总览")
    ws2.append(["图书库", "种数", "册数(在架)", "有馆藏种数",
                f"种码洋({cur})", f"册码洋({cur})", "注销册数"])
    for lib in overview["libraries"]:
        ws2.append([lib["name"], lib["title_count"], lib["copy_count"],
                    lib["titles_with_holding"], lib["amount"], lib["copy_amount"],
                    lib["withdrawn_copy_count"]])
    o = overview["total"]
    ws2.append(["合计", o["title_count"], o["copy_count"],
                o["titles_with_holding"], o["amount"], o["copy_amount"],
                o["withdrawn_copy_count"]])

    return xlsx_response(wb, f"{DIM_TITLES.get(dim, '统计')}报表.xlsx",
                         count=len(data["rows"]))
