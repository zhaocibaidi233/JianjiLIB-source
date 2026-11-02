# -*- coding: utf-8 -*-
"""记录级操作日志：落库、查询、清理。

采集在 `core/oplog.py`（contextvar 收集器）+ `models/__init__.py`（ORM 事件），
本模块只管把收集到的东西写下去、以及把写下去的查出来。

## 为什么落库时机在**中间件**（业务提交之后）

采集只能说明"ORM 打算改这条记录"，业务可能随后回滚（质检没过、条码撞号、
主键冲突……）。回滚了就等于没发生，**不该**留日志 —— 否则用户会看到
"某条记录被改过"但记录其实一点没变，比没有日志更容易误导人。

所以：中间件在 `call_next` 返回后看**状态码**，`< 400` 才落库。
（FastAPI 把 HTTPException 转成响应，所以 400/409/422 都能在这里看到。）

## 与 agent 审计的关系

`agent_audit_logs` 记"请求"，这里记"数据变了"。agent 通过 REST 改记录时
**两张表都会写**：审计回答"它调了什么接口"，操作日志回答"它动了哪条记录"。
不重复，是互补 —— 只读翻页的 agent 只进审计，不进这里。
"""
from __future__ import annotations

import logging

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import oplog
from ..models import RecordLog
from .sorting import apply_sort

log = logging.getLogger(__name__)

# 操作日志**可排序的列**（白名单，见 `services/sorting.py`）。键与前端列的 `prop` 对齐。
# 题名/条码是日志表上的**冗余列**（记录删了也还认得出来），所以排它们不用 join。
LOG_SORTS = {
    "id": RecordLog.id,
    "created_at": RecordLog.created_at,
    "operator": RecordLog.operator,
    "action": RecordLog.action,
    "title": RecordLog.title,
    "barcode": RecordLog.barcode,
}

# 保留条数。操作日志是审计刚需（不像 agent 审计那样是可以随时清零的调试产物），
# 所以留得比 AUDIT_KEEP(5000) 宽：单机工具每天几十条，两万条够撑很多年。
LOG_KEEP = 20000

# 动作取值（给前端下拉；顺序 = 展示顺序）
ACTIONS: tuple[tuple[str, str], ...] = (
    (oplog.ACTION_CREATE, "新建"),
    (oplog.ACTION_UPDATE, "修改"),
    (oplog.ACTION_DELETE, "删除"),
    (oplog.ACTION_IMPORT, "导入"),
    # 「设置」= 不产生记录的数据准备动作（如提取最大书次号抬计数器）。
    (oplog.ACTION_SETTINGS, "设置"),
)

UNSIGNED = "(未署名)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """内存库可能给 naive 值，补上 UTC（与 services/agents.py 同一处理）。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
#  落库
# ---------------------------------------------------------------------------

def flush(db: Session, operator: str | None = None, *, extra_note: str = "") -> int:
    """把当前收集到的变更写成日志行。返回写入行数。

    **写失败一律吞掉**：日志是旁路，不能让"记不上账"把一次正常保存变成 500。
    业务此时已经提交完了，日志表出问题不该反过来把业务判成失败。
    """
    entries = oplog.take()
    if not entries:
        return 0
    rows = [
        RecordLog(
            action=e.action,
            operator=(operator or None),
            library_id=e.library_id,
            record_id=e.record_id,
            title=(e.title or "")[:512],
            barcode=(e.barcode or "")[:64],
            copies=max(int(e.copies or 1), 0),
            note=oplog.note_of(e, extra_note) or None,
            created_at=_now(),
        )
        for e in entries
    ]
    try:
        db.add_all(rows)
        db.commit()
    except Exception as exc:
        db.rollback()
        log.warning("[oplog] 写操作日志失败（%d 行已丢弃）：%s", len(rows), exc)
        return 0
    _prune(db)
    return len(rows)


def record_bulk(db: Session, *, action: str, operator: str | None,
                library_id: int | None, count: int, note: str = "") -> int:
    """直接写一行**整批**日志（后台任务用：那里没有请求上下文、不跑收集器）。

    整批动作（ISO 导入 N 条、按筛选删 M 条）记一行：语义上确实是一个动作，
    记 N 行只会把日志页刷屏，而用户问的是"这一批是谁干的"。
    """
    if count <= 0:
        return 0
    try:
        db.add(RecordLog(action=action, operator=(operator or None),
                         library_id=library_id, record_id=None, title="",
                         barcode="", copies=int(count),
                         note=(note or None), created_at=_now()))
        db.commit()
    except Exception as exc:
        db.rollback()
        log.warning("[oplog] 写批量日志失败：%s", exc)
        return 0
    _prune(db)
    return 1


def _prune(db: Session) -> None:
    """裁掉超出 `LOG_KEEP` 的旧行（一条按主键从旧到新的区间 DELETE）。"""
    try:
        db.execute(
            RecordLog.__table__.delete().where(
                RecordLog.id <= select(RecordLog.id)
                .order_by(RecordLog.id.desc())
                .offset(LOG_KEEP).limit(1).scalar_subquery()
            )
        )
        db.commit()
    except Exception:
        db.rollback()          # 裁剪失败不影响业务：多留几条日志而已


# ---------------------------------------------------------------------------
#  查询
# ---------------------------------------------------------------------------

def _filters(*, record_id: int | None = None, library_id: int | None = None,
             operator: str | None = None, action: str | None = None,
             q: str | None = None, date_from: date | None = None,
             date_to: date | None = None) -> list:
    from ..db import like_escape

    cond = []
    if record_id is not None:
        cond.append(RecordLog.record_id == record_id)
    if library_id is not None:
        cond.append(RecordLog.library_id == library_id)
    if (operator or "").strip():
        pat = f"%{like_escape(operator.strip())}%"
        cond.append(RecordLog.operator.ilike(pat, escape="\\"))
    if (action or "").strip():
        cond.append(RecordLog.action == action.strip())
    if (q or "").strip():
        pat = f"%{like_escape(q.strip())}%"
        cond.append((RecordLog.title.ilike(pat, escape="\\")) |
                    (RecordLog.barcode.ilike(pat, escape="\\")) |
                    (RecordLog.note.ilike(pat, escape="\\")))
    if date_from is not None:
        cond.append(RecordLog.created_at >= datetime(date_from.year, date_from.month,
                                                     date_from.day, tzinfo=timezone.utc))
    if date_to is not None:
        # 含当天：取次日零点为开区间上界（只写 `<= 当日 00:00` 会把当天的全漏掉）
        nxt = datetime(date_to.year, date_to.month, date_to.day,
                       tzinfo=timezone.utc) + timedelta(days=1)
        cond.append(RecordLog.created_at < nxt)
    return cond


def list_logs(db: Session, *, limit: int = 100, offset: int = 0,
              record_id: int | None = None, library_id: int | None = None,
              operator: str | None = None, action: str | None = None,
              q: str | None = None, date_from: date | None = None,
              date_to: date | None = None,
              sort: str | None = None, order: str | None = None) -> tuple[list[dict], int]:
    """日志列表（默认新的在前）。返回 `(items, total)`。

    `sort` / `order`：点击表头排序（白名单见 `LOG_SORTS`，白名单外回退 `id desc`）。
    """
    cond = _filters(record_id=record_id, library_id=library_id, operator=operator,
                    action=action, q=q, date_from=date_from, date_to=date_to)
    base = select(RecordLog).where(*cond) if cond else select(RecordLog)
    inner = select(RecordLog.id).where(*cond) if cond else select(RecordLog.id)
    total = db.execute(select(func.count()).select_from(inner.subquery())).scalar() or 0
    stmt = apply_sort(base, LOG_SORTS, sort, order,
                      default="id", default_order="desc", tiebreak=RecordLog.id)
    rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return [log_public(r) for r in rows], int(total)


def options(db: Session, *, limit: int = 200) -> dict:
    """筛选下拉的候选取值：动作（固定）+ 出现过的操作人（按最近使用去重）。"""
    rows = db.execute(
        select(RecordLog.operator, func.max(RecordLog.id).label("last_id"))
        .where(RecordLog.operator.isnot(None))
        .group_by(RecordLog.operator)
        .order_by(func.max(RecordLog.id).desc())
        .limit(limit)
    ).all()
    return {
        "actions": [{"value": v, "label": label} for v, label in ACTIONS],
        "operators": [r[0] for r in rows if r[0]],
        "actions_keep": LOG_KEEP,
    }


def clear_logs(db: Session, *, before: date | None = None) -> int:
    """清空日志（可只清 `date_to` 之前的，便于"只清上个月"）。返回删除行数。"""
    from sqlalchemy import delete as _delete

    stmt = _delete(RecordLog)
    if before is not None:
        cutoff = datetime(before.year, before.month, before.day, tzinfo=timezone.utc)
        stmt = stmt.where(RecordLog.created_at < cutoff)
    n = db.execute(stmt).rowcount or 0
    db.commit()
    return int(n)


def log_public(row: RecordLog) -> dict:
    action_label = dict(ACTIONS).get(row.action, row.action)
    return {
        "id": row.id,
        "action": row.action,
        "action_label": action_label,
        "operator": row.operator or UNSIGNED,
        "library_id": row.library_id,
        "record_id": row.record_id,
        "title": row.title or "",
        "barcode": row.barcode or "",
        "copies": row.copies,
        "note": row.note or "",
        "created_at": _aware(row.created_at),
    }


__all__ = [
    "ACTIONS", "LOG_KEEP", "UNSIGNED", "clear_logs", "flush", "list_logs",
    "log_public", "options", "record_bulk",
]
