# -*- coding: utf-8 -*-
"""agent 凭据与调用审计的管理接口。

权限归「用户管理」模块（`core/perm.py`），与账号/角色同一道闸门 —— 能建 agent
凭据的人，就是能建账号的人。

## 三条刻意的取舍

1. **明文令牌只在创建响应里出现一次**。库里只有 SHA-256，事后取不回来。这是
   凭据该有的样子（忘了就重发一条），也让「库被拷走也换不出可用凭据」成立。
2. **吊销是软删除**。行留着，审计日志上的 `token_id` 才有处可查；硬删会让
   「这条凭据以前干过什么」变成一笔糊涂账。吊销**立即生效**（中间件每次请求
   都查 `revoked_at`），不需要等任何缓存过期。
3. **审计只保留最近 `AUDIT_KEEP` 条**（5000）。单机工具不该被自己的日志撑爆；
   要长期留档就导出，别指望库里那张表。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AgentToken, Role
from ..services import agents as svc
from ..core.route_meta import api
from .users import require_admin

router = APIRouter(tags=["agents"])


class AgentTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role_id: int
    note: str | None = None


@router.get("/agent-tokens")
@api(module="users")
def list_agent_tokens(admin=Depends(require_admin), db: Session = Depends(get_db)):
    """凭据列表（**不含**令牌本身，只给前 8 位前缀用于在界面上认人）。"""
    rows = db.execute(select(AgentToken).order_by(AgentToken.id)).scalars().all()
    roles = {r.id: r for r in db.execute(select(Role)).scalars().all()}
    items = []
    for row in rows:
        role = roles.get(row.role_id)
        items.append(svc.agent_public(db, row,
                                      role_title=role.title if role else None,
                                      role_name=role.name if role else None))
    return {"items": items}


@router.post("/agent-tokens")
@api(module="users")
def create_agent_token(payload: AgentTokenIn, admin=Depends(require_admin),
                       db: Session = Depends(get_db)):
    """建一条凭据。**响应里的 `token` 是明文，只出现这一次**，界面要提示立刻复制。

    `role_id` 决定这条凭据能干什么：权限判定完全走既有角色，**不给角色之外的
    额外授权**（想让它只读，就建/选一个只读角色）。
    """
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "凭据名称不能为空")
    if db.execute(select(AgentToken).where(AgentToken.name == name)
                  ).scalar_one_or_none() is not None:
        raise HTTPException(409, f"凭据名称「{name}」已存在")
    if db.get(Role, payload.role_id) is None:
        raise HTTPException(404, "角色不存在")

    who = admin.display_name or admin.username
    plain, row = svc.create_agent_token(db, name=name, role_id=payload.role_id,
                                        created_by=who, note=payload.note)
    item = svc.agent_public(db, row)
    item["token"] = plain           # ★ 唯一一次明文
    return item


@router.delete("/agent-tokens/{token_id}")
@api(module="users")
def revoke_agent_token(token_id: int, admin=Depends(require_admin),
                       db: Session = Depends(get_db)):
    """吊销凭据（**立即生效**、不可撤销 —— 要恢复只能新建一条）。"""
    row = svc.revoke_agent_token(db, token_id)
    if row is None:
        raise HTTPException(404, "凭据不存在")
    return svc.agent_public(db, row)


@router.get("/agent-audit")
@api(module="users", capability="admin.audit")
def list_agent_audit(limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0),
                     token_id: int | None = None,
                     q: str | None = None,
                     admin=Depends(require_admin), db: Session = Depends(get_db)):
    """agent 调用审计（新的在前）。`q` 匹配凭据名或路径，用来追「谁动了图书库」。"""
    items, count = svc.list_audit(db, limit=limit, offset=offset,
                                  token_id=token_id, q=q)
    return {"total": count, "limit": limit, "offset": offset, "items": items}


@router.delete("/agent-audit")
@api(module="users")
def clear_agent_audit(token_id: int | None = None, admin=Depends(require_admin),
                      db: Session = Depends(get_db)):
    """清空审计（不给 token_id 则全清）。凭据本身不受影响。

    清除动作会留一条哨兵行（见 services.agents.clear_audit）：谁在何时清了什么，
    审计链上至少有最近一次清除的记录。
    """
    return {"deleted": svc.clear_audit(db, token_id=token_id,
                                       operator=admin.display_name or admin.username)}
