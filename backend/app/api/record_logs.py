# -*- coding: utf-8 -*-
"""操作日志接口。

三个入口：
  - `GET /api/record-logs`            —— 全库日志（筛选：记录/操作人/动作/图书库/日期/关键词）
  - `GET /api/record-logs/options`    —— 筛选下拉的候选取值（动作固定 + 出现过的操作人）
  - `GET /api/records/{id}/logs`      —— **某一条记录**的操作历史（记录详情页用）
  - `DELETE /api/record-logs`         —— 清空（仅管理员）

权限：整条路径归 `records`（图书库与编目）模块 —— 能编目的人本来就该看得到
"这条记录谁改的"；另开一个模块只会让角色权限表多一列没人懂的开关
（与 agent 审计归 `users` 是同一个理由，见 core/perm.py）。
清空是破坏性操作，另加管理员判定。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import record_logs as svc
from ..core.route_meta import api
from .users import require_admin

router = APIRouter(tags=["record-logs"])


@router.get("/record-logs")
@api(module="records")
def list_record_logs(
    record_id: int | None = None,
    library_id: int | None = None,
    operator: str | None = None,
    action: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str | None = None, order: str | None = None,
    db: Session = Depends(get_db),
):
    """操作日志（默认新的在前）。`q` 匹配题名 / 条码 / 说明。

    `sort` / `order`：点击表头排序（白名单见 `services/record_logs.LOG_SORTS`）。
    """
    items, total = svc.list_logs(
        db, limit=limit, offset=offset, record_id=record_id, library_id=library_id,
        operator=operator, action=action, q=q, date_from=date_from, date_to=date_to,
        sort=sort, order=order)
    return {"items": items, "total": total}


@router.get("/record-logs/options")
@api(module="records")
def record_log_options(db: Session = Depends(get_db)):
    """筛选候选取值（动作 + 出现过的操作人）。"""
    return svc.options(db)


@router.delete("/record-logs")
@api(module="records")
def clear_record_logs(before: date | None = None,
                      admin=Depends(require_admin), db: Session = Depends(get_db)):
    """清空操作日志。带 `before` 时只清该日期之前的（"只清上个月"）。

    破坏性但可接受：日志是**审计留痕**不是业务数据，清掉不影响任何记录。
    本机单库，管理员说了算（与 agent 审计的清空同一处理）。
    """
    return {"deleted": svc.clear_logs(db, before=before)}


@router.get("/records/{record_id}/logs")
@api(module="records")
def record_history(record_id: int, limit: int = Query(50, ge=1, le=200),
                   db: Session = Depends(get_db)):
    """某一条记录的变更历史（新的在前）。记录已被删时仍可查（叶子上的 id 不解引用）。"""
    from ..models import RecordORM

    if db.get(RecordORM, record_id) is None:
        # 记录不在了不代表没有历史 —— 但界面上没人会给一个不存在的 id 发请求，
        # 这里 404 是为了尽早暴露"前端拿错了 id"。
        raise HTTPException(404, "记录不存在")
    items, total = svc.list_logs(db, limit=limit, record_id=record_id)
    return {"items": items, "total": total}
