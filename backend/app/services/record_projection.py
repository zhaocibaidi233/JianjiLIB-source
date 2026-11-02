# -*- coding: utf-8 -*-
"""Record 派生投影的统一入口：905、检索列、可借数。

**Phase 6 起 905 的实现也在这里**：`rebuild_905` 直接重建 `fields` + `marc_raw`，
表结构与子字段布局仍是 §10.1 / `_mk905` 的既有口径。检索列与可借数继续转调真源：

| 投影 | 真源实现 | 契约 |
| --- | --- | --- |
| 905 | 本模块 `_rebuild_905` / `_mk905` | §10.1 |
| 检索列 | `services.convert.compute_search_columns` | §10.2 |
| 可借数 | `services.reservations.available_copies` | §10.3 |

本模块只做三件事：定位记录、重建投影、flush。**不 commit**（§9.3：服务层不 commit，
提交由 API/命令处理器统一掌控），不改 905 子字段布局，不改表结构，不碰 Item 状态机。

为什么要这一层（而不是让调用方继续直接 import batch / convert / reservations）：

1. **口径单一**：905 的行集（可流通 Item）、检索列的算法、可借数的公式三者互相关联
   ——905 巡检拿“可流通册”对账，可借数拿“AVAILABLE − ACTIVE Loan”算，检索列只认
   书目字段。散在三个模块里时，新写一条流程很容易只调了其中两个（历史上 `mark_lost`
   漏 flush 就是这类漂移）。
2. **迁移留缝**：Phase 2 起这些投影要搬进领域层。调用方先收敛到本模块，搬家时只改
   这一个文件的函数体，调用点不动（Phase 6 已把 905 实现搬完）。

⚠️ 循环导入：`batch` / `reservations` 都间接依赖 `services` 包（`batch` 内还反向
`from ..services import orm_to_record`），故**一律在函数内 local import**，不在模块顶层
引入，避免 `app.services.__init__` 与这些模块互相等待。
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

__all__ = [
    "rebuild_905",
    "rebuild_905_many",
    "available_copies",
    "rebuild_search",
]

#: 我方**登记**的 905 扩展子字段：不由馆藏表产生（`_mk905` 不写它们），但 905 重建时
#: 必须原样搬运 —— 否则“导入图书库”会把别人家编目系统塞在 905 里的值**随 905 一起
#: 静默丢掉**（参考系统的约定是 `905$Z` 著者号 / `905$S` 四角号，见 io/iso.py 的同名登记）。
#: ⚠️ 只搬运**登记过**的码：把任意未知子字段都带上，等于让脏数据永久粘在 905 上。
EXTRA_905_CODES: tuple[str, ...] = ("Z", "S")


def rebuild_905(db: Session, record_id: int, *,
                extra_source: Any = None) -> None:
    """按该记录当前的**可流通馆藏**重建 905，落回 `fields` + `marc_raw`。

    收录范围（§10.1 D3）：`Item.status = AVAILABLE`（含当前在借的册 —— 在借不改处置
    状态），排除 `SUSPENDED/WITHDRAWN/LOST/IN_PROCESS`。905 为空是合法状态。

    行为约定：
      - 记录不存在 → 安静返回（幂等调用方不必先查存在性）；
      - 只重建 905，**不重算检索列**（两者触发条件不同，见 `rebuild_search`）；
      - 不 commit，只 flush。

    实现先 flush，再按 `_mk905` 的标准子字段布局逐册生成，
    并保留 `905$Z/$S` 扩展码（§15.4）。
    """
    from ..models import RecordORM
    from .convert import orm_to_record

    orm = db.get(RecordORM, record_id)
    if orm is None:
        return
    _rebuild_905(db, orm, orm_to_record(orm), extra_source=extra_source)
    db.flush()


def rebuild_905_many(db: Session, record_ids: Iterable[int]) -> None:
    """批量重建 905：先去重、再按 id 升序逐条重建。

    去重是必须的 —— 批量注销/恢复里同一条记录常被多册命中，逐条重建会在一条记录上
    反复重写 `fields`/`marc_raw`（多册时 O(n²) 的 JSON 重排）。升序是为了让并发批量
    操作以固定顺序取行锁，降低互相死锁的概率（与 `api/holdings.py` 的加锁顺序一致）。

    不存在的 id 由 `rebuild_905` 安静跳过，不抛错。
    """
    ids = {int(rid) for rid in record_ids if rid is not None}
    for record_id in sorted(ids):
        rebuild_905(db, record_id)


def available_copies(db: Session, record_id: int) -> int:
    """当前可借册数（§10.3）：`count(AVAILABLE) - count(ACTIVE Loan on those)`。

    预约、OPAC、借书校验都读这一个投影，不各自写一套。注意它回答的是“这本书还有没有
    册”，不是“这个读者能不能借”（后者是 `borrow_blocked`，读者维度）。

    实现优先转调 `reservations.available_copies`（口径真源）；若该模块因循环导入不可
    用时，退化为本模块内的同口径实现 `_available_copies_local`。两条路径的返回值必须
    相等，`tests/test_record_projection.py` 对此有断言。
    """
    try:
        from .reservations import available_copies as _impl
    except ImportError:
        return _available_copies_local(db, record_id)
    return _impl(db, record_id)


def _available_copies_local(db: Session, record_id: int) -> int:
    """`available_copies` 的兜底实现，口径与 `reservations.available_copies` 逐字相同。

    只统计 `status = AVAILABLE` 的册（SUSPENDED/WITHDRAWN/LOST/IN_PROCESS 不计），
    再减去这些册上仍未归还（ACTIVE）的外借。不按 `Item.status = ON_LOAN` 计算 ——
    借出不改处置状态（§18.1），ON_LOAN 只是历史兼容值。
    """
    from sqlalchemy import func

    from ..models import LOAN_ACTIVE, STATUS_AVAILABLE, HoldingORM, LoanORM

    holding_ids = [hid for (hid,) in
                   db.query(HoldingORM.id)
                   .filter(HoldingORM.record_id == record_id,
                           HoldingORM.status == STATUS_AVAILABLE).all()]
    if not holding_ids:
        return 0
    on_loan = (db.query(func.count(LoanORM.id))
               .filter(LoanORM.holding_id.in_(holding_ids),
                       LoanORM.status == LOAN_ACTIVE)
               .scalar() or 0)
    return max(0, len(holding_ids) - int(on_loan))


def rebuild_search(db: Session, record_id: int) -> None:
    """从 `fields` 重算检索冗余列并写回 ORM（§10.2）。

    重算的列：`title_norm` / `title_pinyin` / `title_pinyin_initials` / `author_norm` /
    `isbn_norm` / `year` / `class_no`。

    口径（不在这里重写，统一走 `convert.compute_search_columns`）：
      - `title_norm` = 200$a + 适用 200$i（分辑名，含多级），**不拼接 Item 卷册**；
      - `class_no` = 690$a；`year` = 210$d 规范化；`isbn_norm` = 010$a 归一到 ISBN-13 形态。

    投影在 Record 写入时生成、不手工维护；本函数是“变更后重建”的唯一入口。
    只写这 7 列，不动 `fields` / `marc_raw` / 905；不 commit，只 flush。
    """
    from ..models import RecordORM
    from .convert import compute_search_columns, orm_to_record

    orm = db.get(RecordORM, record_id)
    if orm is None:
        return
    for key, value in compute_search_columns(orm_to_record(orm)).items():
        setattr(orm, key, value)
    db.flush()


def _rebuild_905(db: Session, orm, rec, extra_source=None) -> None:
    """按**在架**馆藏重建 905，并把结果落回 `orm`（`fields` + `marc_raw`）。

    `create_holdings` 与 `remove_holdings_sync` 的收尾**逐字相同**，故只留这一份 ——
    两处各写一遍时，给其中一处加“保留扩展子字段”必然漏掉另一处（静默不一致）。

    扩展子字段（`EXTRA_905_CODES`：`$Z` 著者号 / `$S` 四角号）：
      - 来源优先级：本次记录 → 旧记录（`extra_source`）→ **持久列 `records.marc_extras`**
        （2026-10-25 起；原先只有前两者，全部册注销后无处挂载即永久丢失）；
      - 本次记录带了 905 时**以它为准**（用户/导入明确删除的不复活）；整个没有 905
        时才回落到列 —— 这两种情况必须区分，否则「删掉 $Z」会被列里的旧值复活。
      - 结果同时写回 `orm.marc_extras`，与是否有在架册无关。
    """
    import json

    from ..core.marc import MarcWriteError, record_to_iso
    from .batch import active_holdings

    # ⚠️ **先 flush**：会话是 autoflush=False，`active_holdings()` 查的是**数据库**，
    # 看不到调用方刚在内存里改过的 h.status。以前“先 flush 再重建”靠 12 个调用点
    # 各自记得（规则 14），漏一个就静默分叉——`mark_lost` 正是漏的那个：把册置
    # LOST 后直接调本函数，905 里仍留着已遗失的册，直到下次任意馆藏变动才自愈
    # 。让“正确”成为默认，而不是纪律。
    db.flush()
    incoming_905 = bool(rec.get_fields("905"))
    extras = _marc_extra_subfields(rec)
    if extra_source is not None:
        extras.extend(pair for pair in _marc_extra_subfields(extra_source) if pair not in extras)
    if not extras and not incoming_905:
        # 本次记录里连 905 都没有（调用方只传了业务字段，或自上一条重建后 905 被清掉）
        # → 用持久列。⚠️ 若记录带了 905 却没有 $Z/$S，那是“明确没有”，不能回落。
        extras = [(c, v) for c, v in (getattr(orm, "marc_extras", None) or [])]
    rec.fields = [f for f in rec.fields if f.tag != "905"]
    class_no = (getattr(orm, "class_no", None) or "").strip() or None
    for i, h in enumerate(active_holdings(db, orm.id)):
        # 905$c/$v 只认 Item；record 不再持有卷册/排架。
        f = _mk905(h, getattr(h, "shelf_no", None),
                   getattr(h, "volume", None), class_no)
        if i == 0:
            for code, v in extras:
                f.add(code, v)
        rec.fields.append(f)
    orm.marc_extras = [[c, v] for c, v in extras] or None
    # ⚠️ **重建后按 tag 归位**（`record_to_iso` 也会排序，这里让 `fields` 与 `marc_raw` 顺序一致）。
    # 旧实现只 append —— 905 会被甩到 9XX（909/982/985）**之后**，把目次区顺序打乱；
    # 而 905 在标准里应排在 801 之后、909 之前。稳定排序，同 tag 的多册 905 顺序不变。
    rec.fields.sort(key=lambda x: x.tag)
    try:
        orm.marc_raw = record_to_iso(rec, encoding="gbk")
    except MarcWriteError:
        orm.marc_raw = record_to_iso(rec, encoding="utf-8")
    orm.fields = json.loads(rec.to_json())


def _marc_extra_subfields(rec) -> list[tuple[str, str]]:
    """记录 905 里**我方登记的扩展子字段**（保序、去重）。"""
    out: list[tuple[str, str]] = []
    for f in rec.get_fields("905"):
        for code in EXTRA_905_CODES:
            for v in f.all(code):
                v = (v or "").strip()
                if v and (code, v) not in out:
                    out.append((code, v))
    return out


def _mk905(h, shelf_no: str | None = None, volume: str | None = None,
           class_no: str | None = None):
    """905 馆藏字段，**按 GB/T 33286 的标准子字段布局**（2026-10-09 改正 / 补齐）：

        $a 馆藏地点（标准是"收藏单位代码或名称"，我方取馆藏地点）
        $b 条码（= 登录号 / 财产登记号）
        $c 排架号（标准"排架区分号"）
        $d 分类号
        $e 书次号（标准原文叫「种次号」）
        $s 索书号
        $v 卷册号（标准"入藏卷期"）

    ⚠️ **旧布局是 `$b` 索书号 / `$h` 条码** —— 两处都与标准相反（标准 `$b` 是**登录号**、
    标准 905 根本没有 `$h`）。现在按标准输出：索书号 `$b`→`$s`、条码 `$h`→`$b`，
    去掉非标准的 `$h`。**不要再改回去。**

    **`$d`/`$e` 从 `h.call_no` 拆分**（`core.callno.parse_call_no`），
    保证 `$d`/`$e`/`$s` 三者自洽；没有索书号时 `$d` 退回记录的分类号。

    **`$f`（复本数）不写入**：本系统每册一个 905，`$f` 恒为 1，没有信息量。
    标准另有的 `$y` 年代范围 / `$z` 复本号无对应数据。

    **`$c` / `$v` 是我方扩展**：历史数据的 905 只有 `$a$b$h`，
    排架号与卷册号只存在数据库列与索书号里，不进 MARC，因此不要把 `$c`/`$v`
    当成已对齐其他系统。

    `shelf_no` / `volume` / `class_no` 是**种级**值（来自 records），而 905 按册生成，
    故由调用方传入。只有值非空时才写对应子字段。
    """
    from ..core.callno import parse_call_no
    from ..core.marc import Field
    f = Field("905", " ", " ")
    if h.location:
        f.add("a", h.location)
    if h.barcode:
        f.add("b", h.barcode)
    if (shelf_no or "").strip():
        f.add("c", (shelf_no or "").strip())
    # $d/$e 从**索书号**里拆 —— 这样 $d/$e/$s 三者必然自洽（`$s` 由 `$d/$e/$c` 组成）
    cls, book, _vol = parse_call_no(h.call_no or "")
    d = cls or (class_no or "").strip()
    if d:
        f.add("d", d)
    if book:
        f.add("e", book)
    if h.call_no:
        f.add("s", h.call_no)
    if (volume or "").strip():
        f.add("v", (volume or "").strip())
    return f

