# -*- coding: utf-8 -*-
"""全库数据体检。

对标旧版系统「系统维护」下的三个体检工具：
    查找无条码的图书 / 查找分类号不规范的图书 / 条码断号（对标旧版系统的「查找空条形码」）

**为什么需要它**：`core/validators.py` 的 15 条质检规则只在**编目录入时**作用于
**单条记录**——它拦得住新录入，拦不住两样东西：
  1. 历史遗留数据（旧版系统迁移、书商 ISO 批量导入）；
  2. 套录带进来的数据（外部源质量参差，且套录时未必逐条过质检）。
于是"库里已经躺着脏数据"这件事没有任何出口。本模块提供**全库扫描**视图，
每条结果都带 `record_id`，供前端直接跳转编目页定位修改。

口径：
  - no-barcode     无条码：不存在任何"条码非空"的馆藏
  - no-class       缺分类号：class_no 为空
  - bad-class      分类号结构异常：非空但不以字母开头
  - barcode-gaps   条码断号：按 (图书库, 前缀, 位数) 分组后数字段的空缺
  - same-isbn-diff-class  同书异号：同一个 ISBN（归一后）下出现了**多个不同的分类号**
                   （对标参考系统的「自动异号同名提醒」。同书应当同索书号，分类号不同
                    就是有一处编错了 —— 分类号是索书号的主体，所以只看它）
  - book-no-gaps   书次号断号：按 (图书库, 分类号) 分组后书次号序列的空缺
  - same-callno-diff-book  异书同号：同一个**完整索书号**被两条以上记录占用，
                   且这些记录既非同 ISBN 也非同题名（与「同书异号」正好成对）
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from openpyxl import Workbook
from sqlalchemy import BigInteger, and_, cast, func, select, text
from sqlalchemy.orm import Session, aliased

from ..db import get_db
from ..models import HoldingORM, RecordORM
from ..services.paging import paginate
from ..core.route_meta import api
from ..schemas import RecordBriefPage
from .common import xlsx_response

router = APIRouter(prefix="/inspect", tags=["inspect"])

# 条码断号最多返回的段数（避免"只有 1 和 999999"这类样本刷屏）
MAX_GAP_SEGMENTS = 200

def _base(db: Session, library_id: int | None = None):
    """体检的基础记录查询（限定图书库）。"""
    q = db.query(RecordORM)
    if library_id:
        q = q.filter(RecordORM.library_id == library_id)
    return q


def _nonempty_class():
    """class_no 去空白后非空。"""
    return func.trim(func.coalesce(RecordORM.class_no, "")) != ""


def _no_active_barcode_subq(db: Session):
    """存在至少一条"条码非空"馆藏的子查询（用于取反）。"""
    return (db.query(HoldingORM.record_id)
            .filter(HoldingORM.record_id == RecordORM.id,
                    HoldingORM.barcode.isnot(None),
                    func.trim(HoldingORM.barcode) != ""))


def _brief(r: RecordORM) -> dict:
    return {"id": r.id, "library_id": r.library_id, "title": r.title_norm,
            "isbn": r.isbn_norm, "author": r.author_norm, "class_no": r.class_no,
            "year": r.year, "source": r.source}


def _paged(q, page: int, page_size: int, db: Session):
    total, rows = paginate(q.order_by(RecordORM.id.desc()), page, page_size)
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_brief(r) for r in rows]}


# --------------------------------------------------------------------------
#  单项体检
# --------------------------------------------------------------------------

def _q_no_barcode(db: Session, library_id: int | None):
    return _base(db, library_id).filter(~_no_active_barcode_subq(db).exists())


def _q_no_class(db: Session, library_id: int | None):
    return _base(db, library_id).filter(~_nonempty_class())


def _q_bad_class(db: Session, library_id: int | None):
    return (_base(db, library_id)
            .filter(_nonempty_class(),
                    ~func.trim(RecordORM.class_no).op("~")(r"^[A-Za-z]")))


def _marc_subfield(fields, tag: str, code: str) -> str:
    """从记录行上的 MARC JSON 里取某个子字段的第一个非空值。

    只在**候选集**（同 ISBN 且分类号有分歧的那一千多条）上用，不额外查库。
    """
    for item in fields or []:
        if item.get("tag") != tag:
            continue
        for pair in item.get("sf") or []:
            if len(pair) == 2 and pair[0] == code:
                value = (pair[1] or "").strip()
                if value:
                    return value
    return ""


def _same_isbn_conflict_ids(db: Session, library_id: int | None) -> set[int]:
    """同书异号要报的那批记录 id。

    ## 为什么不能只按 ISBN 分组

    多卷书**共用一个 ISBN** 是国内出版里的常态（一套书一个书号），而每一卷的
    分类号本来就可能不同（上册 `I242.4`、下册 `I246`）。只按 ISBN 分组会把它们
    全报成"同书异号"——那是误报。

    所以"同一本书"要同时满足：**正题名相同 + 副题名（200$e）相同 + 分卷（200$h）
    相同**。副题名/分卷只存在 `fields` JSONB 里，SQL 侧取不干净，所以候选集
    （同 ISBN 下分类号有分歧的那一批，实测一千多条）拉进 Python 逐对比较。

    ## 另外两条口径

    - **只看分类号，不看书次号**：书次号按"种"分配、本来就各不相同（甚至可以是
      著者号形态的 `L824`），拿它比对等于把每一条同 ISBN 的记录都报成异常；
    - **空分类号先排除**：那是「缺分类号」那一项的事，混进来只会让两处重复报同一批记录。
    """
    cn = func.trim(func.coalesce(RecordORM.class_no, ""))
    isbn = func.trim(func.coalesce(RecordORM.isbn_norm, ""))
    cand = (_base(db, library_id)
            .with_entities(isbn.label("isbn"))
            .filter(isbn != "", cn != "")
            .group_by(isbn)
            .having(func.count(func.distinct(cn)) > 1))
    isbns = [row[0] for row in cand.all()]
    if not isbns:
        return set()

    rows = (_base(db, library_id)
            .with_entities(RecordORM.id, RecordORM.isbn_norm, RecordORM.title_norm,
                           RecordORM.class_no, RecordORM.fields)
            .filter(RecordORM.isbn_norm.in_(isbns)).all())
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault((row.isbn_norm or "").strip(), []).append(row)

    bad: set[int] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if (a.class_no or "").strip() == (b.class_no or "").strip():
                    continue                      # 分类号相同，本来就不算冲突
                if not _same_volume(a, b):
                    continue                      # 多卷书的不同卷，分类号本来就该不同
                bad.add(a.id)
                bad.add(b.id)
    return bad


def _title_identity(row) -> tuple:
    """取 (副题名 200$e, 分卷 200$h)，只用于判断"是不是同一卷"。"""
    return (_marc_subfield(row.fields, "200", "e"),
            _marc_subfield(row.fields, "200", "h"))


def _same_volume(a, b) -> bool:
    """两条记录是不是同一卷。

    **只有两边都明确标了、且标得不一样，才算不同卷**（上/下册这种）。一方没填
    不算——"缺失"不等于"不同卷"，那条很可能是漏填，值得报出来让人看一眼。
    宁可多报一条，也别让真问题沉下去。

    ⚠️ 刻意**不比较正题名**：正题名不同的两条（如共用 ISBN 的「抗日英烈故事」
    与「四大发明的故事」）是另一类问题，但按本项定义仍属"同一 ISBN 下分类号打架"，
    历史上一直在报——多卷书排除规则不该顺手把它们也丢掉（实测那样会少报 600 条）。
    """
    e_a, h_a = _title_identity(a)
    e_b, h_b = _title_identity(b)
    if h_a and h_b and h_a != h_b:
        return False
    if e_a and e_b and e_a != e_b:
        return False
    return True


def _q_same_isbn_diff_class(db: Session, library_id: int | None):
    """同书异号：同一 ISBN 下，**同一本书**出现了不同的分类号。

    判定见 `_same_isbn_conflict_ids`（含"多卷书共 ISBN 不算"这条）。
    """
    ids = _same_isbn_conflict_ids(db, library_id)
    return _base(db, library_id).filter(RecordORM.id.in_(ids or {-1}))


def _same_text(col_a, col_b):
    """SQL 版的「去空白后非空且相等」（对应 Python 侧 `(x or "").strip()` 后比较）。

    `btrim` 只去空格——这些列喂给它们的都是归一化产物，不会带制表符；万一真有，
    差异也只是多报一条（往「能发现」那边偏），可接受。
    """
    a = func.btrim(func.coalesce(col_a, ""))
    b = func.btrim(func.coalesce(col_b, ""))
    return (a != "") & (b != "") & (a == b)


def _q_dup_callno_diff_book(db: Session, library_id: int | None):
    """异书同号：同一个**完整索书号**被 ≥2 条记录占用，且它们不是同一种书。

    与「同书异号」正好成对（那边是"同一种书、分类号却不同"）。

    ★ 三条口径（**判据是"能发现"，不是"硬拦"**）：

    1. **按完整索书号比（含卷册号），不是只比 `分类号/书次号` 基号**。
       行业惯例里「复本、不同版本、多卷书等**仍取原来的种次号**，加上辅助区分号」，
       所以**基号重复本身是正常的** —— 多卷书靠卷册号区分（`I247.5/35:上` / `:下`），
       按基号比会把它们全报出来。带上卷册号之后，真正"排架分不开"的才浮出来。
    2. **"同一种书"= 同 ISBN **或** 同归一题名**（`OR`，不是 `AND`）。
       取 `OR` 是**刻意往"少报"那边偏**：不同版本各有 ISBN 但题名相同，
       按 `AND` 会把正常的不同版本报成冲突。宁可漏报，不可乱报。
    3. **只在同一图书库内比**：同书异校各编一条是正常的（`README` 规则 2），
       而且各校索书号本来就各自编号。

    ⚠️ 本项**只报告、不阻断保存** —— 编目校对环节由人工核对冲突，不是让系统拒收。

    ## 实现方式

    当前实现**只扫一遍馆藏**。
        1. `cn` 小表：`(库, 去空白索书号, 记录)` 去重，去掉空索书号；
        2. 让它跟自己按「同库 + 同索书号 + 记录 id 一大一小」配对（`<` 既避免
           自己配自己，也避免 A-B / B-A 重复）；
        3. 滤掉"同一种书"的对；
        4. 冲突对的**两端都算有问题**，两列 UNION 起来。

    判据与逐条判定一致，只是不再逐条重扫。
    """
    cn = (select(HoldingORM.library_id.label("library_id"),
                 func.btrim(func.coalesce(HoldingORM.call_no, "")).label("cn"),
                 HoldingORM.record_id.label("record_id"))
          .where(func.btrim(func.coalesce(HoldingORM.call_no, "")) != "")
          .distinct()
          .subquery("cn"))
    left, right = cn.alias("l"), cn.alias("r")
    rec_l, rec_r = aliased(RecordORM), aliased(RecordORM)
    same_book = (_same_text(rec_l.isbn_norm, rec_r.isbn_norm)
                 | _same_text(rec_l.title_norm, rec_r.title_norm))
    pair = (select(left.c.record_id.label("a"), right.c.record_id.label("b"))
            .select_from(
                left.join(right, and_(left.c.library_id == right.c.library_id,
                                      left.c.cn == right.c.cn,
                                      left.c.record_id < right.c.record_id))
                .join(rec_l, rec_l.id == left.c.record_id)
                .join(rec_r, rec_r.id == right.c.record_id))
            .where(~same_book))
    if library_id:
        pair = pair.where(left.c.library_id == library_id)
    pairs = pair.subquery("pairs")
    ids = select(pairs.c.a).union(select(pairs.c.b))
    return _base(db, library_id).filter(RecordORM.id.in_(ids))


#: 每项的**一句话解释**。这两项的术语（同书异号 / 异书同号）不看解释根本猜不出
#: 方向，卡片上直接写清楚"同一 X、不同 Y"。
HINTS = {
    "no-barcode": "一条在架馆藏条码都没有，无法借还",
    "no-class": "没有分类号，生成不了索书号",
    "bad-class": "分类号不以字母开头，多半是录错了",
    "same-isbn-diff-class": "同一 ISBN，分类号不一致",
    "same-callno-diff-book": "同一个索书号，分给了不同的书",
    "905-drift": "905 字段与在架馆藏对不上，导出/书标会带出错册",
    "barcode-gaps": "条码号中间断号（按缺口段计）",
    "book-no-gaps": "书次号中间断号（按缺口段计）",
}


def _isbn_key():
    return func.trim(func.coalesce(RecordORM.isbn_norm, ""))


def _grouped_paged(db: Session, base_q, key_expr, page: int, page_size: int):
    """按「冲突键」分页：先翻键，再把这一页的键对应的**全部**成员一次取回来。

    为什么按键翻页而不是按记录：这两项的看点就是"哪几条撞在一起"。按记录翻页
    会把一组拆到两页，读者看到的还是散落的条目，等于没分组。
    """
    keys_q = (base_q.with_entities(key_expr.label("k")).distinct()
              .order_by(key_expr.label("k")))
    total, key_rows = paginate(keys_q, page, page_size)
    keys = [row[0] for row in key_rows]
    if not keys:
        return {"total": total, "page": page, "page_size": page_size,
                "grouped": True, "groups": []}
    rows = (base_q.add_columns(key_expr.label("k"))
            .filter(key_expr.in_(keys))
            .order_by(key_expr.label("k"), RecordORM.id).all())
    buckets: dict = {}
    for orm, key in rows:
        buckets.setdefault(key, []).append(_brief(orm))
    groups = [{"key": key, "count": len(buckets[key]), "records": buckets[key]}
              for key in keys if key in buckets]
    return {"total": total, "page": page, "page_size": page_size,
            "grouped": True, "groups": groups}


def _grouped_dup_callno(db: Session, library_id: int | None, page: int, page_size: int):
    """异书同号：按**索书号**分组（索书号挂在馆藏上，不在记录上）。

    一条记录可能有多册、多个索书号，所以它可能同时出现在好几个组里——这是对的，
    因为它确实同时占用了这些索书号。
    """
    cn = (select(HoldingORM.library_id.label("library_id"),
                 func.btrim(func.coalesce(HoldingORM.call_no, "")).label("cn"),
                 HoldingORM.record_id.label("record_id"))
          .where(func.btrim(func.coalesce(HoldingORM.call_no, "")) != "")
          .distinct()
          .subquery("cn"))
    left, right = cn.alias("l"), cn.alias("r")
    rec_l, rec_r = aliased(RecordORM), aliased(RecordORM)
    same_book = (_same_text(rec_l.isbn_norm, rec_r.isbn_norm)
                 | _same_text(rec_l.title_norm, rec_r.title_norm))
    pair = (select(left.c.cn.label("cn"), left.c.record_id.label("a"),
                   right.c.record_id.label("b"))
            .select_from(
                left.join(right, and_(left.c.library_id == right.c.library_id,
                                      left.c.cn == right.c.cn,
                                      left.c.record_id < right.c.record_id))
                .join(rec_l, rec_l.id == left.c.record_id)
                .join(rec_r, rec_r.id == right.c.record_id))
            .where(~same_book))
    if library_id:
        pair = pair.where(left.c.library_id == library_id)
    pairs = pair.subquery("pairs")

    keys_q = select(pairs.c.cn).distinct().order_by(pairs.c.cn)
    total = db.execute(select(func.count()).select_from(keys_q.subquery())).scalar() or 0
    keys = [r[0] for r in db.execute(
        keys_q.offset((page - 1) * page_size).limit(page_size)).all()]
    if not keys:
        return {"total": total, "page": page, "page_size": page_size,
                "grouped": True, "groups": []}

    rid_by_cn: dict = {}
    for cn_key, a, b in db.execute(select(pairs.c.cn, pairs.c.a, pairs.c.b)
                                   .where(pairs.c.cn.in_(keys))).all():
        bucket = rid_by_cn.setdefault(cn_key, set())
        bucket.add(a)
        bucket.add(b)

    ids = sorted({rid for bucket in rid_by_cn.values() for rid in bucket})
    rows = (_base(db, library_id).filter(RecordORM.id.in_(ids))
            .order_by(RecordORM.id).all()) if ids else []
    by_id = {r.id: _brief(r) for r in rows}
    groups = []
    for cn_key in keys:
        members = [by_id[rid] for rid in sorted(rid_by_cn.get(cn_key, ())) if rid in by_id]
        if members:
            groups.append({"key": cn_key, "count": len(members), "records": members})
    return {"total": total, "page": page, "page_size": page_size,
            "grouped": True, "groups": groups}


def _q_905_drift(db: Session, library_id: int | None):
    """905 条数与**在架**馆藏数不一致。

    905 由馆藏表重建（每在架册一条，见 services.record_projection._rebuild_905），两者必须相等。
    不等的来源都是「某条流程改了馆藏却没能让 905 跟着变」——典型是曾经的按遗失
    处理漏 flush。它不会自愈，导出与书标会带出
    已不在架的册，而统计册数已把它排除：账实不一致且肉眼看不出来。

    用 PG 的 jsonb 函数直接数 905 字段：`records.fields` 是 JSONB 数组，
    每项形如 `{"tag": "905", "sf": [["b", "0000001"], ...]}`。
    """
    drift_ids = text(
        "SELECT r.id FROM records r "
        "WHERE (SELECT count(*) FROM jsonb_array_elements(r.fields) f "
        "       WHERE f->>'tag' = '905') "
        "   <> (SELECT count(*) FROM holdings h "
        "       WHERE h.record_id = r.id AND h.status = 'AVAILABLE')")
    return _base(db, library_id).filter(RecordORM.id.in_(drift_ids))


CHECKS = {
    "no-barcode": ("无条码", _q_no_barcode),
    "no-class": ("缺分类号", _q_no_class),
    "bad-class": ("分类号结构异常", _q_bad_class),
    "same-isbn-diff-class": ("同书异号", _q_same_isbn_diff_class),
    "same-callno-diff-book": ("异书同号", _q_dup_callno_diff_book),
    "905-drift": ("905 与馆藏不符", _q_905_drift),
}


def _ids(q) -> set[int]:
    """同口径体检查询，只把 **id** 装进内存（summary 的「有问题记录」去重用）。

    原实现 `q.all()` 会把每条命中记录的**完整 ORM 行**拉进来——records 行上挂着
    JSONB 全字段数组（`fields`）与 `marc_raw` LargeBinary，5 个体检项逐个 `all()`
    等于把整库图书数据在内存里搬几遍，而每项真正要的只是一个 id 集合。
    """
    return {row[0] for row in q.with_entities(RecordORM.id).all()}


@router.get("/summary")
@api(module="inspect")
def inspect_summary(library_id: int | None = None, db: Session = Depends(get_db)):
    """体检概览（体检页首屏卡片）。"""
    items = []
    bad_ids: set[int] = set()
    for key, (label, fn) in CHECKS.items():
        # 各项结果都是「记录行」级别的集合（_base 起步，行唯一），count == len(ids)，
        # 取一次 id 顺便拿到计数，省掉原来单独的 count() 那一枪。
        ids = _ids(fn(db, library_id))
        items.append({"key": key, "label": label, "hint": HINTS.get(key, ""),
                      "count": len(ids)})
        bad_ids |= ids
    # 两类「断号」都是**段**计数（不是记录数），口径同族，放一起走同一段逻辑。
    # 在函数体内取函数名而不是模块级建元组：这两个函数定义在本函数**之后**，
    # 模块级引用会在 import 时 NameError。
    for key, label, fn in (("barcode-gaps", "条码断号（段）", barcode_gaps),
                           ("book-no-gaps", "书次号断号（段）", book_no_gaps)):
        items.append({"key": key, "label": label, "hint": HINTS.get(key, ""),
                      "count": fn(library_id=library_id,
                                  max_segments=MAX_GAP_SEGMENTS, db=db)["segment_count"]})
    total = _base(db, library_id).count()
    return {"library_id": library_id, "record_total": total,
            "record_with_issue": len(bad_ids), "items": items}


@router.get("/no-barcode", response_model=RecordBriefPage)
@api(module="inspect")
def inspect_no_barcode(library_id: int | None = None,
                       page: int = Query(1, ge=1),
                       page_size: int = Query(50, ge=1, le=500),
                       db: Session = Depends(get_db)):
    """无条码记录：不存在任何条码非空的馆藏。"""
    return _paged(_q_no_barcode(db, library_id), page, page_size, db)


@router.get("/no-class", response_model=RecordBriefPage)
@api(module="inspect")
def inspect_no_class(library_id: int | None = None,
                     page: int = Query(1, ge=1),
                     page_size: int = Query(50, ge=1, le=500),
                     db: Session = Depends(get_db)):
    """缺分类号：class_no 为空（无法生成索书号，必须补）。"""
    return _paged(_q_no_class(db, library_id), page, page_size, db)


@router.get("/bad-class", response_model=RecordBriefPage)
@api(module="inspect")
def inspect_bad_class(library_id: int | None = None,
                      page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=500),
                      db: Session = Depends(get_db)):
    """分类号结构异常：非空但不以字母开头（对齐质检规则 R6901）。"""
    return _paged(_q_bad_class(db, library_id), page, page_size, db)


@router.get("/same-isbn-diff-class")
@api(module="inspect")
def inspect_same_isbn_diff_class(library_id: int | None = None,
                                 page: int = Query(1, ge=1),
                                 page_size: int = Query(50, ge=1, le=500),
                                 grouped: bool = False,
                                 db: Session = Depends(get_db)):
    """同书异号：同一 ISBN 的记录分类号不一致（对标参考系统的「自动异号同名提醒」）。

    `grouped=true` 时按 ISBN 分组返回：组头就是那个 ISBN，组内是分类号打架的几条，
    一眼能看出哪条编错了。平铺模式保留给导出与老调用方。
    """
    if grouped:
        return _grouped_paged(db, _q_same_isbn_diff_class(db, library_id),
                              _isbn_key(), page, page_size)
    return _paged(_q_same_isbn_diff_class(db, library_id), page, page_size, db)


@router.get("/same-callno-diff-book")
@api(module="inspect")
def inspect_same_callno_diff_book(library_id: int | None = None,
                                  page: int = Query(1, ge=1),
                                  page_size: int = Query(50, ge=1, le=500),
                                  grouped: bool = False,
                                  db: Session = Depends(get_db)):
    """异书同号：不同的书占了同一个索书号（与「同书异号」成对）。

    口径与"为什么只报告、不阻断"见 `_q_dup_callno_diff_book`。
    `grouped=true` 时按**索书号**分组返回——平铺列表连索书号这一列都没有，
    看不出谁跟谁撞了。
    """
    if grouped:
        return _grouped_dup_callno(db, library_id, page, page_size)
    return _paged(_q_dup_callno_diff_book(db, library_id), page, page_size, db)


# --------------------------------------------------------------------------
#  条码断号
# --------------------------------------------------------------------------

def barcode_gaps(library_id: int | None = None,
                 max_segments: int = MAX_GAP_SEGMENTS,
                 db: Session = Depends(get_db)):
    """条码断号：按 (图书库, 前缀, 位数) 分组，找出数字段的空缺区间。

    例：0001, 0002, 0005 → 缺 0003-0004（1 段，2 个号）。
    位数纳入分组键，避免 0001 与 100 被当成同一序列。

    ## 实现（下推数据库）

    旧版把全库条码拉进 Python 正则分组再逐对比较；现在一条窗口函数查询在
    数据库端完成「拆前缀/数字段 → 排序 → 找相邻回退」，应用侧只收到**断掉的段**
    而不是全部条码——万册库每次体检不再搬运全量条码行。

    口径与旧版逐字对齐：
    - 正则 `^(.*?)([0-9]+)$` 等价旧 `_TAIL_NUM`（`\\d` 在 ASCII 条码域内就是
      `[0-9]`，显式写成 `[0-9]` 避免两边方言对非 ASCII 数字判定不一致）；
    - 位数（`length(digits)`）与数字值入分组键；
    - 同组内同一数字只算一次（DISTINCT 对齐旧版 `set` 语义）；重复号即便出现，
      排序后相邻差值为 0，也不会造出假缺口；
    - 组内不足 2 个号没有序列可言，窗口函数天然不出行。
    """
    bc_text = func.btrim(func.coalesce(HoldingORM.barcode, ""))
    prefix = func.substring(bc_text, r"^(.*?)[0-9]+$")
    digits = func.substring(bc_text, r"[0-9]+$")
    numbered = (select(HoldingORM.library_id.label("lib"), prefix.label("prefix"),
                       func.length(digits).label("width"),
                       cast(digits, BigInteger).label("num"))
                .where(HoldingORM.barcode.isnot(None), bc_text != "",
                       digits.isnot(None))
                .distinct())
    numbered_sq = numbered.subquery()
    prev_num = func.lag(numbered_sq.c.num).over(
        partition_by=[numbered_sq.c.lib, numbered_sq.c.prefix, numbered_sq.c.width],
        order_by=numbered_sq.c.num,
    )
    # 窗口函数不能出现在 WHERE 里，先落一层子查询再过滤断号
    stage = (select(numbered_sq.c.lib, numbered_sq.c.prefix,
                    numbered_sq.c.width, prev_num.label("prev"),
                    numbered_sq.c.num).subquery())
    rows = db.execute(
        select(stage.c.lib, stage.c.prefix, stage.c.width, stage.c.prev, stage.c.num)
        .where(stage.c.prev.isnot(None), stage.c.num - stage.c.prev > 1)
    ).all()

    segments = [{
        "library_id": lib, "prefix": prefix, "width": width,
        "from": prev + 1, "to": cur - 1, "count": cur - prev - 1,
        "label": f"{prefix}{prev + 1:0{width}d}"
                 + (f" ~ {prefix}{cur - 1:0{width}d}" if cur - prev > 2 else ""),
    } for lib, prefix, width, prev, cur in rows]
    segments.sort(key=lambda s: (-s["count"], s["library_id"], s["prefix"]))
    total_missing = sum(s["count"] for s in segments)
    return {"library_id": library_id, "segment_count": len(segments),
            "missing_count": total_missing,
            "truncated": len(segments) > max_segments,
            "items": segments[:max_segments]}


@router.get("/905-drift")
@api(module="inspect")
def inspect_905_drift(library_id: int | None = None,
                      page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=500),
                      db: Session = Depends(get_db)):
    """905 与馆藏不符：905 字段数 ≠ 在架馆藏数。

    打开任一命中记录保存一次（走 905 重建）即可修复。
    """
    return _paged(_q_905_drift(db, library_id), page, page_size, db)


@router.get("/barcode-gaps")
@api(module="inspect")
def inspect_barcode_gaps(library_id: int | None = None,
                         max_segments: int = Query(MAX_GAP_SEGMENTS, ge=1, le=2000),
                         db: Session = Depends(get_db)):
    """条码断号明细（对标旧版系统的「查找空条形码」）。"""
    return barcode_gaps(library_id=library_id, max_segments=max_segments, db=db)


# --------------------------------------------------------------------------
#  书次号断号
# --------------------------------------------------------------------------

def book_no_gaps(library_id: int | None = None,
                 max_segments: int = MAX_GAP_SEGMENTS,
                 db: Session = Depends(get_db)):
    """书次号断号：按 (图书库, 分类号) 分组，找出书次号序列里的空缺。

    与「条码断号」同族，但分组键不同 —— 书次号**按分类号各自编号**
    （计数器键就是 `lib{id}|{class_no}`），所以 `I18/1` 与 `K209/1` 是两个独立序列。

    三条口径（改动前先想清楚）：

    1. **只认纯数字书次号**：书次号允许是著者号形态（`I210.4/L824`），
       那种没有"序列"可言，混进来全是误报（而且 `int()` 会直接炸）；
    2. **按数值归一**：`1` 与 `01` 是同一个号（位数在书次号里没有含义）。
       ⚠️ 收进 `set` **不是**为了修什么 bug —— 同一"种"的多册共用同一个书次号，
       重复值本身**不会造出假缺口**；集合只是省内存、也更贴"一个号一次"的语义；
    3. **来源是 `holdings.call_no`**：书次号没有独立列，只存在于索书号里
       （`分类号/书次号[:卷册号]`），解析走 `core.callno.parse_call_no()`
       —— 与写回 `set_book_no()` 互为逆运算的**同一份**口径，不另写一套拆分。
    """
    from ..core.callno import parse_call_no

    q = db.query(HoldingORM.library_id, HoldingORM.call_no).filter(
        HoldingORM.call_no.isnot(None), func.trim(HoldingORM.call_no) != "")
    if library_id:
        q = q.filter(HoldingORM.library_id == library_id)
    # 复本多册同号不会造出假缺口（旧版用 set 去重对齐），DISTINCT 先把重复抻掉的
    # 行数省下来——万册库的索书号按"种"重复率很高，去重往往大于四分之一。
    q = q.distinct()

    groups: dict[tuple[int, str], set[int]] = defaultdict(set)
    for lib_id, call_no in q.all():
        class_no, book_no, _vol = parse_call_no(call_no)
        if not class_no or not book_no.isdigit():
            continue
        groups[(lib_id, class_no)].add(int(book_no))

    segments: list[dict] = []
    for (lib_id, class_no), nums in groups.items():
        if len(nums) < 2:
            continue
        ordered = sorted(nums)
        for prev, cur in zip(ordered, ordered[1:]):
            if cur - prev > 1:
                segments.append({
                    "library_id": lib_id, "class_no": class_no,
                    "from": prev + 1, "to": cur - 1, "count": cur - prev - 1,
                    "label": f"{class_no}/{prev + 1}"
                             + (f" ~ {class_no}/{cur - 1}" if cur - prev > 2 else ""),
                })
    segments.sort(key=lambda s: (-s["count"], s["library_id"], s["class_no"]))
    return {"library_id": library_id, "segment_count": len(segments),
            "missing_count": sum(s["count"] for s in segments),
            "truncated": len(segments) > max_segments,
            "items": segments[:max_segments]}


@router.get("/book-no-gaps")
@api(module="inspect")
def inspect_book_no_gaps(library_id: int | None = None,
                         max_segments: int = Query(MAX_GAP_SEGMENTS, ge=1, le=2000),
                         db: Session = Depends(get_db)):
    """书次号断号明细：同一分类号下，书次号序列中间的空缺。"""
    return book_no_gaps(library_id=library_id, max_segments=max_segments, db=db)


@router.get("/export")
@api(module="inspect")
def export_inspect(scan: str, library_id: int | None = None,
                   db: Session = Depends(get_db)):
    """导出某一体检项的完整结果（Excel）。"""
    record_labels = {
        "no-barcode": "无条码",
        "no-class": "缺分类号",
        "bad-class": "分类号结构异常",
        "same-isbn-diff-class": "同书异号",
        "same-callno-diff-book": "异书同号",
    }
    gap_labels = {
        "barcode-gaps": "条码断号",
        "book-no-gaps": "书次号断号",
    }
    wb = Workbook()
    ws = wb.active
    if scan in record_labels:
        ws.title = record_labels[scan][:31]
        query = CHECKS[scan][1](db, library_id)
        rows = query.order_by(RecordORM.id.desc()).limit(50000).all()
        ws.append(["记录号", "图书库", "题名", "ISBN", "作者", "分类号", "出版年", "来源"])
        for row in rows:
            ws.append([row.id, row.library_id, row.title_norm or "", row.isbn_norm or "",
                       row.author_norm or "", row.class_no or "", row.year or "",
                       row.source or ""])
        widths = [10, 10, 42, 18, 20, 14, 10, 16]
        filename = f"{record_labels[scan]}_{len(rows)}条.xlsx"
    elif scan in gap_labels:
        ws.title = gap_labels[scan][:31]
        if scan == "barcode-gaps":
            data = barcode_gaps(library_id=library_id, max_segments=2000, db=db)
            ws.append(["图书库", "条码前缀", "位数", "起始号", "结束号", "缺失数量", "范围"])
            for item in data["items"]:
                ws.append([item["library_id"], item["prefix"], item["width"],
                           item["from"], item["to"], item["count"], item["label"]])
            widths = [10, 16, 8, 12, 12, 10, 36]
        else:
            data = book_no_gaps(library_id=library_id, max_segments=2000, db=db)
            ws.append(["图书库", "分类号", "起始号", "结束号", "缺失数量", "范围"])
            for item in data["items"]:
                ws.append([item["library_id"], item["class_no"], item["from"],
                           item["to"], item["count"], item["label"]])
            widths = [10, 18, 12, 12, 10, 36]
        filename = f"{gap_labels[scan]}_{data['segment_count']}段.xlsx"
    else:
        raise HTTPException(400, "不支持的体检项")

    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width
    ws.freeze_panes = "A2"
    return xlsx_response(wb, filename)
