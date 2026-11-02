# -*- coding: utf-8 -*-
"""记录级操作日志的**采集**。

## 这个模块只管"收集"，不管"落库"

分工：本模块是纯标量收集器（不碰 ORM、不碰数据库、不碰 HTTP），
落库与查询在 `services/record_logs.py`，触发点在 `models/__init__.py` 的 ORM 事件，
写库时机在 `main.py` 的中间件（业务提交之后）。

## 为什么用 contextvar + ORM 事件，而不是在各接口里手写

能改一条**图书库记录**的路径有七处以上：正常保存（`api/records.py`）、
批量删除、图书转库复制记录（`api/transfer.py`）、馆藏变动重建 905
（`services/batch.py::remove_holdings_sync` 直接给 `orm.fields` 赋值）、
册的注销/恢复、套录本地化、ISO 导入。

挨个手写"记得记一笔"，漏一处就是**静默的失忆** —— 而操作日志的价值恰恰在
"查得出来"。缺一条的日志比没有日志更糟：它会让人以为"没事发生"。
所以挂 ORM 事件（和 `records.price` 冗余列同一个理由，见），
任何写入路径（含以后新写的）自动被记。

## 合并规则

一次 HTTP 请求里同一条记录可能被 flush 多次（保存书目 -> 重建 905 -> 回写馆藏），
每次 flush 都会触发一次 `after_update`。逐次记会得到一串"这条记录被改了"的噪音，
所以按 `record_id` 合并成一条，`action` 取更"重"的那个：

    delete > import > create > update

理由：用户关心的是"这条记录被建出来了 / 被删了"，而不是"建完又被改了一下"。

## 没有 begin 时静默丢弃

测试、脚本、一次性维护工具直接调用 service 层时不会有人去 `begin()`。
此时 `collect()` 必须是个 no-op（不是抛错）—— 采集是"尽力而为"的旁路，
绝不能因为它没准备好就把业务弄挂。
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_IMPORT = "import"
# 2026-10-09 新增：**不产生/不改动任何记录**的批量数据准备动作（如「提取最大书次号」
# 抬高书次号计数器）。它改的是计数器而不是书目，记成 `import` 会让"导入"这个筛选
# 出现一批根本没导记录的行 —— 宁可多一个动作类型，也别让审计口径含糊。
ACTION_SETTINGS = "settings"

# action 的"重量"（见模块 docstring）。数值大者胜出。
_RANK: dict[str, int] = {
    ACTION_SETTINGS: 0,
    ACTION_UPDATE: 0,
    ACTION_CREATE: 1,
    ACTION_IMPORT: 2,
    ACTION_DELETE: 3,
}

# 变更列的中文名。落进 `note` 给人看 —— 编目员要看的是"书目字段被改了"，
# 不是"fields 这个列被改了"。
COL_LABELS: dict[str, str] = {
    "fields": "书目字段",
    "leader": "头标区",
    "marc_raw": "ISO 原文",
    "library_id": "所属图书库",
    "source": "来源",
    "status": "状态",
    "class_no": "分类号",
    "shelf_no": "排架号",
    "volume": "卷册号",
    "volume_seq": "卷册序",
    "price": "定价",
    "price_currency": "定价币种",
    "created_by": "编目员",
}


@dataclass
class Entry:
    """收集到的一次变更（合并后）。全部是标量：本模块不认识 ORM。"""

    record_id: int | None
    action: str
    library_id: int | None = None
    title: str = ""
    barcode: str = ""
    # 涉及的**册数**：单条记录改动恒为 1；批量导入的汇总行放实际条数。
    copies: int = 1
    note: str = ""
    # 变了的列（键为 ORM 列名）。落库时翻成中文塞进 note。
    changes: set[str] = field(default_factory=set)


@dataclass
class _State:
    """一次请求/任务的收集缓冲。"""

    entries: list[Entry] = field(default_factory=list)
    # record_id -> entries 里的下标，用于 O(1) 合并（导入上千条时不做线性查找）
    index: dict[int, int] = field(default_factory=dict)


_state: ContextVar[_State | None] = ContextVar("record_log_collector", default=None)


def begin() -> None:
    """开始一次收集（中间件在每个请求开头调用；后台任务在开跑前调用）。

    ⚠️ **只能在"最外层"调用**。`BaseHTTPMiddleware` 会把路由派发到一个子 task，
    子 task 的 context 是父 context 的**拷贝**：在子 context 里 `set()` 一个
    ContextVar，父 context 看不到。所以"在路由里 `discard()` 再 `begin()`"
    换掉的是**子 context 的那份**引用，中间件手里仍是原来那个收集器
    。
    要在路由里丢弃已收集的内容，用 `clear_entries()`（原地清空，不换对象）。
    """
    _state.set(_State())


def clear_entries() -> None:
    """**原地**清空当前收集器（保留同一个 State 对象）。

    给"整批动作要替代逐条明细"的场景用（ISO / 发货清单导入）：先 `clear_entries()`
    把那一批逐条 `create` 丢掉，再 `append_bulk()` 记一行汇总。

    与 `discard()` 的区别正是上面那个 context 坑 —— 这里不碰 ContextVar，
    所以路由（子 context）调用它，中间件（父 context）也看得见。
    """
    st = _state.get()
    if st is not None:
        st.entries.clear()
        st.index.clear()


def active() -> bool:
    """当前是否在收集（供测试与调试用）。"""
    return _state.get() is not None


def collect(record_id: int | None, action: str, *, library_id: int | None = None,
            title: str = "", barcode: str = "", copies: int = 1,
            note: str = "", changes: set[str] | None = None) -> None:
    """记一次变更（由 ORM 事件调用）。

    没有 `begin()` 时静默返回：采集是旁路，不该因为"没人开收集"而报错。
    """
    st = _state.get()
    if st is None:
        return

    # 汇总行（批量导入）不按 record_id 合并：它本来就不针对单条记录。
    if record_id is None:
        st.entries.append(Entry(record_id=None, action=action, library_id=library_id,
                                title=title, barcode=barcode, copies=copies,
                                note=note, changes=set(changes or ())))
        return

    pos = st.index.get(record_id)
    if pos is None:
        st.index[record_id] = len(st.entries)
        st.entries.append(Entry(record_id=record_id, action=action,
                                library_id=library_id, title=title,
                                barcode=barcode, copies=copies, note=note,
                                changes=set(changes or ())))
        return

    ent = st.entries[pos]
    if _RANK.get(action, 0) > _RANK.get(ent.action, 0):
        ent.action = action
    # 后面的事件带着更全的信息（insert 时还没排架号，update 时才有）——
    # 因此"填空而非覆盖"：已经有值的不动。
    ent.library_id = ent.library_id if ent.library_id is not None else library_id
    ent.title = ent.title or title
    ent.barcode = ent.barcode or barcode
    ent.note = ent.note or note
    ent.copies = max(ent.copies, copies)
    if changes:
        ent.changes |= set(changes)


def append_bulk(action: str, *, library_id: int | None, count: int, note: str = "",
                title: str = "", barcode: str = "") -> None:
    """手动追加一条**汇总**日志（整批动作：ISO 导入、按筛选批量删除）。

    整批动作记一行而不是 N 行：导入 1000 条记 1000 行会把日志页刷屏，
    而用户问的是"这一批是谁导的"。语义上也确实是一个动作。
    """
    collect(None, action, library_id=library_id, title=title, barcode=barcode,
            copies=max(int(count), 0), note=note)


def take() -> list[Entry]:
    """取出并清空当前收集（落库用）。没有收集时返回空列表。"""
    st = _state.get()
    _state.set(None)
    return list(st.entries) if st is not None else []


def discard() -> None:
    """放弃当前收集（请求失败/回滚时用 —— 没成功就不该留下"改过"的记录）。

    与 `clear_entries()` 不同：它把 ContextVar 置空，**只在当前 context 生效**
    （中间件调用没问题；在路由里调用请看 `begin()` 的说明）。
    """
    _state.set(None)


def peek() -> list[Entry]:
    """只看不清（测试与调试用）。"""
    st = _state.get()
    return list(st.entries) if st is not None else []


# ---------------------------------------------------------------------------
#  从字段数组里取"这条记录是谁"（供 ORM 事件调用）
# ---------------------------------------------------------------------------

def peek_identity(fields) -> tuple[str, str]:
    """从字段数组里取 `(题名, 首个条码)`。

    **为什么日志要冗余存这两样**：日志要能**独立读懂**。记录删掉之后只剩一个
    id 的日志等于没记 —— 谁也没法回答"被删的是哪本书"。跟 agent 审计冗余
    `token_name` 是同一个道理（凭据删了也认得出是谁）。

    ⚠️ **条码子字段两套布局都要认**（2026-10-09 起我方 905 按标准把条码放 `$b`）：
    新布局 `$b`（= 登录号），旧布局 `$h`。**见到 `$h` 即判定旧布局**（新布局不写 `$h`），
    否则新布局的 `$b` 才是条码 —— 旧布局的 `$b` 是**索书号**，认错会记成索书号。
    """
    title = ""
    bar_legacy = bar_std = ""
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        tag = f.get("tag")
        if tag not in ("200", "905"):
            continue
        for sf in f.get("sf") or []:
            if not isinstance(sf, (list, tuple)) or len(sf) < 2:
                continue
            code, value = sf[0], sf[1]
            text = str(value or "").strip()
            if not text:
                continue
            if tag == "200" and code == "a" and not title:
                title = text
            elif tag == "905" and code == "h" and not bar_legacy:
                bar_legacy = text          # 旧布局（2026-10-09 前）：条码在 $h
            elif tag == "905" and code == "b" and not bar_std:
                bar_std = text             # 新布局（标准）：条码在 $b（登录号）
        if title and (bar_legacy or bar_std):
            break
    barcode = bar_legacy or bar_std
    return title[:512], barcode[:64]


def note_of(entry: Entry, extra: str = "") -> str:
    """把"这条日志自己的说明"+"变了哪些列"+"调用方补的说明"拼成一句给人看的话。"""
    parts: list[str] = []
    if entry.note:
        parts.append(entry.note)
    if extra:
        parts.append(extra)
    labels = [COL_LABELS.get(k, k) for k in sorted(entry.changes)]
    # 「变更：…」只在**修改**时才写。新建的 changes 是 flush 过程中顺带攒出来的
    # （记录 insert 后紧接着回写排架号/卷册号、重建 905，会触发一次 before_update，
    # 合并进同一条 create 条目）——那条记录之前根本不存在，写"变更：书目字段/排架号"
    # 会让人以为是被改出来的。实测（2026-10-08 20 条 ISBN 录入抽查）确实如此。
    if labels and entry.action == ACTION_UPDATE:
        parts.append("变更：" + "/".join(labels))
    return "；".join(parts)[:256]


__all__ = [
    "ACTION_CREATE", "ACTION_DELETE", "ACTION_IMPORT", "ACTION_UPDATE",
    "COL_LABELS", "Entry", "active", "append_bulk", "begin",
    "clear_entries", "collect", "discard", "note_of", "peek", "peek_identity",
    "take",
]
