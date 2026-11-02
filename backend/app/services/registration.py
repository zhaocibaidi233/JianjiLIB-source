# -*- coding: utf-8 -*-
"""自助注册申请与管理员审批（的延伸）。

## 开关默认**关**

`settings` 表 `key="auth"` 里的 `allow_register`。缺行 / 缺键 / 存的不是布尔
⇒ 一律当作关。方向必须是"默认关"：一个能把账号密码提交进去的公开接口，
不该在管理员不知道的情况下就开着——尤其是这套系统本来就设计成单机零配置可用。

## 注册只在「登录已启用」时可用

`auth_enabled()` = users 表有没有人。没人的时候开注册是**有害**的：
第一份申请被批准 → 系统当场进入"必须登录"，而申请人拿不到管理员角色，
于是全系统只剩一个没权限的账号，谁也无法再进用户管理（只能手工改库）。
所以 `registration_open()` 里那条 `auth_enabled` 判据不是冗余的开关叠开关，
它挡的是「注册把人带进一个没有管理员的死锁」。

## 申请单不是账号

批准那一刻才 `make_user`。详见 `models.RegistrationRequest` 的 docstring
（为什么不给 `users` 加 `status` 列）。

## 口令哈希进终态即清空

申请人自己设口令是**唯一可行**的流程——本系统没有邮件/短信通道，管理员没法把
临时密码交到对方手上。但哈希只该活在 pending 期间：批准时搬进 `users`，
驳回时直接抹掉。申请单要长期留着当台账，没道理让永远不会再用到的哈希躺在库里。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (REG_APPROVED, REG_PENDING, REG_REJECTED, RegistrationRequest,
                      Role, Setting, User)
from . import auth as svc
from .settings import _coerce_bool

# 设置行的 key 与其中的开关键。与 clc 的 `{"auto_register": bool}` 同一形态：
# 模块自带一个开关端点，而不是挤进 `api/ai.py` 那份 `_ALLOWED_SETTING_KEYS` 白名单。
SETTING_KEY = "auth"
ALLOW_KEY = "allow_register"

MAX_NOTE_LEN = 256          # 与 RegistrationRequest.note 列宽一致
MAX_REASON_LEN = 256


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
#  开关
# ---------------------------------------------------------------------------

def allow_register(db: Session) -> bool:
    """管理员是否打开了「允许自助注册」。任何异常/缺行都返回 False（默认关）。"""
    try:
        row = db.get(Setting, SETTING_KEY)
    except Exception:
        return False
    value = row.value if row is not None else None
    if not isinstance(value, dict):
        return False
    return _coerce_bool(value.get(ALLOW_KEY), False)


def set_allow_register(db: Session, enabled: bool) -> None:
    """写开关（只改这一个键，同一行里将来的其它 auth 设置不动）。"""
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        db.add(Setting(key=SETTING_KEY, value={ALLOW_KEY: bool(enabled)}))
    else:
        row.value = {**(row.value or {}), ALLOW_KEY: bool(enabled)}
    db.commit()


def registration_open(db: Session) -> bool:
    """对外的唯一判据：登录已启用 **且** 开关开着。见模块 docstring。"""
    return svc.auth_enabled(db) and allow_register(db)


# ---------------------------------------------------------------------------
#  提交
# ---------------------------------------------------------------------------

def username_taken(db: Session, username: str) -> bool:
    return db.execute(select(User.id).where(User.username == username)).first() is not None


def pending_of(db: Session, username: str) -> RegistrationRequest | None:
    return db.execute(
        select(RegistrationRequest)
        .where(RegistrationRequest.username == username,
               RegistrationRequest.status == REG_PENDING)
    ).scalar_one_or_none()


def submit(db: Session, *, username: str, password_hash: str,
           note: str | None) -> RegistrationRequest:
    """落一条待审申请（校验在 API 层做，与 `api/auth.py::auth_setup` 同一分工）。"""
    row = RegistrationRequest(username=username, password_hash=password_hash,
                              note=note, status=REG_PENDING)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
#  审批
# ---------------------------------------------------------------------------

def approve(db: Session, req: RegistrationRequest, *, display_name: str,
            role: Role, actor: User) -> User:
    """批准：建账号、申请单进终态并抹掉哈希。

    调用方需保证 `req.status == pending`、`role` 存在、`display_name` 合法。
    一次性 commit：账号与申请单状态必须同生同死——分两次提交会出现
    「账号建好了但申请单还是 pending」，于是同一个人能被再批准一次、建出两个号。

    口令**直接搬**申请单里的哈希（与 `api/users.py::create_user` 一样直接构造
    `User`，不走 `svc.make_user`）：不重算就省一次 scrypt（n=2^14 是几十毫秒级的
    同步开销），而且「批准后即可用」这件事和申请人当初设的口令是同一份数据，
    不存在两边算出不同哈希的空间。
    """
    user = User(username=req.username, display_name=display_name,
                password_hash=req.password_hash, role_id=role.id, enabled=True)
    db.add(user)
    req.status = REG_APPROVED
    req.password_hash = None
    req.reject_reason = None
    req.reviewed_by = actor.display_name or actor.username
    req.reviewed_at = _now()
    db.commit()
    db.refresh(user)
    return user


def reject(db: Session, req: RegistrationRequest, *, reason: str | None,
           actor: User) -> RegistrationRequest:
    req.status = REG_REJECTED
    req.password_hash = None
    req.reject_reason = reason
    req.reviewed_by = actor.display_name or actor.username
    req.reviewed_at = _now()
    db.commit()
    db.refresh(req)
    return req


def public_view(req: RegistrationRequest) -> dict:
    """给管理列表用的一行。**绝不含 password_hash**——它在这一层就该被挡在门外，
    而不是靠前端不显示。"""
    return {
        "id": req.id,
        "username": req.username,
        "note": req.note,
        "status": req.status,
        "reject_reason": req.reject_reason,
        "reviewed_by": req.reviewed_by,
        "reviewed_at": req.reviewed_at,
        "created_at": req.created_at,
    }


__all__ = [
    "ALLOW_KEY", "MAX_NOTE_LEN", "MAX_REASON_LEN", "SETTING_KEY",
    "allow_register", "approve", "pending_of", "public_view", "registration_open",
    "reject",
    "set_allow_register", "submit", "username_taken",
]
