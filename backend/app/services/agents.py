# -*- coding: utf-8 -*-
"""agent 凭据与调用审计。

## 为什么 agent 要有自己的凭据，而不是复用登录令牌

登录令牌是**给人**的：30 天过期、登出即失效、每换一台机器就得重登。agent
（MCP 客户端 / 定时脚本）的令牌要写在配置文件里长期有效，且必须能**单独吊销**
——某台机器的配置泄露时，把那条凭据吊销就行，不用去改所有人的登录态。

## 权限不另起炉灶

凭据绑一个**既有 Role**，判定走 `core/perm.py` 那张模块权限表（经
`services/auth.check_path`，与人的请求**同一个函数**）。agent 能做的事
不会比同角色的人多一件 —— 两套判断必然漂移，漂移那天就是越权的那天。

## 审计只记 agent

人的操作有界面、有确认框、当事人说得清；agent 是无人值守的，出事只能靠日志
回答「它什么时候动了什么」。所以**逐条记** agent 请求（方法 + 路径 + 状态码 +
耗时），人的请求一条不记（量级差两个数量级，且不该因为开了这功能就被记账）。

保留最近 `AUDIT_KEEP` 条：单机工具不能让一张日志表把库撑爆。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.security import new_token, token_hash
from ..models import AgentAuditLog, AgentToken

# 明文令牌的前缀。让它一眼能认出「这是 agent 凭据」——配置文件里几十行看下来，
# 靠开头几个字符分得清哪条是什么，比事后去库里查快得多。
PLAIN_PREFIX = "cat-"

# 列表里展示的明文位数（够认人，不够登录）
PREFIX_LEN = 8

# `last_used_at` 的节流间隔：每个请求都写一次会让只读接口变成写接口（还抢行锁）。
_TOUCH_INTERVAL = timedelta(minutes=5)

# 审计保留条数。超出部分按 id 从旧往新删（一条 DELETE，主键索引区间扫描）。
AUDIT_KEEP = 5000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite / 内存库可能给 naive 值，补上 UTC 再和 now() 相减。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
#  凭据
# ---------------------------------------------------------------------------

def create_agent_token(db: Session, *, name: str, role_id: int,
                       created_by: str | None = None,
                       note: str | None = None) -> tuple[str, AgentToken]:
    """建一条凭据，返回 `(明文令牌, 行)`。

    **明文只在这一刻存在**（随后库里只有 SHA-256），调用方必须在响应里一次性
    交出去 —— 事后没有任何办法再取回，只能重新建一条。
    """
    plain = PLAIN_PREFIX + new_token()
    row = AgentToken(name=name.strip(), token_hash=token_hash(plain),
                     prefix=plain[:PREFIX_LEN], role_id=role_id,
                     created_by=(created_by or None), note=(note or None),
                     created_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return plain, row


def resolve_agent_token(db: Session, token: str | None) -> AgentToken | None:
    """令牌 → 凭据。无效 / 已吊销一律 None。"""
    if not token:
        return None
    row = db.execute(
        select(AgentToken).where(AgentToken.token_hash == token_hash(token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    now = _now()
    if row.last_used_at is None or now - _aware(row.last_used_at) > _TOUCH_INTERVAL:
        row.last_used_at = now
        db.commit()
    return row


def revoke_agent_token(db: Session, token_id: int) -> AgentToken | None:
    """吊销（**软删除**：留着行，审计日志上的 token_id 才有处可查）。"""
    row = db.get(AgentToken, token_id)
    if row is None:
        return None
    row.revoked_at = _now()
    db.commit()
    return row


def agent_public(db: Session, row: AgentToken, *, role_title: str | None = None,
                 role_name: str | None = None) -> dict:
    from ..core.perm import effective_levels

    if role_title is None:
        from ..models import Role
        role = db.get(Role, row.role_id)
        role_title = role.title if role is not None else None
        role_name = role.name if role is not None else None
        perms = (role.permissions or {}) if role is not None else {}
    else:
        perms = {}
    return {
        "id": row.id, "name": row.name, "prefix": row.prefix,
        "role_id": row.role_id, "role_name": role_name, "role_title": role_title,
        "levels": effective_levels(perms),
        "created_by": row.created_by, "note": row.note,
        "created_at": _aware(row.created_at),
        "last_used_at": _aware(row.last_used_at),
        "revoked_at": _aware(row.revoked_at),
        "active": row.revoked_at is None,
    }


# ---------------------------------------------------------------------------
#  审计
# ---------------------------------------------------------------------------

def record_audit(db: Session, *, token_id: int, token_name: str, method: str,
                 path: str, status: int, duration_ms: int) -> None:
    """记一行 agent 调用，并裁掉超出 `AUDIT_KEEP` 的旧行。

    **每条 agent 请求都要写**，所以这里必须便宜：一次 INSERT + 一次区间 DELETE
    （绝大多数时候删 0 行）。
    """
    db.add(AgentAuditLog(token_id=token_id, token_name=token_name,
                         method=(method or "")[:8], path=(path or "")[:256],
                         status=int(status), duration_ms=int(duration_ms),
                         created_at=_now()))
    db.commit()
    try:
        db.execute(
            AgentAuditLog.__table__.delete().where(
                AgentAuditLog.id <= select(
                    AgentAuditLog.id
                ).order_by(AgentAuditLog.id.desc()).offset(AUDIT_KEEP).limit(1).scalar_subquery()
            )
        )
        db.commit()
    except Exception:
        db.rollback()          # 裁剪失败不该影响业务：日志少留几条无所谓
        logging.getLogger(__name__).warning(
            "agent 审计裁剪失败（保留 %d 条的清理未执行）", AUDIT_KEEP, exc_info=True)


def list_audit(db: Session, *, limit: int = 100, offset: int = 0,
               token_id: int | None = None, q: str | None = None) -> tuple[list[dict], int]:
    """审计列表（新的在前）。`q` 匹配凭据名与路径。"""
    cond = []
    if token_id is not None:
        cond.append(AgentAuditLog.token_id == token_id)
    if (q or "").strip():
        from ..db import like_escape
        pat = f"%{like_escape(q.strip())}%"
        cond.append((AgentAuditLog.path.ilike(pat, escape="\\")) |
                    (AgentAuditLog.token_name.ilike(pat, escape="\\")))
    from sqlalchemy import func as _f

    base = select(AgentAuditLog).where(*cond) if cond else select(AgentAuditLog)
    inner = select(AgentAuditLog.id).where(*cond) if cond else select(AgentAuditLog.id)
    count = db.execute(select(_f.count()).select_from(inner.subquery())).scalar() or 0
    rows = db.execute(base.order_by(AgentAuditLog.id.desc())
                      .limit(limit).offset(offset)).scalars().all()
    return [audit_public(r) for r in rows], int(count)


def clear_audit(db: Session, *, token_id: int | None = None,
                operator: str | None = None) -> int:
    """清空审计（可按凭据清）。返回删除行数。

    清除动作本身**落一条哨兵行**（token_id=0 保留值，token_name='audit-clear'）：
    否则管理员可以无痕抹掉全部调用痕迹——审计可以被无痕清除等于没有审计
    。哨兵行不随本次删除消失（删在前、写在后），
    只有下一次「全清」才会把它抹掉并留下新哨兵——链上永远有最近一次清除的记录。
    """
    from sqlalchemy import delete as _delete
    stmt = _delete(AgentAuditLog)
    if token_id is not None:
        stmt = stmt.where(AgentAuditLog.token_id == token_id)
    n = db.execute(stmt).rowcount or 0
    scope = f"token_id={token_id}" if token_id is not None else "全部"
    db.add(AgentAuditLog(
        token_id=0, token_name="audit-clear",
        method="DELETE",
        path=(f"/api/agent-audit 清除审计（{scope}，共 {n} 条；操作人："
              f"{operator or '未知'}）")[:256],
        status=200, duration_ms=0, created_at=_now()))
    db.commit()
    return int(n)


def audit_public(row: AgentAuditLog) -> dict:
    return {
        "id": row.id, "token_id": row.token_id, "token_name": row.token_name,
        "method": row.method, "path": row.path, "status": row.status,
        "duration_ms": row.duration_ms, "created_at": _aware(row.created_at),
    }


__all__ = [
    "AUDIT_KEEP", "PREFIX_LEN", "PLAIN_PREFIX", "agent_public", "audit_public",
    "clear_audit", "create_agent_token", "list_audit", "record_audit",
    "resolve_agent_token", "revoke_agent_token",
]
