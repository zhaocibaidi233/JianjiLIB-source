from __future__ import annotations

from sqlalchemy import event

from ..core.pricing import price_columns
from ..core import oplog

from .catalog import RecordORM

@event.listens_for(RecordORM, "before_insert")
@event.listens_for(RecordORM, "before_update")
def _sync_price_columns(mapper, connection, target: RecordORM) -> None:
    """凡是要落库的书目记录，都从 `fields` 重算 `price` / `price_currency`。

    **为什么挂在 ORM 事件上，而不是散在各个写入点**：能改 `fields` 的地方不止
    `record_to_orm_values()` 一处 —— 图书转库要复制记录（`api/transfer.py`）、
    馆藏变动要重建 905（`services/batch.py::remove_holdings_sync` 直接给 `orm.fields` 赋值）、
    套录本地化也改 `fields`。挨个补一遍，将来新写的路径还是会漏；漏一处的后果是
    **统计页码洋与记录详情对不上，而且是静默的**（账目数字，不能有这种失败模式）。
    挂事件后，任何写入路径（含以后新写的）自动一致 —— 这是本列唯一的一致性保证。

    代价：每次写记录多跑一次小正则（字段数组只有几十项），可忽略。

    边界：`session.bulk_insert_mappings` 等 bulk 操作**不触发** ORM 事件。
    本项目批量导入记录走的是 `session.add`（`services/batch.py`），故无遗漏；
    将来若改走 bulk，必须自己带上这两列。
    """
    for key, value in price_columns(target.fields).items():
        setattr(target, key, value)


@event.listens_for(RecordORM, "before_insert")
@event.listens_for(RecordORM, "before_update")
def _sync_reader_level(mapper, connection, target: RecordORM) -> None:
    """`reader_level` 是 `100$a` 第 17-19 位（读者对象）的**缓存列**。

    与 `price`/`price_currency` 同一套哲学（见上面那个钩子）：能改 `fields` 的路径
    不止一处（新建、PUT、导入、转库、套录本地化…），挨个补必然漏；挂 ORM 事件
    才能保证任何写入路径都一致。**真源是 `100$a`**，这一列只是副本。

    归一成**去掉尾随空格**的形式（`em ` → `em`）—— CNMARC 该位是 3 字符定长、
    末位常为空格，带不带空格在界面上看着一样，统一成不带更省心。
    """
    target.reader_level = _reader_level_of(target.fields)


def _reader_level_of(fields) -> str | None:
    """从 `fields` 里取 `100$a` 第 17-19 位（去尾随空格；取不到 → None）。"""
    for f in (fields or []):
        if not isinstance(f, dict) or f.get("tag") != "100":
            continue
        for sf in (f.get("sf") or []):
            if isinstance(sf, (list, tuple)) and len(sf) >= 2 and sf[0] == "a":
                a = sf[1] or ""
                return (a[17:20].strip() or None) if len(a) >= 20 else None
        return None
    return None


_DERIVED_COLUMNS = frozenset({"price", "price_currency", "marc_raw"})


def _changed_columns(target: RecordORM) -> set[str]:
    """本次 UPDATE 真正变了的列名（派生列已剔除）。"""
    from sqlalchemy import inspect as _inspect

    out: set[str] = set()
    try:
        state = _inspect(target)
    except Exception:              # 拿不到状态就别猜（采集是旁路，不能影响写入）
        return out
    for attr in state.attrs:
        if attr.key in _DERIVED_COLUMNS:
            continue
        try:
            if attr.history.has_changes():
                out.add(attr.key)
        except Exception:
            continue
    return out


@event.listens_for(RecordORM, "after_insert")
def _log_record_insert(mapper, connection, target: RecordORM) -> None:
    """新建记录 -> 一条 `create`（必须 `after_insert`：此前 id 还没生成）。"""
    title, barcode = oplog.peek_identity(target.fields)
    oplog.collect(target.id, oplog.ACTION_CREATE, library_id=target.library_id,
                  title=title, barcode=barcode)


@event.listens_for(RecordORM, "before_update")
def _log_record_update(mapper, connection, target: RecordORM) -> None:
    """修改记录 -> 一条 `update`（含变了的列）。

    用 `before_update` 而非 `after_update`：`attr.history`（"到底改了什么"）
    在 flush 过程中最可靠，flush 之后再取可能已被清理。
    """
    title, barcode = oplog.peek_identity(target.fields)
    oplog.collect(target.id, oplog.ACTION_UPDATE, library_id=target.library_id,
                  title=title, barcode=barcode, changes=_changed_columns(target))


@event.listens_for(RecordORM, "before_delete")
def _log_record_delete(mapper, connection, target: RecordORM) -> None:
    """删除记录 -> 一条 `delete`（只记 id 等于没记，故连题名一起留下）。"""
    title, barcode = oplog.peek_identity(target.fields)
    oplog.collect(target.id, oplog.ACTION_DELETE, library_id=target.library_id,
                  title=title, barcode=barcode)
