# -*- coding: utf-8 -*-
"""批量处理服务：共享导入管线（同步/后台任务复用）与馆藏生成。"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ..core import clean as cl
from ..core import isbn as _  # noqa: F401  (保持 core.isbn 引用直观)
from ..core import oplog
from ..core.clc import shorten_record_class_nos
from ..core.marc import MarcWriteError, Record
from ..core.validators import LEVEL_ERROR, validate
from ..core.volume import (VOLUME_SEP, call_no_base, derive_volume_seq, join_call_no)
from ..io.iso import read_iso
from ..models import CallNoCounter, HoldingORM, RecordORM
from .settings import class_no_rule

log = logging.getLogger(__name__)

BARCODE_KEY_SUFFIX = "__barcode__"
_DEFAULT_BARCODE_DIGITS = 7


def barcode_key(library_id: int) -> str:
    """条码流水号的计数键——按图书库独立（与书次号一致）。

    多校管理下各校是独立实体，条码各自从 1 起编；此前用全局键 "__barcode__"，
    新建的第二书库会接着第一书库的号往下走。
    """
    return f"lib{library_id}|{BARCODE_KEY_SUFFIX}"


WITHDRAW_KEY_SUFFIX = "__withdraw__"


def withdraw_key(library_id: int) -> str:
    """注销号流水键（按图书库独立）——复用书次号计数表，避免再建一张表。"""
    return f"lib{library_id}|{WITHDRAW_KEY_SUFFIX}"


def active_holdings(db: Session, record_id: int) -> list[HoldingORM]:
    """在架馆藏（905 与统计都只认在架册）。"""
    from ..models import STATUS_AVAILABLE
    return (db.query(HoldingORM)
            .filter(HoldingORM.record_id == record_id,
                    HoldingORM.status == STATUS_AVAILABLE)
            .order_by(HoldingORM.id).all())


def next_withdraw_no(db: Session, library_id: int) -> str:
    """生成注销号：ZX{图书库}-{5 位流水}，按库独立。"""
    return f"ZX{library_id}-{bump_counter(db, withdraw_key(library_id)):05d}"


def raise_withdraw_seq(db: Session, library_id: int, seq: int) -> None:
    """手工指定注销号（ZX{库}-{n}）后把该库注销号计数器抬到至少 n。

    与 set_book_no 抬书次号同理：不抬的话下次自动注销号会撞上刚手工指定的号。
    """
    _raise_seq(db, withdraw_key(library_id), seq)


def barcode_digits_list(db: Session) -> list[int]:
    """**允许的**条码位数列表（全局设置 `barcode.digits`），升序去重。

    兼容两种存法：新的列表 `{"digits": [7, 8]}` 与旧的单值 `{"digits": 8}`。
    多值是真实需求——旧库常有 7 位历史号与 8 位新号并存。
    脏值（布尔 / 非整数 / 越界 4~16）逐项丢弃；全丢光则回退默认 `[7]`。
    **绝不抛异常**（与 `services/settings.py` 的容错读法一致）。
    """
    try:
        from ..models import Setting
        row = db.get(Setting, "barcode")
        if row and row.value:
            raw = row.value.get("digits")
            vals = raw if isinstance(raw, (list, tuple, set)) else [raw]
            out: set[int] = set()
            for v in vals:
                if isinstance(v, bool):        # True/False 是 int 子类，显式排除
                    continue
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    continue
                if 4 <= n <= 16:
                    out.add(n)
            if out:
                return sorted(out)
    except Exception:
        pass
    return [_DEFAULT_BARCODE_DIGITS]


def _barcode_digits(db: Session) -> int:
    """自动流水生成条码时用的宽度 = 允许位数里**最宽**的那个。

    取最宽而不是最窄：多个位数通常意味着"历史留下了窄号、新号是宽号"
    （如 7 位旧号 + 8 位新号）。若按最窄生成，新号会**比现有新号还短**，
    等于把正在使用的号段悄悄换窄。`barcode_digits_list` 已升序，取 [-1] 即可。
    """
    return barcode_digits_list(db)[-1]


def barcode_width(code: str) -> int:
    """条码的「位数」= 末尾连续数字的长度（前缀不计，与录入时的补零口径一致）。

    `ABC00000123` → 8；`0011971` → 7；不含数字 → 0。
    """
    m = re.search(r"(\d+)$", (code or "").strip())
    return len(m.group(1)) if m else 0


def check_barcode_digits(db: Session, code: str) -> None:
    """条码位数必须是设置里允许的位数之一，否则 `ValueError`（API 层转 400）。

    这是**硬拦**而非提示：图书库以条码为核心，位数不齐会让流水号、
    书标与扫描枪识别互相打架。
    """
    allowed = barcode_digits_list(db)
    w = barcode_width(code)
    if w not in allowed:
        shown = " / ".join(str(a) for a in allowed)
        raise ValueError(
            f"条码 {code} 是 {w} 位，不在设置的条码位数（{shown} 位）内；"
            f"如确实要收这类条码，请先到「全局设置 → 条码位数」把它加进去")


# 原子取号：一条 SQL 完成「自增 + 读回」。ON CONFLICT 的 UPDATE 会锁住已有行直到
# 本事务提交，并发事务在这里排队串行领号 —— 不会重号。
# 旧实现（SELECT 读出 -> +=1 -> 写回 ORM）在两个编目端同时保存时会静默领到相同的
# 书次号 / 条码 / 注销号，而索书号没有唯一约束兜底，撞了也无从发现。
_BUMP_SQL = text(
    "INSERT INTO callno_counters (class_no, current_seq) VALUES (:key, 1) "
    "ON CONFLICT (class_no) DO UPDATE SET "
    "current_seq = callno_counters.current_seq + 1, updated_at = now() "
    "RETURNING current_seq"
)


def bump_counter(db: Session, key: str) -> int:
    """书次号 / 条码流水 / 注销号共用的原子自增取号（见 _BUMP_SQL 注释）。"""
    seq = int(db.execute(_BUMP_SQL, {"key": key}).scalar_one())
    _sync_counter_instance(db, key)
    return seq


# ---------------------------------------------------------------------------
#  批量导入的号段预分配
# ---------------------------------------------------------------------------
#
# ## 为什么需要另一条路径
#
# `bump_counter` 的 UPSERT 会锁住 `callno_counters` 的那一行，**直到本事务提交**。
# 导入是「每 500 条一批 commit」，于是一次导入会让同库其它终端在「取书次号 / 生成
# 条码 / 生成注销号」上排队，最多等一整批（实测几十秒，编目员体感是"保存没反应"）。
#
# ## 做法
#
# 导入期间按 `CALLNO_BLOCK` 个号为单位**一次性预留**，用独立连接 + 立即提交，
# 主事务只从内存里发号，不再碰计数器行。代价是**允许跳号**：导入中途失败时，
# 这一块里没用掉的号就空了。书次号不要求连续（撞号才是问题），这个代价可以接受。
#
# 非导入路径（单条新建、编目页保存）仍走 `bump_counter`：那里一次只取一个号，
# 事务很短，不需要为它引入跳号。

CALLNO_BLOCK = 50

#: 号段键 → [下一个可用号, 本段结束号]。挂在 `Session.info` 上，随会话生灭。
_BLOCK_KEY = "counter_blocks"


def _reserve_block(key: str, size: int) -> tuple[int, int]:
    """用**独立连接**预留 `size` 个号并立即提交，返回 `(起, 止)` 闭区间。

    单独开会话而不是复用当前会话：当前会话正处在导入的长事务里，在它上面
    commit 会把半批数据一起落库（正是 `batch_cataloging` 踩过的坑）。
    """
    from ..db import SessionLocal

    other = SessionLocal()
    try:
        # 一次 +size，返回自增后的值 = 本段最后一个号
        last = int(other.execute(
            text("INSERT INTO callno_counters (class_no, current_seq) VALUES (:key, :size) "
                 "ON CONFLICT (class_no) DO UPDATE SET "
                 "current_seq = callno_counters.current_seq + :size, updated_at = now() "
                 "RETURNING current_seq"),
            {"key": key, "size": size}).scalar_one())
        other.commit()
    finally:
        other.close()
    return last - size + 1, last


def bump_counter_block(db: Session, key: str, size: int = CALLNO_BLOCK) -> int:
    """从**预分配的号段**里取下一个号；段用尽时再预留一块。

    只在批量导入路径调用（见上面的说明）。号保证不重复、递增；允许因中途失败而跳号。
    """
    blocks = db.info.setdefault(_BLOCK_KEY, {})
    cur = blocks.get(key)
    if cur is None or cur[0] > cur[1]:
        start, end = _reserve_block(key, size)
        cur = [start, end]
        blocks[key] = cur
        # 预留走的是另一条连接，本会话 identity map 里的旧值要刷新（见 _sync_counter_instance）
        _sync_counter_instance(db, key)
    seq = cur[0]
    cur[0] += 1
    return seq


#: 批量导入模式开关。用 contextvar 而不是函数参数：取号发生在 `create_holdings` 深处
#: （书次号、条码流水两条路径），把 flag 一路传下去会污染所有调用方签名。
_BULK_IMPORT = ContextVar("bulk_import", default=False)


@contextmanager
def bulk_import_mode():
    """进入批量导入模式：这期间取号走号段预分配，不再逐条锁计数器行。

    退出时把本会话里没用完的号段丢掉（它们已经提交进库，所以是**跳号**，不是重复
    发号）。跳号可以接受：书次号要求唯一与递增，不要求连续。
    """
    token = _BULK_IMPORT.set(True)
    try:
        yield
    finally:
        _BULK_IMPORT.reset(token)


def _next_seq(db: Session, key: str) -> int:
    """取号入口：批量导入时走号段，其余场景逐条原子自增（事务短，不跳号）。"""
    if _BULK_IMPORT.get():
        return bump_counter_block(db, key)
    return bump_counter(db, key)


# 「抬到至少 target」：手动指定书次号时用，只升不降 —— 否则下次自动编号会从
# 旧值继续，撞上刚手工指定的号。
_RAISE_SEQ_SQL = text(
    "INSERT INTO callno_counters (class_no, current_seq) VALUES (:key, :target) "
    "ON CONFLICT (class_no) DO UPDATE SET "
    "current_seq = GREATEST(callno_counters.current_seq, :target), updated_at = now()"
)


def _raise_seq(db: Session, key: str, target: int) -> None:
    db.execute(_RAISE_SEQ_SQL, {"key": key, "target": target})
    _sync_counter_instance(db, key)


def _sync_counter_instance(db: Session, key: str) -> None:
    """原生 SQL 绕过了 ORM：本会话先前若已加载过该计数行（identity map 里的旧值），
    强制刷新，避免后续 current()（_BoundCounter/_DBCounter）读到陈旧序号。"""
    db.get(CallNoCounter, key, populate_existing=True)


def _collect_barcodes(rec: Record, tag: str, codes: tuple[str, ...]) -> list[str]:
    """某个 tag 下这些子字段的全部非空值（去重保序）。"""
    out: list[str] = []
    for f in rec.get_fields(tag):
        for code in codes:
            for v in f.all(code):
                v = (v or "").strip()
                if v and v not in out:
                    out.append(v)
    return out


def _record_copy_volumes(rec: Record) -> list[str | None]:
    """**每一册**的卷册号，按 905 字段顺序——与 `_record_barcodes` 一一对位。

    背景：导入原先只取 `first_value("905","v")`，
    一条书目下 3 册各带不同 `$v` 时会被"拍平"成第一册的值。多卷书（一条书目挂
    上册/中册/下册三册）正好是这种形状，卷册号丢了就分不清哪册是哪卷。

    卷册号两级来源：
      1. `905$v`（标准字段）；
      2. `$v` 空时从 `$s` 的卷册段回填（`I247.5/8:上册` 的 `上册`）——书商数据
         常把卷册号内嵌在索书号里而不单列 `$v`（原实现解析后就把第三段扔了）。
    没有条码的 905 不对应任何册，跳过（与 `_record_barcodes` 同口径）。
    """
    from ..core.callno import parse_call_no
    out: list[str | None] = []
    for f in rec.get_fields("905"):
        bc = (f.get("h") or f.get("b") or "").strip()
        if not bc:
            continue
        vol = (f.get("v") or "").strip()
        if not vol:
            _cls, _book, vol_from_s = parse_call_no((f.get("s") or "").strip())
            vol = (vol_from_s or "").strip()
        out.append(vol or None)
    return out


def _record_barcodes(rec: Record) -> list[str]:
    """记录里自带的**全部**条码（`905` 优先；没有才看 `995`）。

    905 的条码子字段**两套布局都要认**（2026-10-09 起我方按标准把条码放 `$b`）：
    - 新布局：条码在 `$b`（= 登录号）；
    - 旧布局：条码在 `$h`（标准里没有 `$h` ⇒ **见到 `$h` 即判定为旧布局**）。
    故**先试 `$h`、没有再试 `$b`** —— 顺序不能反：旧布局的 `$b` 是**索书号**。

    为什么是"全部"而不是第一个：书商数据常把**每册一条**的字段（909 / 985 / 995）
    放在记录里，一条书目带 3 个条码就是 3 册。只取第一个会**静默丢掉其余 2 册**
    （用户 2026-10-07 给的真实数据正是每条 3 册）。

    ⚠️ **逐字段回退**（2026-10-25 修正）：旧实现是「字段集合级二选一」（只要有
    一个 `$h` 就把所有 905 都按 `$h` 读），混合布局的文件里新布局的 `$b` 会被
    整批漏掉、静默少册。改成**每个 905 自己先 `$h` 再 `$b`** —— 一条字段用哪种
    布局由它自己决定；同一条字段内顺序仍是 `$h` 优先（旧布局的 `$b` 是索书号）。
    """
    out: list[str] = []
    for f in rec.get_fields("905"):
        v = (f.get("h") or f.get("b") or "").strip()
        if v and v not in out:
            out.append(v)
    return out or _collect_barcodes(rec, "995", ("f", "h", "a"))


def _extract_record_barcode(rec: Record) -> str | None:
    """记录里**自带**的第一个条码（`905` 优先，其次 `995`）。"""
    vals = _record_barcodes(rec)
    return vals[0] if vals else None


def _record_holding_extras(rec: Record) -> dict:
    """从记录（含字段映射后的）`905` 里取出**记录级 / 馆藏级**散值。

    为什么必须在这里读出来：图书库路径建馆藏时会**重建 905**（`create_holdings` 里
    `rec.fields = [f for f in rec.fields if f.tag != "905"]` 之后按馆藏重写），
    所以 `905$a/$s/$c/$v` 里的信息要先落到记录列/馆藏上，否则会被重建覆盖 ——
    这样"把 909 的字段映射到 905"才真正有意义：

      - `905$c` → 排架号（写入本批册的 `holdings.shelf_no`）
      - `905$v` → 卷册号（写入本批册的 `holdings.volume`）
      - `905$s`（索书号）→ 拆出**书次号**（`分类号/书次号` 的后半段），供沿用；
        顺带在缺 `690$a` 时补上分类号（否则建馆藏会因"缺分类号"被拒）
        ⚠️ **旧布局（2026-10-09 前）索书号在 `$b`** —— 只有**见到 `$h`**（新布局不写 `$h`）
        才回退读 `$b`；否则会把新布局的 `$b`（条码）误当索书号。

    ⚠️ **不取 `905$a` 作馆藏地点**：`905$a` 在我方语义里是馆藏地点，但书商数据里的
    自家 `905` 含义各不相同（实测某校数据把 `905$a` 写成经手人姓名），自动采信等于
    往馆藏里写脏地点。要批量设馆藏地点请用「条码管理 → 批量改地点」。
    """
    from ..core.callno import parse_call_no
    out: dict = {}
    shelf = (rec.first_value("905", "c") or "").strip()
    if shelf:
        out["shelf_no"] = shelf
    vol = (rec.first_value("905", "v") or "").strip()
    if vol:
        out["volume"] = vol
    call_no = (rec.first_value("905", "s") or "").strip()
    if not call_no and (rec.first_value("905", "h") or "").strip():
        # 旧布局兼容（2026-10-09 前）：905 无 `$s`、有 `$h`，索书号当时在 `$b`
        call_no = (rec.first_value("905", "b") or "").strip()
    if call_no:
        cls, book, _vol = parse_call_no(call_no)
        if book:
            out["book_no"] = book
        if cls and not (rec.first_value("690", "a") or "").strip():
            out["class_no"] = cls
    return out


def validate_import_holding_values(extras: dict, barcodes: list[str],
                                   class_no: str = "") -> None:
    """外部导入的统一列宽边界；超长报错，绝不截断（不访问数据库）。"""
    limits = {
        "shelf_no": (HoldingORM.shelf_no.property.columns[0].type.length, "排架号"),
        "volume": (HoldingORM.volume.property.columns[0].type.length, "卷册号"),
        "book_no": (HoldingORM.call_no.property.columns[0].type.length, "书次号"),
        "location": (HoldingORM.location.property.columns[0].type.length, "馆藏地点"),
    }
    for key, (limit, label) in limits.items():
        value = extras.get(key)
        if value is not None and len(value) > limit:
            raise ValueError(f"{label}最多 {limit} 个字符")
    barcode_limit = HoldingORM.barcode.property.columns[0].type.length
    for code in barcodes:
        if len(code) > barcode_limit:
            raise ValueError(f"条码最多 {barcode_limit} 个字符")
    if extras.get("book_no") and class_no:
        manual = extras["book_no"].strip().split(VOLUME_SEP)[0].strip()
        call_no = join_call_no(f"{class_no}/{manual}", extras.get("volume"))
        limit = HoldingORM.call_no.property.columns[0].type.length
        if len(call_no) > limit:
            raise ValueError(f"索书号最多 {limit} 个字符（含分类号、书次号和卷册号）")


class ImportBatchError(RuntimeError):
    """批次运行故障；result 只将已提交的书目/馆藏计为成功。"""

    def __init__(self, cause: Exception, result: dict):
        super().__init__(str(cause))
        self.result = result


def make_barcode_allocator(start: str | None):
    """条码规则 -> 递增生成函数；`start` 为空返回 None，格式非法抛 ValueError。

    `start` 采用与录入栏「起始条码」**完全相同的口径**（见 `create_holdings`）：
    `前缀 + 数字`，如 `A00000001` / `00000001` / `M99`。**位数取数字部分的长度**，
    每调用一次输出一个（前缀 + 补零序号），序号自增；与 `create_holdings` 的区别只是
    这里的计数器**跨记录共享**（每条记录领一个号，而不是一条记录的多个复本）。
    """
    s = (start or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\D*?)(\d+)", s)
    if not m:
        raise ValueError(f"起始条码格式不支持（应为「前缀 + 数字」，如 A00000001）: {start}")
    prefix, digits = m.group(1), m.group(2)
    width, n = len(digits), int(digits)

    def nxt() -> str:
        nonlocal n
        code = f"{prefix}{n:0{width}d}"
        n += 1
        return code

    return nxt


def import_records_to_db(db: Session, records: list[Record], source: str,
                         progress_cb=None, library_id: int | None = None,
                         barcode_start: str | None = None,
                         force: bool = False, checkpoint_cb=None) -> dict:
    """导入管线：清洗 -> 分类号截取 -> 质检 -> **建记录 + 建馆藏**（分批提交）。

    本函数**只**服务于「外部文件直落图书库」（`POST /import/iso?target=records`、
    `POST /jobs/import-iso`）—— 资源库那一支走 `upsert_resource_record`，不经过这里，
    所以下面的分类号截取不会污染资源库的原始底稿。

    ⚠️ **图书库以条码为核心**（README 规则 1）：本函数**必定**为每条记录建馆藏，
    条码来源两选一 ——
      ① 记录自带的条码（`905$b` 新布局 / `905$h` 旧布局，或 `995`）—— **有几个条码就建几册**。
         书商数据常把"每册一条"的字段（909 / 985 / 995，映射后落到 `905$b`）放在记录里，
         一条书目带 3 个条码就是 3 册；只取第一个会静默丢掉其余册（2026-10-07 真实数据）。
      ② `barcode_start`（前缀 + 起始号，口径同录入栏「起始条码」）自动生成一个。
    两者都没有 → **该条不入库**并记入 `write_errors`（旧实现只写 RecordORM、不建馆藏，
    于是复制出一批"有条目无条码"的脏记录，2026-10-07 用户报出）。

    **记录级/馆藏级散值**从（映射后的）`905` 里读：`$c`→排架号、`$v`→卷册号、
    `$a`→馆藏地点、`$s`→索书号（旧布局在 `$b`；拆出书次号，缺 `690$a` 时补分类号）。
    必须在这里读 —— 建馆藏时 `905` 会被**重建**，不先落到记录列/馆藏上就丢了。

    质检：有 ERROR 的记录默认**拒绝**（`force=True` 才放行）—— 与手工录入、
    发货清单直落图书馆同一条门槛（图书库只收校验好的记录）。

    分类号截取：外部文件带进来的 `690$a` 按本馆全局设置截短，口径与套录一致。
    逐条 `SAVEPOINT` 隔离：某条失败（条码重复 / 位数不合规 / 缺 690）不影响其余记录。
    """
    # 这两个函数原先住在 api 层，导致 services → api 的反向依赖（分层倒挂）。
    # 已下沉到 services（book_libraries），依赖方向恢复单向。
    from . import record_to_orm_values
    from .book_libraries import resolve_library

    lib = resolve_library(db, library_id)
    lib_id, lib_name = lib.id, lib.name
    alloc = make_barcode_allocator(barcode_start)
    imported = skipped = with_errors = no_barcode = copies = 0
    errors: list[dict] = []
    forced: list[dict] = []
    total = len(records)
    processed = 0
    class_rule = class_no_rule(db)

    def result_snapshot() -> dict:
        return {"imported": imported, "copies": copies, "skipped": skipped,
                "with_quality_errors": with_errors, "no_barcode": no_barcode,
                "write_errors": errors[:20], "error_count": len(errors),
                "forced": forced[:20], "forced_count": len(forced),
                "library_id": lib_id, "library": lib_name,
                "processed": processed, "total": total}

    committed = result_snapshot()

    def collect_committed_log() -> None:
        # ORM 事件也会收集已回滚 SAVEPOINT 的行：只留已 commit 的汇总，失败同样如此。
        oplog.clear_entries()
        if committed["imported"]:
            oplog.append_bulk(oplog.ACTION_IMPORT, library_id=lib_id,
                              count=committed["imported"],
                              note=f"来源：{source}；共 {committed['copies']} 册")

    def commit_batch() -> None:
        nonlocal committed
        snapshot = result_snapshot()
        # 后台任务的检查点与业务数据在同一事务落库，重启也能看见已提交数。
        if checkpoint_cb:
            checkpoint_cb(processed, total, snapshot)
        db.commit()
        committed = snapshot
        collect_committed_log()
        if progress_cb:
            progress_cb(processed, total)

    def process_record(rec: Record, index: int) -> None:
        nonlocal imported, skipped, with_errors, no_barcode, copies
        cl.clean_record(rec, migrate_pinyin=True)
        shorten_record_class_nos(rec, class_rule)
        label = rec.first_value("200", "a") or rec.first_value("010", "a") or f"第 {index} 条"
        bad = [f for f in validate(rec) if f.level == LEVEL_ERROR]
        if bad:
            with_errors += 1
            if not force:
                skipped += 1
                # 把**具体哪条规则**写进提示：只说"质检未通过"时，50 条被拦就要
                # 逐条点开才知道原因（实测真实 ISO 导入正是这种情况）。这里给前
                # 两条规则 + 剩余条数，够定位又不至于把消息撑爆。
                detail = "；".join(f.message for f in bad[:2])
                if len(bad) > 2:
                    detail += f"（另有 {len(bad) - 2} 处）"
                errors.append({"row": label,
                               "reason": f"质检未通过：{detail}。可改用强制入库"})
                return
        barcodes = _record_barcodes(rec)
        if not barcodes and alloc:
            barcodes = [alloc()]
        if not barcodes:
            no_barcode += 1
            skipped += 1
            errors.append({"row": label,
                           "reason": "记录没有条码，且未提供条码规则（图书库要求每条记录至少一个条码）"})
            return
        extras = _record_holding_extras(rec)
        if extras.get("class_no"):
            from ..core.marc import Field, Subfield
            from ..io.iso import insert_field_ordered
            insert_field_ordered(rec, Field("690", " ", " ", "",
                                           [Subfield("a", extras["class_no"])]))
        try:
            validate_import_holding_values(extras, barcodes,
                                           (rec.first_value("690", "a") or "").strip())
            with db.begin_nested():
                orm = RecordORM(**record_to_orm_values(rec, source=source),
                                library_id=lib_id)
                db.add(orm)
                db.flush()
                orm.volume_seq = derive_volume_seq(extras.get("volume"))
                create_holdings(db, orm, len(barcodes), location=extras.get("location"),
                                barcodes=barcodes, book_no=extras.get("book_no"),
                                per_copy_volumes=_record_copy_volumes(rec) or None,
                                volume=extras.get("volume"),
                                shelf_no=extras.get("shelf_no"))
            imported += 1
            copies += len(barcodes)
            if bad:
                forced.append({"row": label, "reason": "；".join(f.message for f in bad[:3])})
        except (ValueError, MarcWriteError, DataError, IntegrityError) as exc:
            skipped += 1
            errors.append({"row": label, "reason": str(exc)})

    try:
        # 整批导入期间走号段预分配：否则每册都要 UPSERT 一次 callno_counters，
        # 行锁一直持有到 500 条一批的 commit，同库其它终端取号会排队到整批结束
        # 。代价是允许跳号，见 bump_counter_block 的说明。
        with bulk_import_mode():
            for processed, rec in enumerate(records, 1):
                process_record(rec, processed)
                # 拒绝行也要推进批次，不能因 continue 跳过所有检查点。
                if (progress_cb or checkpoint_cb) and processed % 500 == 0:
                    commit_batch()
        if processed != committed["processed"] or not total:
            commit_batch()
    except Exception as exc:
        db.rollback()
        partial = result_snapshot()
        rolled_back = imported - committed["imported"]
        partial.update(imported=committed["imported"], copies=committed["copies"],
                       forced=committed["forced"], forced_count=committed["forced_count"],
                       skipped=skipped + rolled_back + max(processed - imported - skipped, 0),
                       rolled_back=rolled_back, unprocessed=max(total - processed, 0),
                       aborted=True)
        errors.append({"row": f"第 {processed} 条/批次提交", "reason": str(exc)})
        partial["write_errors"] = errors[:19] + [errors[-1]]
        partial["error_count"] = len(errors)
        collect_committed_log()
        raise ImportBatchError(exc, partial) from exc
    collect_committed_log()
    return committed


def recover_interrupted_jobs(db: Session) -> int:
    """应用启动时调用：把遗留的 running / pending 任务标记为 error。

    后台任务跑在进程内（FastAPI BackgroundTasks），进程一重启执行体就没了，
    状态行会永远停在 running —— 前端进度条永远转圈。启动时统一判死。
    注意：任务可能已 commit 了部分数据（导入每 500 条一 commit），这里只如实
    标记"中断、结果未知"，不做对账。返回被标记的任务数。
    """
    from ..models import Job
    n = (db.query(Job)
         .filter(Job.status.in_(["running", "pending"]))
         .update({Job.status: "error",
                  Job.error: "服务重启，任务中断（结果未知，请核对数据后重试）"},
                 synchronize_session=False))
    db.commit()
    return n


def run_iso_import_job(job_id: int, data: bytes, source: str,
                       library_id: int | None = None, target: str = "records",
                       barcode_start: str | None = None, force: bool = False,
                       mapping: dict | None = None,
                       operator: str | None = None) -> None:
    """后台任务：ISO 文件导入，进度写 jobs 表。

    `target` 决定落点（2026-10-07 起 ISO 与发货清单两条路径口径一致）：
      - `records`（默认）：**图书库** —— `library_id` 是图书库 id；每条都要有条码
        （记录自带 `905`/`995` 或 `barcode_rule` 生成），缺条码的不入库；
      - `resource`：**资源库** —— `library_id` 是资源库 id（缺省「手工导入」），按 ISBN 去重。

    `mapping`：字段映射（源 `tag$code` → 目标 `tag$code`），解析后**先搬运再写库**，
    两条落点都生效。

    `operator`：发起人的署名。后台任务跑在请求之外，没有中间件帮忙
    收集与落库，所以这里自己 `begin()` / `flush()` —— 否则"这批是谁导的"永远查不到。
    """
    from ..db import SessionLocal
    from ..models import Job
    db = SessionLocal()
    # 后台任务不在 HTTP 中间件的管辖范围内，操作日志的收集要自己开（见 docstring）
    oplog.begin()
    parse_errors: list = []
    mapped: list[str] = []
    try:
        job = db.get(Job, job_id)
        job.status = "running"
        job.message = "解析文件"
        job.progress = 5
        db.commit()

        records, parse_errors = read_iso(data)
        total = len(records)
        job.message = f"解析到 {total} 条记录"
        db.commit()

        if mapping:
            # 整批合计（`tally`）而不是"去重收集逐条消息" —— 后者会把同一条消息并成
            # 一条，数字却是**首条记录**的，看起来像总数（实测导 150 册显示"9 处"）。
            from ..io.iso import apply_field_mapping, format_mapping_totals
            tally: dict = {}
            for rec in records:
                apply_field_mapping(rec, mapping, tally=tally)
            mapped = format_mapping_totals(tally)

        if target == "resource":
            from .resource_cache import import_iso_records_to_library
            job.message = f"写入资源库（{total} 条）"
            db.commit()
            result = import_iso_records_to_library(db, records, library_id, source_tag=source)
            result["imported"] = result.pop("added")
        else:
            def checkpoint(done: int, t: int, snapshot: dict):
                job.progress = 5 + int(90 * done / max(t, 1))
                job.message = f"导入中 {done}/{t}"
                job.result = {**snapshot, "parse_errors": parse_errors[:10],
                              **({"mapped": mapped} if mapped else {})}

            result = import_records_to_db(db, records, source,
                                          library_id=library_id,
                                          barcode_start=barcode_start, force=force,
                                          checkpoint_cb=checkpoint)
        result["parse_errors"] = parse_errors[:10]
        if mapped:
            result["mapped"] = mapped
        job.status = "done"
        job.progress = 100
        job.message = "完成"
        job.result = result
        db.commit()
    except Exception as e:
        db.rollback()
        job = db.get(Job, job_id)
        job.status = "error"
        job.error = str(e)[:800]
        if isinstance(e, ImportBatchError):
            job.result = {**e.result, "parse_errors": parse_errors[:10],
                          **({"mapped": mapped} if mapped else {})}
        else:
            previous = job.result or {"imported": 0, "copies": 0}
            job.result = {**previous, "aborted": True,
                          "write_errors": previous.get("write_errors", [])[:19]
                                          + [{"row": "批次", "reason": str(e)}],
                          "error_count": previous.get("error_count", 0) + 1,
                          "parse_errors": parse_errors[:10]}
        job.message = f"失败；已提交 {job.result.get('imported', 0)} 条"
        db.commit()
    finally:
        # 管线已经剔除回滚行、只保留 commit 汇总；任务 error 不能抹掉已写业务账。
        # 日志仍用独立会话，状态写失败不应连日志一起回滚。
        from ..services import record_logs as logs_svc
        try:
            s = SessionLocal()
            try:
                logs_svc.flush(s, operator)
            finally:
                s.close()
        except Exception as e:
            log.warning("[oplog] 后台任务写操作日志失败（job=%s）：%s", job_id, e)
        db.close()


def set_book_no(db: Session, orm: RecordORM, class_no: str, book_no: str) -> str:
    """把记录全部馆藏的索书号覆盖为「分类号/书次号」，并抬高书次号计数器。

    手动指定书次号时必须同时抬高计数器，否则下次自动编号会撞上这个号。
    返回新的索书号。
    """
    # 防御：`:` 是**保留**的卷册号分隔符（core.volume.VOLUME_SEP），书次号里不允许出现它。
    # 所以一刀切到第一个 `:` 之前即可 —— 一条规则覆盖三种脏输入：
    #   ① 界面把卷册号后缀一起回填进来（`1:上册`）→ 不切就拼成 `K209/1:上册:上册`；
    #   ② 请求同时改卷册号、夹带的却是**旧**后缀（`1:上册` + volume=下册）
    #      → 不切就拼成 `K209/1:上册:下册`；
    #   ③ 没设卷册号却夹带后缀（`1:上册` + volume=NULL）
    #      → 不切会落成「call_no 有 `:` 段、但 records.volume 为 NULL」的不一致态。
    # 为什么不能只按「匹配当前卷册号才剥」（`call_no_base(manual, volume)`）：
    #   修不了 ② —— 旧后缀匹配不上**新**卷册号，且 PUT 之后的
    #   `apply_volume_to_holdings()` 也救不回来（已实测漂移落库）。
    # 为什么不能反过来按 `/` 取段：附加区分号（`I210.4/L824/2`）必须原样保留。
    # **这层不是冗余**：POST /records 走 create_holdings → 本函数，之后没有
    # `apply_volume_to_holdings()` 兜底，本处是唯一防线（PUT 才有那一步）。
    manual = (book_no or "").strip().split(VOLUME_SEP)[0].strip()
    if not manual:
        raise ValueError("书次号不能为空")
    # 索书号 = 基号(分类号/书次号) + 每册自己的卷册号后缀；卷册号可空。
    base_no = f"{class_no}/{manual}"
    holdings = db.query(HoldingORM).filter(HoldingORM.record_id == orm.id).all()
    target_calls = {join_call_no(base_no, h.volume) for h in holdings} or {base_no}
    call_no = base_no
    # 同库跨记录撞号兜底：前端把**预览号**回填进书次号框
    # 后按手动号回传，「打开表单→保存」窗口内该号可能已被别的终端占用——抬号逻辑
    # （GREATEST）对「号已被占」无感知，静默落成异书同号。
    # 豁免口径与数据体检（inspect 异书同号）**完全一致**：同 ISBN 或同题名的记录
    # 占同一索书号不算冲突——书商「一册一条」导出里同种书的各条记录就是这种形状，
    # 一刀切会把正常导入整批拒掉；既不同 ISBN 也不同题名才报错。复本共享是**同一
    # record_id**（本记录全部馆藏改成同一个号），不进这个检查；已注销/已遗失册的号
    # 同样占位 —— 与「书次号永不回收」一致。
    rows = (db.query(HoldingORM.id, RecordORM.isbn_norm, RecordORM.title_norm)
            .join(RecordORM, RecordORM.id == HoldingORM.record_id)
            .filter(HoldingORM.library_id == orm.library_id,
                    HoldingORM.call_no.in_(target_calls),
                    HoldingORM.record_id != orm.id)
            .all())
    if rows and not any(
        (orm.isbn_norm and r.isbn_norm == orm.isbn_norm)
        or (orm.title_norm and r.title_norm == orm.title_norm)
        for r in rows):
        raise ValueError(
            f"索书号「{call_no}」已被本库其它记录占用：请刷新预览、清空书次号改用"
            "自动取号，或换一个书次号")
    for h in holdings:
        h.call_no = join_call_no(base_no, h.volume)
    if manual.isdigit():
        _raise_seq(db, f"lib{orm.library_id}|{class_no}", int(manual))
    return call_no


def create_holdings(db: Session, orm: RecordORM, copies: int,
                    location: str | None = None,
                    barcode_start: str | None = None,
                    barcodes: list[str] | None = None,
                    book_no: str | None = None,
                    per_copy_volumes: list[str | None] | None = None,
                    accession_batch_no: str | None = None,
                    volume: str | None = None,
                    shelf_no: str | None = None) -> list[HoldingORM]:
    """馆藏生成：条码（显式列表 / 指定起始号递增 / 自动流水）+ 索书号（同种共用）+ 905 同步。

    barcodes：手动完整条码列表（优先）；barcode_start：起始号递增；
    两者都空则自动流水。条码按记录所属图书库独立编号与查重。

    book_no：手动指定书次号（索书号 "分类号/书次号" 的后半段）。给了就覆盖该记录
    现有馆藏的索书号，并把书次号计数器抬到该值，避免后续自动号撞号。

    per_copy_volumes：**逐册**卷册号（按条码顺序对位，来自导入文件的每条 905$v）。
    给了就按册覆盖种级卷册号与索书号的卷册后缀 —— 多卷书一条书目挂多册时，
    各卷的号不能统一成第一册的。
    """
    from ..services import orm_to_record

    lib_id = orm.library_id
    rec = orm_to_record(orm)
    class_no = (rec.first_value("690", "a") or "").strip()
    if not class_no:
        raise ValueError("记录缺少 690 分类号，无法生成索书号")

    # 本批册的默认卷册号/架位（空串归一成 NULL）；record 不再持有这两个字段。
    volume = (volume or "").strip() or None
    shelf_no = (shelf_no or "").strip() or None

    # 索书号：手动书次号 > 沿用同种已有 > 按书次号生成（按记录所属图书库隔离）
    # 三种分支统一产出「基号 + 卷册号后缀」，保证索书号同种共用（README 规则 5）。
    key = f"lib{orm.library_id}|{class_no}"
    existing = db.query(HoldingORM).filter(HoldingORM.record_id == orm.id).first()
    # 给已有记录追加册时：未显式给卷/架位就沿用第一册，避免新册凭空丢卷。
    if existing is not None:
        if volume is None:
            volume = (existing.volume or "").strip() or None
        if shelf_no is None:
            shelf_no = (existing.shelf_no or "").strip() or None
    if (book_no or "").strip():
        # 手动指定要覆盖同种已有馆藏，否则"改了书次号却没生效"
        call_no = join_call_no(set_book_no(db, orm, class_no, book_no), volume)
    elif existing and existing.call_no:
        # 沿用同种已有基号：先把旧的卷册号后缀剥掉（**后缀精确匹配**，合订本自带
        # 斜杠也不能靠 split('/')），再按当前卷册号拼回 —— 重复执行幂等。
        base = call_no_base(existing.call_no, volume)
        base = call_no_base(base, existing.volume)
        call_no = join_call_no(base, volume)
    else:
        # 取号必须用 `_next_seq` 的**返回值**，不能回读计数行。批量导入模式走号段
        # 预分配（`bump_counter_block`）：预留时计数行已被独立连接抬到**块尾**
        # （CALLNO_BLOCK=50），而真正发号只发生在内存号段里。旧写法丢弃返回值、
        # 再经 `_BoundCounter.current()` 回读 `current_seq` —— 逐条模式下恰好等于
        # 刚发的号（侥幸正确），批量模式下拿到的是块尾，于是每个首次出现的分类号
        # 书次号永远等于 50/100/…。`join_call_no` 与 `make_call_no` 的拼接口径一致
        # （见 callno_preview 同款用法）。
        seq = _next_seq(db, key)
        call_no = join_call_no(f"{class_no}/{seq}", volume)

    # 条码序列：显式列表 > 起始号递增 > 自动流水
    n = len(barcodes) if barcodes else max(1, copies)
    digits = _barcode_digits(db)

    def barcode_seq(count: int, start: str | None, explicit: list[str] | None):
        if explicit:
            yield from [b.strip() for b in explicit if b.strip()]
            return
        if start:
            m = re.fullmatch(r"(\D*?)(\d+)", start.strip())
            if not m:
                raise ValueError(f"起始条码格式不支持（应为数字或前缀+数字）: {start}")
            prefix, num = m.group(1), m.group(2)
            width = len(num)
            base = int(num)
            for i in range(count):
                yield f"{prefix}{base + i:0{width}d}"
        else:
            for _ in range(count):
                yield f"{_next_seq(db, barcode_key(lib_id)):0{digits}d}"

    holdings = []
    codes = list(barcode_seq(n, barcode_start, barcodes))
    if len(codes) < n:
        raise ValueError(f"手动条码数量不足：需要 {n} 个，提供 {len(codes)} 个")
    # 批内去重：库内查重（下面的 dup 查询）看不到**本批尚未 flush** 的行——
    # 扫描枪重复扫、粘贴带重复行时三个同号会一起进 add()，直到 flush 才炸
    # IntegrityError（500，用户看不懂）。这里先拦成 400。
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            raise ValueError(f"本批条码重复：{code}（同一批里一个条码只能出现一次）")
        seen.add(code)
    # 逐册卷册号（导入对位用，见 _record_copy_volumes）：给了就越过种级，
    # 按册落到 holdings.volume 与 call_no 的卷册后缀上。
    base_call = call_no_base(call_no, volume) if per_copy_volumes else call_no
    for i, code in enumerate(codes):
        # 位数必须是「全局设置 → 条码位数」里允许的位数之一（硬拦，见 check_barcode_digits）。
        # 自动流水分支按最窄位数生成，天然合规；显式条码与起始号递增都可能不合规。
        check_barcode_digits(db, code)
        # 查重限定本库：不同图书库（不同学校）各自编号，允许同号
        dup = (db.query(HoldingORM.id)
               .filter(HoldingORM.library_id == lib_id, HoldingORM.barcode == code)
               .first())
        if dup:
            raise ValueError(f"条码已存在（本库）: {code}")
        vol_i = volume
        call_i = call_no
        if per_copy_volumes:
            v = per_copy_volumes[i] if i < len(per_copy_volumes) else None
            v = (v or "").strip() or None
            if v != volume:
                vol_i, call_i = v, join_call_no(base_call, v)
        h = HoldingORM(
            record_id=orm.id, library_id=lib_id, barcode=code,
            accession_no=code, acquired_at=datetime.now(timezone.utc),
            price=orm.price, source=orm.source,
            accession_batch_no=accession_batch_no,
            shelf_no=shelf_no,
            location=location, call_no=call_i, volume=vol_i)
        db.add(h)
        holdings.append(h)
    # 规则 14：autoflush=False，写镜像后必须 flush，后面 active_holdings 才读得到新值
    db.flush()

    # 显式条码 / 起始号递增**不消耗流水号**，所以要把本批的纯数字条码抬进计数器
    # （与 set_book_no 抬书次号、转库 _raise_to_max_barcode 同理）：不抬的话，
    # 之后走自动流水时会一路生成到这批号上，撞号报错中断录入——有查重兜底，
    # 不会静默重复，但录入被打断的体验很糟。
    # 只统计**纯数字**条码：带前缀的（A00000050）属于另一个号段，不该影响自动流水。
    # 只有「显式条码 / 起始号」两个分支需要抬：自动流水分支的号就是 bump_counter
    # 产出的，计数器已经到位，再抬一次纯属多余——批量导入时那是每条一次 UPSERT。
    if barcodes or barcode_start:
        numeric = [int(c) for c in codes if c.isdigit()]
        if numeric:
            _raise_seq(db, barcode_key(lib_id), max(numeric))

    # 905 馆藏字段同步：按**在架**馆藏重建（注销/遗失的册不进 905）；
    # 与 `remove_holdings_sync` 共用同一份收尾（含"保留 905$Z/$S 扩展子字段"）。
    from .record_projection import rebuild_905 as _projection_rebuild_905
    _projection_rebuild_905(db, orm.id)
    return holdings


def apply_volume_to_holdings(db: Session, orm: RecordORM,
                             volume: str | None = None) -> None:
    """卷册号变更后：按规则 5 重算该记录**全部**馆藏的索书号，并同步册级卷号。

    遍历**全部**册（含已注销/已遗失的册）；905 仍只在架册生成。
    只改 call_no / volume，**不改 status**。每册按自己的旧卷号精确剥后缀，
    匹配不上原样返回，故重复执行不产生漂移。
    """
    volume = (volume or "").strip() or None
    for h in db.query(HoldingORM).filter(HoldingORM.record_id == orm.id).all():
        base = call_no_base(h.call_no, h.volume)
        h.call_no = join_call_no(base, volume)
        h.volume = volume
    # 规则 14：autoflush=False —— 不 flush 的话，紧接着的 remove_holdings_sync
    # 里 active_holdings 读到的会是旧值。
    db.flush()


def remove_holdings_sync(db: Session, orm: RecordORM,
                         extra_source: Record | None = None) -> None:
    """馆藏变更后重建 905；书目更新可提供旧记录以保留登记扩展。"""
    from .record_projection import rebuild_905
    rebuild_905(db, orm.id, extra_source=extra_source)


class _BoundCounter:
    """core.callno 协议适配：回调传裸分类号，查询始终用构造时绑定的完整 key。"""

    def __init__(self, db: Session, key: str):
        self._db, self._key = db, key

    def current(self, class_no: str) -> int:
        c = self._db.get(CallNoCounter, self._key)
        return c.current_seq if c else 0

    def bump(self, class_no: str) -> int:
        return self.current(class_no)
