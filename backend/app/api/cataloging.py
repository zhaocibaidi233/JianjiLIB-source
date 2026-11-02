# -*- coding: utf-8 -*-
"""套录检索与选录入库 + 索书号生成。"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CallNoCounter, HoldingORM, RecordORM
from ..core import isbn as isbnlib
from ..core.callno import make_call_no, parse_call_no
from ..core.clean import normalize_class_no
from ..core.volume import join_call_no
from ..services import class_no_rule, localize_record, record_from_payload
from ..services.batch import _raise_seq, bump_counter
from ..sources import search_tiered
from ..sources.registry import active_sources
from ..core.route_meta import api

router = APIRouter(prefix="/cataloging", tags=["cataloging"])

# "这串是不是 ISBN"：去掉连字符 / 空格后，是 10 位（末位可为 X）或 13 位数字。
_ISBN_SHAPE = re.compile(r"^(?:\d{9}[\dXx]|\d{13})$")


def _looks_like_isbn(s: str) -> bool:
    return bool(_ISBN_SHAPE.match(re.sub(r"[^0-9Xx]", "", s or "")))


_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def _split_title_author(q: str) -> tuple[str, str] | None:
    """「三体 刘慈欣」→ ("三体", "刘慈欣")；不是这个形态返回 None。

    ## 为什么需要这个

    录入栏只有一个框，编目员的自然写法是「书名 作者」——但后端原先把整串当成
    一个题名去做**完全相等**匹配，必然落空；落到 Z39.50 更糟：`@attr 1=4 三体 刘慈欣`
    会被 yaz 当成"题名 + 多余词元"，直接语法错误，连 `Number of hits` 都不输出，
    前端于是显示「所有源都不可达」——看起来像网络问题，其实是检索式拼错了。

    ## 为什么只在中日韩文字时才拆

    `Harry Potter and the Philosopher's Stone` 这种题名本身带空格，拆开就废了。
    只在**首段与其余部分都含中日韩文字**时才认定是「题名 + 责任者」——中文书名
    极少带空格，这个判据足够稳，也不会误伤西文题名。
    """
    parts = (q or "").split()
    if len(parts) < 2:
        return None
    head, tail = parts[0], " ".join(parts[1:])
    if _CJK.search(head) and _CJK.search(tail):
        return head, tail
    return None


class SearchIn(BaseModel):
    isbn: str | None = None
    title: str | None = None
    author: str | None = None
    # 通用检索词：录入栏那个「套录」框只有一个输入框，由后端判断这是 ISBN 还是书名 / 作者。
    q: str | None = None
    # 一次最多返回多少条候选。**不给就用全局设置**（全局设置 → 资源库 → 套录命中条数，
    # 默认 8）。让服务端决定而不是前端传死值：候选列表、扩展检索、批量套录都要同一个口径，
    # 前端各写一个数字迟早不一致。
    limit: int | None = None
    # 第几页（从 0 起）。翻页靠它，`limit` 就是每页条数。
    page: int = 0
    library_id: int | None = None      # 图书库层按此隔离；空则落到默认库
    # 不短路：各层命中合并返回。给「库里已有，但仍想看看资源库 / Z39.50 怎么著录」
    # 用——默认关，因为打开就意味着每次都要等 Z39.50 的网络往返。
    all_tiers: bool = False


@router.post("/search")
@api(module="records", level="read")
def cataloging_search(payload: SearchIn, db: Session = Depends(get_db)):
    """套录检索：图书库 → 本地资源库 → Z39.50，**前一层命中即短路**。

    `q` 是给录入栏单输入框用的通用检索词：
      - 形如 ISBN（去连字符后 10 / 13 位）→ 按 ISBN 查；
      - 否则**先按题名查，没命中再按责任者查一次**。为什么分两次：Z39.50 会把
        title + author 拼成 `@and`（AND），两个一起传会互相打架、什么都查不到。
    响应里的 `matched_by` 回显实际用的是哪一种，前端据此提示"按责任者找到的"。

    返回的候选里 `record_id` 非空表示命中的是**已入库记录**。前端**不再据此自动打开
    条目**——同 ISBN 的多卷书（上下册共用一个 ISBN）在库里是多条记录，命中图书库时
    要把全部记录连同 `volume` 一起交给编目员，由他决定「打开已有条目加复本」还是
    「作为新记录另编一册」（见）。
    """
    isbn, title, author = payload.isbn, payload.title, payload.author
    q = (payload.q or "").strip()
    by_generic_title = False
    if q and not (isbn or title or author):
        if _looks_like_isbn(q):
            isbn = q
        elif (split := _split_title_author(q)) is not None:
            # 「书名 作者」：拆开分别查，别再整串当题名（见 _split_title_author）
            title, author = split
        else:
            title = q
            by_generic_title = True
    if not (isbn or title or author):
        raise HTTPException(400, "至少提供 isbn/title/author/q 之一")

    sources = active_sources(db, payload.library_id)
    from ..services.settings import CATALOG_LIMIT_MAX, CATALOG_LIMIT_MIN, catalog_limit
    limit = payload.limit if payload.limit is not None else catalog_limit(db)
    limit = max(CATALOG_LIMIT_MIN, min(CATALOG_LIMIT_MAX, int(limit)))
    matched_by = "isbn" if isbn else "title"
    page = max(0, int(payload.page or 0))
    cands = search_tiered(sources, isbn=isbn, title=title,
                          author=author, limit=limit, page=page,
                          all_tiers=payload.all_tiers)
    if by_generic_title and not cands:
        # 书名没命中 → 再当作者找一次。资源库目前只支持 ISBN / 书名，作者主要靠 Z39.50。
        cands = search_tiered(sources, author=q, limit=limit, page=page)
        if cands:
            matched_by = "author"

    # 总条数封顶（`search_tiered` 已按 limit 截过，这里再兜一次，防止将来改动漏掉）。
    cands = cands[:limit]

    return {"matched_by": matched_by, "page": page, "limit": limit,
            "candidates": [{
        "source": c.source_name, "key": c.source_key, "title": c.title,
        "author": c.author, "publisher": c.publisher, "year": c.year,
        "class_no": c.class_no, "isbn": c.isbn,
        # 已入库记录 id：非空 = 命中图书库，前端弹窗让编目员二选一（不再自动打开）
        "record_id": c.record_id,
        # 种级卷册号：同 ISBN 多卷书靠它区分候选是哪一册（其余源为 None）
        "volume": c.volume,
        # 归一 ISBN（统一为 13 位）：同一本书在不同源可能以 ISBN-10/13 两种形态
        # 出现，前端按它去重才不会把同一本书显示成两条。
        "isbn_key": isbnlib.canonical(c.isbn) if c.isbn else None,
        "record": None if c.record is None else {
            "leader": c.record.leader,
            "fields": json.loads(c.record.to_json()),
        },
    } for c in cands]}


class ImportIn(BaseModel):
    """用户从候选中选定一条记录（record 为检索返回的完整记录）。"""
    record: dict
    agency: str = "LOCAL"


@router.post("/prepare")
@api(module="records", level="read")
def cataloging_prepare(payload: ImportIn, db: Session = Depends(get_db)):
    """载入编辑器：本地化改写后返回临时副本（不写图书库）。

    图书库只存编目校验好的记录——前端编辑确认后经 POST /records（质检门槛）入库。
    """
    rec = record_from_payload(payload.record)
    if not rec.fields:
        raise HTTPException(400, "record 不能为空")
    # 套录时按全局设置（的三键规则）截短过长分类号（690$a）。**只在套录路径**
    # 生效，库里已有记录不受影响（见 services/localize.py 第 4 步注释与）。
    rec, stats = localize_record(rec, agency=payload.agency,
                                 class_rule=class_no_rule(db))
    # 记录级卷册号缺省：套录源没给 905 时 volume 全空，同套多卷（囧成语 $h1..6、
    # 学汉语分级读物 $h1/2/3）在编辑页与列表上没有"第几册"可看。200$h（分册号）
    # 是 CNMARC 里现成的卷次标识，取第一个作 volume 缺省——编目员可在界面上改
    # （改后索书号带 :卷 后缀），不改也至少有值可辨。905 给了 volume 的不受影响
    # （前端 populateExtra 只在 record.volume 缺失时才回退到这个值）。
    hno = rec.first_value("200", "h")
    if hno and hno.strip():
        stats["volume_from_200h"] = hno.strip()
    return {"record": {"leader": rec.leader, "fields": json.loads(rec.to_json()),
                       "volume": (hno or "").strip() or None},
            "localize": stats}


class CallNoIn(BaseModel):
    class_no: str
    volume: str | None = None
    library_id: int | None = None


def _counter_key(db: Session, library_id: int | None, class_no: str) -> str:
    """书次号按图书库隔离（每校 I247.5/1 各自编号）。"""
    from ..api.book_libraries import resolve_library
    if library_id is None:
        library_id = resolve_library(db, None).id
    return f"lib{library_id}|{class_no}"


@router.post("/callno")
@api(module="records")
def gen_callno(payload: CallNoIn, db: Session = Depends(get_db)):
    """生成索书号（分类号/书次号），书次号计数按图书库隔离。

    取号走 `bump_counter` 的原子自增：旧实现（读出 -> +=1 -> 写回）在两个
    编目端同时点「生成索书号」时会领到同一个号。分类号先归一（大写）——
    计数器按「图书库×分类号」字符串分键，不归一会 fork（见 normalize_class_no）。
    """
    class_no = normalize_class_no(payload.class_no)
    key = _counter_key(db, payload.library_id, class_no)
    seq = bump_counter(db, key)
    call_no = make_call_no(class_no, _DBCounter(db, key), volume=payload.volume)
    db.commit()
    return {"call_no": call_no, "seq": seq}


@router.get("/callno-preview")
@api(module="records")
def callno_preview(class_no: str, volume: str | None = None,
                   library_id: int | None = None,
                   db: Session = Depends(get_db)):
    """索书号预览（peek，不消耗号段）：分类号/下一个书次号[:卷册号]（按图书库隔离）。

    分类号归一（大写）后再查计数器：预览键必须与保存键（clean_record 归一后的
    690$a）同串，否则用户按预览号手敲书次号会和实际计数器错位。
    """
    class_no = normalize_class_no(class_no)
    key = _counter_key(db, library_id, class_no)
    counter = db.get(CallNoCounter, key)
    seq = (counter.current_seq if counter else 0) + 1
    # 与 core.callno.make_call_no 同一口径：基号 = 分类号/书次号，卷册号用
    # core.volume.VOLUME_SEP（":"）后置。
    # 走 join_call_no 而不是手拼，避免两处规则漂移。
    no = join_call_no(f"{class_no}/{seq}", volume)
    return {"call_no": no, "seq": seq}


@router.post("/callno-resync")
@api(module="records")
def resync_callno(db: Session = Depends(get_db)):
    """按馆藏重算书次号计数器（**只升不降**）—— 计数器脱钩的自愈入口。

    背景：旧版系统 的书次号是保存时现算 max+1，天然与数据一致；本系统的计数器
    是独立表（callno_counters），直接改库、历史遗留或未来未预见的路径都可能让
    它**低于**馆藏里的真实最大号——之后自动取号就会撞号。本端点扫描全部馆藏，把每个「图书库×分类号」的计数器抬到
    馆藏索书号里的最大纯数字书次号（已达标的键不动），返回抬过的键清单。

    口径说明：
      - 只升不降（`_raise_seq` 的 GREATEST 语义）：馆藏里的号就是"已占用"的证据，
        抬到位永不撞号；降回去反而可能撞上手工指定过、馆藏已删的号。
      - 附加区分号（`I210.4/L824/2` 的 `L824/2`）不是纯数字，不参与最大号统计
        （与 set_book_no 的抬号条件一致）。
      - 键的分类号取 `records.class_no`（与生成路径的键同源），非馆藏索书号前缀。

    权限：/api/cataloging 映射到 records 模块（core/perm.py），写操作按 records
    write 判定 —— 与编目保存同档。
    """
    rows = (db.query(HoldingORM.library_id, RecordORM.class_no, HoldingORM.call_no)
            .join(RecordORM, RecordORM.id == HoldingORM.record_id)
            .filter(HoldingORM.call_no.isnot(None),
                    RecordORM.class_no.isnot(None))
            .all())
    best: dict[tuple[int, str], int] = {}
    for lib_id, class_no, call_no in rows:
        _cls, book, _vol = parse_call_no(call_no)
        if not book.isdigit():
            continue
        key_cls = normalize_class_no(class_no)
        if not key_cls:
            continue
        k = (int(lib_id), key_cls)
        if int(book) > best.get(k, 0):
            best[k] = int(book)
    raised, already_ok = [], 0
    for (lib_id, class_no), target in sorted(best.items()):
        key = f"lib{lib_id}|{class_no}"
        cur = db.get(CallNoCounter, key)
        cur_seq = cur.current_seq if cur else 0
        if target > cur_seq:
            _raise_seq(db, key, target)
            raised.append({"key": key, "from": cur_seq, "to": target})
        else:
            already_ok += 1
    db.commit()
    return {"keys": len(best), "raised": raised, "already_ok": already_ok}


class _DBCounter:
    """core.callno 协议的数据库适配（route 内已自增，bump 仅返回；绑定完整 key）。"""

    def __init__(self, db: Session, key: str):
        self._db, self._key = db, key

    def current(self, class_no: str) -> int:
        c = self._db.get(CallNoCounter, self._key)
        return c.current_seq if c else 0

    def bump(self, class_no: str) -> int:
        return self.current(class_no)
