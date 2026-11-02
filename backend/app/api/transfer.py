# -*- coding: utf-8 -*-
"""图书转库：把书目记录连同馆藏从一个图书库搬到另一个图书库。

## 为什么是「预览 + 执行」两阶段，且必须共用同一份判定

转库是**不可逆**的物理搬书动作，还会改条码与索书号。按下之前用户必须看到
「会动多少册、哪些动不了」；而如果预览给出的数字与执行时的判定不是同一份代码
产出的，就会出现「预览 3 册、实际动了 5 册」——与「按筛选批量删除」是同一条
铁律（`_record_query` 唯一真源），只是换了个操作。

所以 `prepare()` 是唯一真源：预览直接序列化它的结果；执行则在**同一事务里重跑
一遍** prepare 再照单执行（不复用前端回传的计划——那是可以伪造的）。

## 五条硬规则

1. **外借中的册不许转**：按 ACTIVE Loan 判定；书不在架上，转过去就是账实不符。
2. **有冲突就整批拒绝**（409）：宁可让用户改选项重来，也不要"静默少转几册"。
3. **并入目标库已有同种时，索书号跟目标库走**：同种书共用索书号（README 规则 5），
   把源库的号带过去等于一个种两种号，上架必乱。
4. **转空的源记录要删掉**：图书库以条码为核心（README 规则 5 / 实现说明），
   留在源库的无条码记录既上不了架也进不了账。
5. **台账每册一行**：重新编号后原条码就消失了，没有台账就回答不了
   「这批书原来是什么号」——财产登记账这类上级检查要用的账目恰恰要能回答。
"""
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.callno import parse_call_no
from ..core.operator import operator_of
from ..core.volume import join_call_no
from ..db import get_db, like_escape
from ..models import (INACTIVE_STATUSES, STATUS_AVAILABLE, BookLibrary,
                      CallNoCounter, HoldingORM, LibraryTransfer, RecordORM)
from ..services.batch import (_barcode_digits, _raise_seq, barcode_key,
                              bump_counter)
from ..services.circulation import active_loan_holding_ids
from ..services import record_projection, reservations as reservation_service
from ..core.route_meta import api
from .records import RecordFilterIn, _record_query

router = APIRouter(prefix="/transfers", tags=["transfers"])

# 条码处理：保留原号（撞号即拒绝）/ 按目标库流水重新编号
BARCODE_MODES = ("keep", "renumber")
# 索书号处理：保留原号（撞号即拒绝）/ 按目标库书次号重新取号
CALLNO_MODES = ("keep", "renumber")

# 单次转库上限。转库要逐册改号、重建 905，一次几千册已经很慢；
# 真要搬整库就分批，也便于分批核对。
MAX_TRANSFER_RECORDS = 5000

# 一次操作一个批号（与注销号同构），供台账把同批次的册串起来
_TRANSFER_KEY_SUFFIX = "__transfer__"


def transfer_key(library_id: int) -> str:
    return f"lib{library_id}|{_TRANSFER_KEY_SUFFIX}"


class TransferIn(BaseModel):
    """转库请求。两种选书方式二选一：`ids`（勾选）或 `filter`（按筛选整批）。"""
    from_library_id: int
    to_library_id: int
    ids: list[int] = []
    filter: RecordFilterIn | None = None
    # 前端把「页面上显示的条数」回传；与后端实际匹配数不一致 → 拒绝
    expected_total: int | None = None
    barcode_mode: str = "keep"
    callno_mode: str = "keep"
    # 已注销 / 已遗失的册不在架上，默认不带走；勾上则连同留账一起搬
    include_inactive: bool = False
    # 排架号是各馆自己的架位体系，搬过去通常要重新上架 → 默认清空
    keep_shelf_no: bool = False


# ---------------------------------------------------------------------------
#  取号预演（纯函数，预览与执行共用）
# ---------------------------------------------------------------------------

def _holdings_of(db: Session, record_id: int) -> list[HoldingORM]:
    """一条记录的全部馆藏（按条码排）。模型里没有 ORM 关系，显式查。"""
    return (db.query(HoldingORM).filter(HoldingORM.record_id == record_id)
            .order_by(HoldingORM.barcode, HoldingORM.id).all())


def _target_barcode_set(db: Session, lib_id: int) -> set[str]:
    return {b for (b,) in db.query(HoldingORM.barcode)
            .filter(HoldingORM.library_id == lib_id,
                    HoldingORM.barcode.isnot(None)).all()}


def _plan_barcodes(db: Session, lib_id: int, count: int) -> tuple[list[str], int]:
    """按目标库流水**预演** count 个条码（不消耗号段），返回 (条码列表, 最终序号)。

    为什么要「预演」而不是直接 `bump_counter`：预览阶段绝不能动计数器 ——
    用户只是看一眼就把一段号消耗掉是灾难。执行阶段用同一函数再算一遍，最后
    `_raise_seq` 把计数器抬到最终序号；两次之间若别人取过号，序号更高，
    预演结果自动跟着走，不会撞。
    """
    digits = _barcode_digits(db)
    counter = db.get(CallNoCounter, barcode_key(lib_id))
    seq = counter.current_seq if counter else 0
    used = _target_barcode_set(db, lib_id)
    out: list[str] = []
    while len(out) < count:
        seq += 1
        code = f"{seq:0{digits}d}"
        if code not in used:
            used.add(code)
            out.append(code)
    return out, seq


def _target_book_no_set(db: Session, lib_id: int, class_no: str) -> set[str]:
    """目标库该分类号下已在用的书次号（用于避免撞索书号）。"""
    rows = (db.query(HoldingORM.call_no)
            .join(RecordORM, RecordORM.id == HoldingORM.record_id)
            .filter(HoldingORM.library_id == lib_id,
                    RecordORM.class_no == class_no,
                    HoldingORM.call_no.isnot(None)).all())
    out: set[str] = set()
    for (call_no,) in rows:
        _cls, book, _vol = parse_call_no(call_no)
        if book:
            out.add(book)
    return out


def _plan_book_nos(db: Session, lib_id: int, class_no: str,
                   count: int) -> tuple[list[str], int]:
    """按目标库书次号计数器**预演** count 个书次号（不消耗号段）。"""
    key = f"lib{lib_id}|{class_no}"
    counter = db.get(CallNoCounter, key)
    seq = counter.current_seq if counter else 0
    used = _target_book_no_set(db, lib_id, class_no)
    out: list[str] = []
    while len(out) < count:
        seq += 1
        code = str(seq)
        if code not in used:
            used.add(code)
            out.append(code)
    return out, seq


def _target_callno_map(db: Session, lib_id: int) -> dict[str, int]:
    """目标库「索书号 → 占用它的记录 id」，用于 keep 模式的撞号判定。"""
    rows = (db.query(HoldingORM.call_no, HoldingORM.record_id)
            .filter(HoldingORM.library_id == lib_id,
                    HoldingORM.call_no.isnot(None)).all())
    out: dict[str, int] = {}
    for call_no, rid in rows:
        out.setdefault(call_no, rid)
    return out


# ---------------------------------------------------------------------------
#  prepare：唯一真源
# ---------------------------------------------------------------------------

def _match_records(db: Session, payload: TransferIn) -> tuple[list[RecordORM], list[int]]:
    """选出要转的记录。库号**强制**用 `from_library_id`（不信任 filter 里的值）。"""
    if payload.filter is not None:
        if payload.expected_total is None:
            raise HTTPException(400, "按筛选转库必须回传 expected_total（页面上显示的条数）")
        cond = payload.filter.model_dump()
        cond["library_id"] = payload.from_library_id
        query = _record_query(db, **cond)
        matched = query.count()
        if matched != payload.expected_total:
            raise HTTPException(409, (
                f"筛选结果与预期不一致（页面显示 {payload.expected_total} 条，"
                f"当前实际 {matched} 条）。数据已发生变化，请刷新后重试。"))
        if matched > MAX_TRANSFER_RECORDS:
            raise HTTPException(400, f"单次最多转库 {MAX_TRANSFER_RECORDS} 条，请缩小筛选范围后分批转库")
        return query.order_by(RecordORM.id).all(), []

    ids = payload.ids
    if not ids:
        raise HTTPException(400, "ids 不能为空")
    rows = (db.query(RecordORM)
            .filter(RecordORM.id.in_(ids),
                    RecordORM.library_id == payload.from_library_id)
            .order_by(RecordORM.id).all())
    missing = sorted(set(ids) - {r.id for r in rows})
    return rows, missing


def _movable(holdings: list[HoldingORM], include_inactive: bool,
             on_loan_ids: set[int] | None = None
             ) -> tuple[list[HoldingORM], list[HoldingORM]]:
    """可转的册 / 被拦下的册。已注销与已遗失默认不带走（不在架上）。

    外借中的册按 `loans` 的 ACTIVE 行判定；ON_LOAN 状态已退休。
    """
    on_loan_ids = on_loan_ids or set()
    move, blocked = [], []
    for h in holdings:
        if h.id in on_loan_ids:
            blocked.append(h)
        elif h.status == STATUS_AVAILABLE or (include_inactive and h.status in INACTIVE_STATUSES):
            move.append(h)
    return move, blocked


def _find_target_record(db: Session, orm: RecordORM, to_lib_id: int) -> RecordORM | None:
    """目标库里同 ISBN + 同卷册号的记录 → 复用（同种书合并到一条，不重复建种）。

    没有 ISBN 时**不猜**（题名模糊匹配会把不同版本并到一条），一律新建。
    """
    if not orm.isbn_norm:
        return None
    # Phase 3：优先用 200$h/$i 的 volume_key；旧数据没有 key 时回退 records.volume。
    want_key = orm.volume_key
    for cand in (db.query(RecordORM)
                 .filter(RecordORM.library_id == to_lib_id,
                         RecordORM.isbn_norm == orm.isbn_norm).all()):
        if cand.volume_key == want_key:
            return cand
    return None


def _skip_reason(blocked: list[HoldingORM], total: int,
                 on_loan_ids: set[int] | None = None) -> str:
    if total == 0:
        return "该记录没有馆藏"
    on_loan_ids = on_loan_ids or set()
    if blocked and all(h.id in on_loan_ids for h in blocked):
        return "全部册外借中，不能转库"
    return "没有可转的册（只剩已注销 / 已遗失，勾上「连同已注销的册」才会带走）"


def prepare(db: Session, payload: TransferIn) -> dict:
    """算出「要转哪些册、各换成什么号、哪些动不了」。**不写库**。"""
    if payload.barcode_mode not in BARCODE_MODES:
        raise HTTPException(400, f"barcode_mode 只能是 {' / '.join(BARCODE_MODES)}")
    if payload.callno_mode not in CALLNO_MODES:
        raise HTTPException(400, f"callno_mode 只能是 {' / '.join(CALLNO_MODES)}")
    src = db.get(BookLibrary, payload.from_library_id)
    if src is None:
        raise HTTPException(404, f"源图书库不存在：{payload.from_library_id}")
    dst = db.get(BookLibrary, payload.to_library_id)
    if dst is None:
        raise HTTPException(404, f"目标图书库不存在：{payload.to_library_id}")
    if src.id == dst.id:
        raise HTTPException(400, "源图书库与目标图书库相同，无需转库")

    records, missing = _match_records(db, payload)

    # 第一遍：定下每册的归属与目标记录（纯读）。
    # 分成两遍而不是边算边查：renumber 时"先算的册会占掉后算的册的号"，
    # 必须先把需求数汇总，再一次性预演整批号段。
    staged: list[dict] = []
    renumber_barcode_total = 0
    renumber_callno: dict[str, int] = {}

    for orm in records:
        holdings = _holdings_of(db, orm.id)
        on_loan = active_loan_holding_ids(db, [h.id for h in holdings])
        move, blocked = _movable(holdings, payload.include_inactive, on_loan)
        if not move:
            staged.append({"orm": orm, "holdings": holdings, "move": [],
                           "blocked": blocked, "target": None,
                           "skip": _skip_reason(blocked, len(holdings), on_loan)})
            continue
        target = _find_target_record(db, orm, dst.id)
        if payload.barcode_mode == "renumber":
            renumber_barcode_total += len(move)
        if payload.callno_mode == "renumber" and target is None:
            class_no = (orm.class_no or "").strip()
            renumber_callno[class_no] = renumber_callno.get(class_no, 0) + 1
        staged.append({"orm": orm, "holdings": holdings, "move": move,
                       "blocked": blocked, "target": target, "skip": None})

    barcode_pool: list[str] = []
    barcode_final_seq = 0
    if payload.barcode_mode == "renumber" and renumber_barcode_total:
        barcode_pool, barcode_final_seq = _plan_barcodes(db, dst.id, renumber_barcode_total)
    book_pool: dict[str, list[str]] = {}
    book_final_seq: dict[str, int] = {}
    if payload.callno_mode == "renumber":
        for class_no, n in renumber_callno.items():
            if n:
                codes, seq = _plan_book_nos(db, dst.id, class_no, n)
                book_pool[class_no] = codes
                book_final_seq[class_no] = seq

    used_barcodes = _target_barcode_set(db, dst.id)
    used_callnos = _target_callno_map(db, dst.id)
    plans: list[dict] = []
    conflicts: list[dict] = []
    skipped: list[dict] = []
    copies_total = 0
    bar_i = 0

    for st in staged:
        orm: RecordORM = st["orm"]
        if st["skip"]:
            skipped.append({"record_id": orm.id, "title": orm.title_norm,
                            "reason": st["skip"]})
            continue
        move: list[HoldingORM] = st["move"]
        target: RecordORM | None = st["target"]
        class_no = (orm.class_no or "").strip()
        volume = next((h.volume for h in move if h.volume), None)

        # 索书号：
        #   并入已有同种 → 跟目标库那条走（同种共号，README 规则 5）；
        #   新建记录     → keep 沿用源号 / renumber 从预演池取。
        shared_call_no: str | None = None
        if target is not None:
            tgt_holdings = _holdings_of(db, target.id)
            shared_call_no = next((h.call_no for h in tgt_holdings if h.call_no), None)
            if shared_call_no is None and payload.callno_mode == "renumber":
                pool = book_pool.get(class_no) or []
                book = pool.pop(0) if pool else None
                if book:
                    shared_call_no = join_call_no(f"{class_no}/{book}", volume)
        elif payload.callno_mode == "renumber":
            pool = book_pool.get(class_no) or []
            book = pool.pop(0) if pool else None
            shared_call_no = join_call_no(f"{class_no}/{book}", volume) if book else None

        # 索书号撞号**按条判一次**，不是按册判：同种的所有复本本来就是一个号
        # （README 规则 5），按册判会把"自己刚登记的号"当成冲突（实测：一本 2 册的
        # 书直接被自己挡住，409）。所以先判、再落，落的时候也只落一次。
        if (payload.callno_mode == "keep" and target is None
                and shared_call_no and shared_call_no in used_callnos):
            conflicts.append({"kind": "call_no", "value": shared_call_no,
                              "record_id": orm.id, "title": orm.title_norm,
                              "holding_id": move[0].id})

        copies = []
        for h in move:
            call_no = shared_call_no or h.call_no
            if payload.barcode_mode == "renumber":
                code = barcode_pool[bar_i] if bar_i < len(barcode_pool) else None
                bar_i += 1
            else:
                code = h.barcode
            # 撞号判定只在 keep 模式有意义（renumber 的号是本函数刚挑的空号）
            if payload.barcode_mode == "keep" and code and code in used_barcodes:
                conflicts.append({"kind": "barcode", "value": code,
                                  "record_id": orm.id, "title": orm.title_norm,
                                  "holding_id": h.id})
            copies.append({"holding_id": h.id, "old_barcode": h.barcode,
                           "new_barcode": code, "old_call_no": h.call_no,
                           "new_call_no": call_no, "status": h.status})
            if code:
                used_barcodes.add(code)
        if shared_call_no:
            used_callnos.setdefault(shared_call_no, orm.id)

        copies_total += len(copies)
        plans.append({
            "record_id": orm.id, "title": orm.title_norm, "isbn": orm.isbn_norm,
            "target": {"mode": "reuse" if target else "new",
                       "record_id": target.id if target else None},
            "copies": copies,
            "blocked": [{"holding_id": h.id, "barcode": h.barcode, "status": h.status}
                        for h in st["blocked"]],
            # 全部馆藏都转走 → 源记录删掉（不留无条码记录）
            "drop_source": len(move) == len(st["holdings"]),
        })

    return {
        "from_library": {"id": src.id, "name": src.name},
        "to_library": {"id": dst.id, "name": dst.name},
        "barcode_mode": payload.barcode_mode,
        "callno_mode": payload.callno_mode,
        "records": len(plans),
        "copies": copies_total,
        "plans": plans,
        "conflicts": conflicts,
        "skipped": skipped,
        "missing": missing,
        # 执行阶段据此抬计数器（预览阶段不落库）
        "_barcode_final_seq": barcode_final_seq,
        "_book_final_seq": book_final_seq,
    }


# ---------------------------------------------------------------------------
#  执行
# ---------------------------------------------------------------------------

def _new_record_from(db: Session, orm: RecordORM, to_lib_id: int) -> RecordORM:
    """在目标库复制出一条书目记录（书目数据逐字相同，馆藏随后重建）。"""
    dst = RecordORM(
        library_id=to_lib_id,
        marc_raw=orm.marc_raw,
        leader=orm.leader,
        fields=[dict(f) for f in (orm.fields or [])],
        source=orm.source,
        isbn_norm=orm.isbn_norm,
        title_norm=orm.title_norm,
        title_pinyin=orm.title_pinyin,
        title_pinyin_initials=orm.title_pinyin_initials,
        author_norm=orm.author_norm,
        year=orm.year,
        class_no=orm.class_no,
        item_type=orm.item_type,
        created_by=orm.created_by,
        # 卷册/排架归 Item，随册移动；书目只复制卷册键与排序号。
        volume_seq=orm.volume_seq,
        volume_key=orm.volume_key,
        # 905 是按馆藏重建的，而源记录整条转走时可能一本在架册都没有（全在外借、
        # 或被 drop_source 删掉）——重建不出 905，挂在持久列上的扩展子字段
        # （905$Z 著者号 / 905$S 四角号）就会永久消失。它们是**他人编目系统的约定**，
        # 丢了没法从别处恢复，所以必须显式跟着走。
        marc_extras=orm.marc_extras,
    )
    db.add(dst)
    db.flush()      # 规则 14：autoflush=False，不 flush 拿不到 id
    return dst


def apply_transfer(db: Session, payload: TransferIn, operator: str | None) -> dict:
    """按 prepare 的结果执行转库。**整批一个事务**，出错整体回滚。"""
    # 同一事务里重算：不信任前端回传的计划，也不让"预览之后数据变了"悄悄生效
    plan = prepare(db, payload)
    record_ids = {item["record_id"] for item in plan["plans"]}
    record_ids.update(item["target"]["record_id"] for item in plan["plans"] if item["target"]["record_id"])
    for record_id in sorted(record_ids):
        db.get(RecordORM, record_id, with_for_update=True, populate_existing=True)
    holding_ids = [copy["holding_id"] for item in plan["plans"] for copy in item["copies"]]
    if holding_ids:
        (db.query(HoldingORM).filter(HoldingORM.id.in_(holding_ids)).order_by(HoldingORM.id)
         .populate_existing().with_for_update().all())
    plan = prepare(db, payload)
    current_ids = {item["record_id"] for item in plan["plans"]}
    current_ids.update(item["target"]["record_id"] for item in plan["plans"] if item["target"]["record_id"])
    if not current_ids.issubset(record_ids):
        raise HTTPException(409, "转库目标书目已变更，请刷新预览后重试")
    if plan["conflicts"]:
        sample = "、".join(str(c["value"]) for c in plan["conflicts"][:10])
        more = "…" if len(plan["conflicts"]) > 10 else ""
        raise HTTPException(409, (
            f"有 {len(plan['conflicts'])} 处编号冲突，已整批取消：{sample}{more}。"
            f"请改用「重新编号」，或先在目标库处理这些编号。"))
    if not plan["plans"]:
        raise HTTPException(400, "没有可转的册")

    today = datetime.now().strftime("%Y%m%d")
    batch_no = f"{today}-{bump_counter(db, transfer_key(payload.to_library_id)):03d}"

    moved_copies = 0
    moved_records = 0
    created_records = 0
    deleted_source = 0
    ledger: list[LibraryTransfer] = []

    for item in plan["plans"]:
        src_orm = db.get(RecordORM, item["record_id"])
        if src_orm is None:
            continue
        target = None
        if item["target"]["mode"] == "reuse" and item["target"]["record_id"]:
            target = db.get(RecordORM, item["target"]["record_id"])
        if target is None:
            target = _new_record_from(db, src_orm, payload.to_library_id)
            created_records += 1

        for cp in item["copies"]:
            h = db.get(HoldingORM, cp["holding_id"])
            if h is None:
                continue
            h.record_id = target.id
            h.library_id = payload.to_library_id
            h.barcode = cp["new_barcode"]
            # 登录号默认等于条码；转库重新编号时同步，独立登录号保留。
            if h.accession_no and h.accession_no == cp["old_barcode"]:
                h.accession_no = cp["new_barcode"]
            h.call_no = cp["new_call_no"]
            ledger.append(LibraryTransfer(
                batch_no=batch_no,
                from_library_id=payload.from_library_id,
                to_library_id=payload.to_library_id,
                record_id=src_orm.id, to_record_id=target.id, holding_id=h.id,
                old_barcode=cp["old_barcode"], new_barcode=cp["new_barcode"],
                old_call_no=cp["old_call_no"], new_call_no=cp["new_call_no"],
                title=src_orm.title_norm, operator=operator))
            moved_copies += 1
        db.flush()      # 规则 14：随后 remove_holdings_sync 要读到新挂靠的册

        # 新到的册可能让目标库的预约队列前进（并入同种记录时尤其明显）。
        reservation_service.promote_ready(db, target.id, operator=operator)

        # 905 按各自库里的在架馆藏重建：目标库新收的册、源库剩下的册
        record_projection.rebuild_905(db, target.id)
        moved_records += 1
        if item["drop_source"]:
            # 源记录删除会 ondelete=CASCADE 掉预约：先把进行中预约取消并改挂到
            # 接手的记录，保留「谁被取消、为什么」的审计行。
            reservation_service.cancel_active_for_record(
                db, src_orm.id, "馆藏已转出", operator=operator,
                reassign_record_id=target.id)
            db.delete(src_orm)
            deleted_source += 1
        else:
            record_projection.rebuild_905(db, src_orm.id)
            # 源记录还留着（典型：仍有在借册）：按剩余在架册重算队列，不能卡死。
            reservation_service.cancel_if_unavailable(
                db, [src_orm.id], "馆藏已转出", operator=operator)
            reservation_service.promote_ready(db, src_orm.id, operator=operator)
    db.flush()

    # 计数器抬到本批用到的最大号。keep 模式同样要抬：否则下一本自动取号
    # 会撞上刚搬进来的号（搬进来的号可能比目标库现有流水更大）。
    if payload.barcode_mode == "renumber" and plan["_barcode_final_seq"]:
        _raise_seq(db, barcode_key(payload.to_library_id), plan["_barcode_final_seq"])
    else:
        _raise_to_max_barcode(db, payload.to_library_id)
    if payload.callno_mode == "renumber":
        for class_no, seq in (plan["_book_final_seq"] or {}).items():
            if seq:
                _raise_seq(db, f"lib{payload.to_library_id}|{class_no}", seq)
    else:
        _raise_to_max_book_no(db, payload.to_library_id, plan)

    db.add_all(ledger)
    db.commit()
    return {
        "batch_no": batch_no,
        "moved_copies": moved_copies,
        "moved_records": moved_records,
        "created_records": created_records,
        "deleted_source_records": deleted_source,
        "skipped": plan["skipped"],
        "missing": plan["missing"],
        "conflicts": 0,
    }


def _raise_to_max_barcode(db: Session, lib_id: int) -> None:
    """把目标库条码流水抬过「现有最大数字尾号」。"""
    best = 0
    for (bc,) in (db.query(HoldingORM.barcode)
                  .filter(HoldingORM.library_id == lib_id,
                          HoldingORM.barcode.isnot(None)).all()):
        m = re.search(r"(\d+)$", bc or "")
        if m:
            best = max(best, int(m.group(1)))
    if best:
        _raise_seq(db, barcode_key(lib_id), best)


def _raise_to_max_book_no(db: Session, lib_id: int, plan: dict) -> None:
    """keep 模式：把搬进来的书次号抬进目标库计数器（按分类号分组）。"""
    best: dict[str, int] = {}
    for item in plan["plans"]:
        for cp in item["copies"]:
            cls, book, _vol = parse_call_no(cp["new_call_no"] or "")
            if cls and book.isdigit():
                best[cls] = max(best.get(cls, 0), int(book))
    for cls, seq in best.items():
        _raise_seq(db, f"lib{lib_id}|{cls}", seq)


# ---------------------------------------------------------------------------
#  接口
# ---------------------------------------------------------------------------

@router.post("/preview")
@api(module="records", level="read")
def transfer_preview(payload: TransferIn, db: Session = Depends(get_db)):
    """转库预览（只读）：列出将转的册、换号情况、冲突与被拦下的册。"""
    return prepare(db, payload)


@router.post("/apply")
@api(module="records")
def transfer_apply(payload: TransferIn, request: Request,
                   db: Session = Depends(get_db)):
    """执行转库。整批一个事务；有冲突则整批拒绝，不做部分迁移。"""
    return apply_transfer(db, payload, operator_of(request))


@router.get("")
@api(module="records")
def list_transfers(from_library_id: int | None = None,
                   to_library_id: int | None = None,
                   batch_no: str | None = None,
                   q: str | None = None,
                   limit: int = Query(50, ge=1, le=500),
                   offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db)):
    """转库台账：按册记录「从哪来、到哪去、原来什么号」。

    `q` 匹配题名或新旧条码片段 —— 追一本书的下落是最常见的用法。
    """
    query = db.query(LibraryTransfer)
    if from_library_id:
        query = query.filter(LibraryTransfer.from_library_id == from_library_id)
    if to_library_id:
        query = query.filter(LibraryTransfer.to_library_id == to_library_id)
    if batch_no:
        query = query.filter(LibraryTransfer.batch_no == batch_no)
    if q:
        pat = f"%{like_escape(q.strip())}%"
        query = query.filter(LibraryTransfer.title.ilike(pat, escape="\\")
                             | LibraryTransfer.old_barcode.ilike(pat, escape="\\")
                             | LibraryTransfer.new_barcode.ilike(pat, escape="\\"))
    total = query.count()
    names = {l.id: l.name for l in db.query(BookLibrary).all()}
    rows = (query.order_by(LibraryTransfer.id.desc())
            .offset(offset).limit(limit).all())
    return {
        "total": total, "limit": limit, "offset": offset,
        "items": [{
            "id": t.id, "batch_no": t.batch_no,
            "from_library": names.get(t.from_library_id),
            "to_library": names.get(t.to_library_id),
            "from_library_id": t.from_library_id, "to_library_id": t.to_library_id,
            "record_id": t.record_id, "to_record_id": t.to_record_id,
            "holding_id": t.holding_id,
            "old_barcode": t.old_barcode, "new_barcode": t.new_barcode,
            "old_call_no": t.old_call_no, "new_call_no": t.new_call_no,
            "title": t.title, "operator": t.operator,
            "created_at": str(t.created_at) if t.created_at else None,
        } for t in rows],
    }
