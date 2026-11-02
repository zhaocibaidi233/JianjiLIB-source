# -*- coding: utf-8 -*-
"""登录会话与账号服务。

## 登录是**默认关闭**的

判定标准只有一条：`users` 表里有没有人。空表 = 没启用登录，所有接口照旧开放
（单机零配置可用，这是本项目一贯的取舍）；建出第一个管理员的那一刻自动开启。
不用环境变量开关，是因为「配置在环境变量里」和「库里的实际状态」会漂移——
用户换了台机器跑、忘了设变量，权限就静默失效了；而「库里有没有账号」不会骗人。

## 令牌只存哈希

`user_sessions.token_hash` 是明文令牌的 SHA-256（见 `core/security.token_hash`）。
库被拷走也换不出可用的登录态。明文只在两处存在：登录响应体、前端 localStorage。

## last_seen 节流

`last_seen_at` 用于「管理员看谁在线」。每个请求都写一次库会让读接口变成写接口
（还抢行锁），所以只在这条会话距上次记录超过 `_TOUCH_INTERVAL` 时才更新。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..core.perm import (DEFAULT_ROLES, effective_levels, module_for_path,
                         normalize_permissions, required_level, role_can)
from ..core.security import hash_password, new_token, token_hash
from ..models import PERM_WILDCARD, PERM_WRITE, ROLE_ADMIN, Role, User, UserSession

# 会话有效期。单机编目工具，天天用，太短会天天弹登录框；30 天是浏览器
# 「保持登录」的常见量级。可用 JIANJI_SESSION_DAYS 覆盖。
try:
    SESSION_DAYS = max(1, int(os.environ.get("JIANJI_SESSION_DAYS", "30")))
except ValueError:
    SESSION_DAYS = 30

_TOUCH_INTERVAL = timedelta(minutes=5)

# 允许的令牌请求头（按优先级）。`Authorization: Bearer` 是标准写法；
# `X-Auth-Token` 与本项目既有的 X-Access-Token / X-Operator 风格一致，
# 便于 agent / 脚本客户端不带 Authorization 也能用。
AUTH_HEADERS = ("Authorization", "X-Auth-Token")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """把库里取出的时间补上 UTC 时区。

    PG 的 `timestamptz` 经 psycopg2 回来是 aware 的，但**测试用 SQLite / 内存库
    或手工构造的对象**可能给 naive 值，直接和 aware 的 now() 相减会抛
    `TypeError: can't subtract offset-naive and offset-aware datetimes`。
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
#  开关与出厂数据
# ---------------------------------------------------------------------------

def auth_enabled(db: Session) -> bool:
    """只有成功查到空 users 才关闭登录；数据库故障必须向上传播。"""
    return db.execute(select(User.id).limit(1)).first() is not None


# seed_first_admin 的并发互斥键。值任意，但全项目内要唯一（advisory lock 按键全局
# 互斥）；取固定命名空间字符串的 sha1 前 8 字节。
_SETUP_ADVISORY_LOCK_KEY = 0x2BBFA03E


def ensure_default_roles(db: Session, *, commit: bool = True) -> None:
    """补齐出厂角色（幂等）。已存在的**不动**——管理员改过的权限不能被重启覆盖。

    `commit=False` 供 `seed_first_admin` 使用：角色必须在当前事务里可见，但
    事务级 advisory lock 不能在这里提交释放，否则并发 setup 会各建一个管理员。
    """
    existing = {name for (name,) in db.execute(select(Role.name)).all()}
    changed = False
    for spec in DEFAULT_ROLES:
        if spec["name"] in existing:
            continue
        db.add(Role(name=spec["name"], title=spec["title"],
                    permissions=dict(spec["permissions"]),
                    builtin=spec["builtin"], note=spec["note"]))
        changed = True
    if changed:
        if commit:
            db.commit()
        else:
            db.flush()


def admin_role(db: Session) -> Role | None:
    return db.execute(select(Role).where(Role.name == ROLE_ADMIN)).scalar_one_or_none()


def role_by_name(db: Session, name: str) -> Role | None:
    """按**稳定标识**取角色（`roles.name`：`admin` / `editor` / 自建角色名）。

    不认中文 `title`：标题可以随时改，而权限判定只认 `name`。拿中文名去匹配，
    改一次标题就会让绑定悄悄失效。
    """
    key = (name or "").strip()
    if not key:
        return None
    return db.execute(select(Role).where(Role.name == key)).scalar_one_or_none()


# ---------------------------------------------------------------------------
#  会话
# ---------------------------------------------------------------------------

def token_from_request(request) -> str | None:
    """从请求头取令牌。`Authorization: Bearer xxx` 优先，其次 `X-Auth-Token`。"""
    try:
        headers = request.headers
    except Exception:
        return None
    raw = (headers.get("Authorization") or "").strip()
    if raw:
        parts = raw.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip() or None
        # 裸令牌（不带 Bearer 前缀）也接受：手工 curl 调试时少打一个词
        return raw
    return (headers.get("X-Auth-Token") or "").strip() or None


def create_session(db: Session, user: User, client: str | None = None) -> str:
    """新建会话，返回**明文令牌**（唯一一次出现）。"""
    token = new_token()
    now = _now()
    db.add(UserSession(token_hash=token_hash(token), user_id=user.id,
                       created_at=now, expires_at=now + timedelta(days=SESSION_DAYS),
                       last_seen_at=now, client=(client or "")[:256] or None))
    user.last_login_at = now
    db.commit()
    return token


def resolve_session(db: Session, token: str | None) -> User | None:
    """令牌 → 用户。无效 / 过期 / 账号停用一律 None（并顺手清掉过期行）。

    过期行**懒删除**：不做定时清理任务（单机工具没必要引入调度），
    每次遇到一条就删一条，表不会无限长。
    """
    if not token:
        return None
    try:
        row = db.get(UserSession, token_hash(token))
    except Exception:
        return None
    if row is None:
        return None
    now = _now()
    if _aware(row.expires_at) is None or _aware(row.expires_at) <= now:
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.enabled:
        return None
    # last_seen 节流（见模块 docstring）
    if row.last_seen_at is None or now - _aware(row.last_seen_at) > _TOUCH_INTERVAL:
        try:
            row.last_seen_at = now
            # 滑动续期：剩余有效期不足一半时续满整个窗口（30 天）。活跃用户的
            # 会话一直有效、不再有「用到第 29 天突然被踢」；不活跃的会话仍在
            # 最后一次使用后 30 天到期，窗口没有变长。
            remaining = _aware(row.expires_at) - now
            if remaining.total_seconds() < SESSION_DAYS * 86400 / 2:
                row.expires_at = now + timedelta(days=SESSION_DAYS)
            db.commit()
        except Exception:
            db.rollback()
    return user


def destroy_session(db: Session, token: str | None) -> bool:
    if not token:
        return False
    row = db.get(UserSession, token_hash(token))
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def purge_user_sessions(db: Session, user_id: int) -> int:
    """踢掉某账号的全部登录态（停用账号 / 改密码 / 删账号时调）。"""
    rows = db.execute(select(UserSession).where(UserSession.user_id == user_id)).scalars().all()
    for row in rows:
        db.delete(row)
    return len(rows)


# ---------------------------------------------------------------------------
#  序列化
# ---------------------------------------------------------------------------

def role_public(role: Role) -> dict:
    return {
        "id": role.id, "name": role.name, "title": role.title,
        "permissions": role.permissions or {},
        "levels": effective_levels(role.permissions or {}),
        "builtin": bool(role.builtin), "note": role.note,
    }


def user_public(db: Session, user: User) -> dict:
    """账号 + 角色 + **摊平后的模块档位**。

    带上 `levels` 而不是只给原始 `permissions`：前端侧栏要按模块决定显示哪些菜单，
    让前端再实现一遍「通配 / 档位比较」的逻辑必然与后端漂移（两边各写一遍同一套规则）。
    """
    role = db.get(Role, user.role_id)
    perms = (role.permissions or {}) if role is not None else {}
    return {
        "id": user.id, "username": user.username,
        "display_name": user.display_name, "enabled": bool(user.enabled),
        "role_id": user.role_id,
        "role_name": role.name if role is not None else None,
        "role_title": role.title if role is not None else None,
        "permissions": perms,
        "levels": effective_levels(perms),
        "is_admin": bool(role is not None and role_can(perms, "users", PERM_WRITE)),
        "last_login_at": _aware(user.last_login_at),
        "created_at": _aware(user.created_at),
    }


def user_is_admin(db: Session, user: User) -> bool:
    role = db.get(Role, user.role_id)
    if role is None:
        return False
    return role.name == ROLE_ADMIN or role_can(role.permissions or {}, "users", PERM_WRITE)


# ---------------------------------------------------------------------------
#  建号
# ---------------------------------------------------------------------------

def make_user(db: Session, *, username: str, display_name: str, password: str,
              role: Role) -> User:
    """建账号（**不 commit**，调用方决定事务边界）。"""
    user = User(username=username, display_name=display_name,
                password_hash=hash_password(password), role_id=role.id, enabled=True)
    db.add(user)
    return user


def seed_first_admin(db: Session, *, username: str, password: str,
                     display_name: str | None = None) -> User:
    """首次使用：建出厂角色 + 管理员账号。**仅在没有任何账号时可用**。

    原子性：路由层的 `auth_enabled` 检查是 check-then-act，两个并发 setup 都可能
    在空表窗口通过、各建一个管理员（同名有 unique 兜底，**异名没有**）。这里先取
    事务级 advisory 锁把并发 seed 串行化，锁内重查空库——后到的抛 `ValueError`，
    由 API 层转 403。lan_guard 只在局域网形态兜底，单机回环 + 反向代理仍可打
    。
    """
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
               {"k": _SETUP_ADVISORY_LOCK_KEY})
    if auth_enabled(db):
        raise ValueError("已有账号，无法重复初始化，请用管理员账号登录")
    ensure_default_roles(db, commit=False)
    role = admin_role(db)
    if role is None:                                  # 极端情况：admin 角色被删了
        role = Role(name=ROLE_ADMIN, title="管理员",
                    permissions={PERM_WILDCARD: PERM_WRITE}, builtin=True,
                    note="全部模块可读写（含用户管理）。")
        db.add(role)
        db.flush()
    user = make_user(db, username=username, display_name=display_name or username,
                     password=password, role=role)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
#  权限判定（供依赖使用）
# ---------------------------------------------------------------------------

def check_path(db: Session, role_id: int | None, method: str, path: str,
               who: str = "") -> tuple[bool, str]:
    """`(是否放行, 拒绝原因)` —— **人与 agent 共用这一份判定**。

    登录态接口（`/api/auth/*`）**只要求登录、不查模块权限**——否则会出现
    「改自己密码需要用户管理权限」这种死锁（见 core/perm 的 MODULE_AUTH）。

    `who` 只进拒绝文案：人传「当前账号：张三」，agent 传「agent：导入机器人」。

    ## 判定来源：优先路由声明，回退旧前缀表

    2026-10-26 改造：路由自己声明 `@api(module=..., level=...)`，
    判定优先读它。**旧前缀表保留为回退**——索引没建（脚本/测试直接调用）或某条路由
    漏声明时，仍按旧表判，行为与改造前一致。两边一致由 `test_route_declarations`
    与启动检查共同保证。
    """
    from ..core.route_meta import default_level, resolve

    meta = resolve(method, path)
    capability = None
    if meta is not None:
        if meta["public"]:
            return True, ""
        module = meta["module"]
        level = meta["level"] or default_level(method)
        capability = meta.get("capability")
    else:
        module = module_for_path(path)
        if module is None:
            # 未登记模块：登录开启时**拒绝**（默认从严，见 core/perm docstring）
            return False, "该接口未登记访问模块，已被权限系统拒绝（请反馈给维护者）"
        level = required_level(method, path)

    if module is None:
        return False, "该接口未登记访问模块，已被权限系统拒绝（请反馈给维护者）"
    if module == "auth":
        return True, ""
    role = db.get(Role, role_id) if role_id is not None else None
    perms = (role.permissions or {}) if role is not None else {}
    # 声明了具体能力的路由按能力算档位（能力是权限的长期单位，§12.1）；
    # 未声明的仍按 module+level。两者都落到同一套模块档位存储上（v1.0 兼容层）。
    if capability is not None:
        from ..core.capabilities import level_for_capability
        level = level_for_capability(capability)
    if role_can(perms, module, level):
        return True, ""
    from ..core.perm import MODULE_BY_KEY
    title = MODULE_BY_KEY[module].title if module in MODULE_BY_KEY else module
    need = "读写" if level == PERM_WRITE else "查看"
    suffix = f"（{who}）" if who else ""
    return False, f"没有「{title}」的{need}权限{suffix}"


def check_request(db: Session, user: User, method: str, path: str) -> tuple[bool, str]:
    """账号请求的权限判定（``check_path`` 的薄壳）。"""
    return check_path(db, user.role_id, method, path,
                      who=f"当前账号：{user.display_name or user.username}")


__all__ = [
    "AUTH_HEADERS", "SESSION_DAYS", "admin_role", "auth_enabled", "check_path",
    "check_request",
    "create_session", "destroy_session", "ensure_default_roles", "make_user",
    "normalize_permissions", "purge_user_sessions", "resolve_session", "role_by_name",
    "role_public",
    "seed_first_admin", "token_from_request", "user_is_admin", "user_public",
]
