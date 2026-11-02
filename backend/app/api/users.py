# -*- coding: utf-8 -*-
"""用户与角色管理（仅管理员）。

## 三条「自锁」防护

这套接口最容易写出的 bug 是把自己锁在门外。以下三种情况一律 400 拒绝：

  1. **删掉最后一个管理员** / 把它降级成普通角色 / 停用它 —— 之后没人能进用户管理，
     要改回来只能手工改库；
  2. **停用或删除自己** —— 操责任者当场掉线，还得手工改库；
  3. **删掉内置 admin 角色、或改它的权限** —— 它是「全模块通配」这个不变量的载体，
     被改坏之后连恢复入口都没有。

## 角色删除时的账号处理

有账号挂着的角色**不让删**（400 并报出账号数），不做「自动改挂到别的角色」：
静默换角色会让一批人突然多出或少掉权限，而且没人知道发生过。让管理员先手工
把账号调走，是慢一点但看得见的做法。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.perm import normalize_permissions, role_can
from ..core.security import (MAX_DISPLAY_NAME_LEN, hash_password,
                             normalize_username, password_problem)
from ..core import throttle
from ..db import get_db
from ..models import (PERM_WILDCARD, PERM_WRITE, REG_PENDING, REG_STATUSES, Role,
                      RegistrationRequest, ROLE_ADMIN, User)
from ..services import auth as svc
from ..services import registration as reg_svc
from ..core.route_meta import api
from ..schemas import RoleList, UserList
from .auth import current_user

router = APIRouter(tags=["users"])


class UserIn(BaseModel):
    username: str
    display_name: str | None = None
    password: str
    role_id: int
    enabled: bool = True


class UserUpdateIn(BaseModel):
    display_name: str | None = None
    role_id: int | None = None
    enabled: bool | None = None
    # 只有管理员重置别人密码时才用；留空表示不改
    password: str | None = None


class RoleIn(BaseModel):
    name: str
    title: str
    permissions: dict = {}
    note: str | None = None


class RoleUpdateIn(BaseModel):
    title: str | None = None
    permissions: dict | None = None
    note: str | None = None


def require_admin(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    """仅管理员。`/api/users` 与 `/api/roles` 的路径级权限已经拦了一道，
    这里再判一次是为了**语义自明**：读这个文件的人不用去翻 perm.py 才知道谁能用。"""
    if not svc.user_is_admin(db, user):
        raise HTTPException(403, "需要管理员权限")
    return user


def _admin_count(db: Session, exclude_user_id: int | None = None) -> int:
    """当前**可用**的管理员数（角色拥有 users 模块写权限、且账号启用）。"""
    rows = db.execute(select(User.id, User.role_id, User.enabled)).all()
    roles = {r.id: r for r in db.execute(select(Role)).scalars().all()}
    n = 0
    for uid, role_id, enabled in rows:
        if not enabled or uid == exclude_user_id:
            continue
        role = roles.get(role_id)
        if role is not None and role_can(role.permissions or {}, "users", PERM_WRITE):
            n += 1
    return n


def _guard_role_change(db: Session, target: User, new_role: Role, actor: User) -> None:
    """改角色前检查是否会弄丢最后一个管理员。"""
    was_admin = svc.user_is_admin(db, target)
    will_be_admin = role_can(new_role.permissions or {}, "users", PERM_WRITE)
    if was_admin and not will_be_admin and _admin_count(db, exclude_user_id=target.id) == 0:
        raise HTTPException(400, "不能取消最后一个管理员的权限")


# ---------------------------------------------------------------------------
#  注册申请（自助注册的审批队列）
#
#  这一组路由**声明在账号路由之前**：`/users/{user_id}` 里的路径参数没写转换器，
#  任何一段字符串都能匹配上它（然后 int 校验失败返 422，而不是继续找后面的路由）。
#  `/users/modules` 为此挪到了 `/auth/modules`（见 api/auth.py::auth_modules），
#  这里直接按结论摆好，不再踩第二次。
# ---------------------------------------------------------------------------

class RegistrationApproveIn(BaseModel):
    # 批准时才定显示名与角色：申请人自选角色等于自助提权，
    # 而 12 个模块的权限矩阵是管理员的职责范围，不能让公开表单往里面写。
    display_name: str | None = None
    role_id: int


class RegistrationRejectIn(BaseModel):
    reason: str | None = None


class RegistrationSettingIn(BaseModel):
    allow_register: bool


@router.get("/users/registrations")
@api(module="users")
def list_registrations(status: str | None = None,
                       admin: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """审批队列。默认只给**待审**的；`status=all` 或某个具体状态才放宽。

    返回值里单独带一个 `pending` 计数：Users 页那个 tab 标签上的待办数要的永远是
    「全系统还有几条没处理」，不能从已筛选的列表长度里取——管理员翻到「已批准」
    时列表长度是 0，标签就变成「注册申请 0」，那是骗人的。
    """
    if status not in (None, "", "all", *REG_STATUSES):
        raise HTTPException(400, f"status 只能是 all 或 {' / '.join(REG_STATUSES)}")
    stmt = select(RegistrationRequest).order_by(RegistrationRequest.id.desc())
    if status == "all":
        rows = db.execute(stmt).scalars().all()
    else:
        # 缺省与 `status=pending` 同一口径：审批队列默认只看没处理的，
        # 历史台账要管理员显式点 `all`（否则旧申请一多，待办就被淹没）。
        want = status or REG_PENDING
        rows = db.execute(stmt.where(RegistrationRequest.status == want)).scalars().all()
    pending = db.execute(
        select(func.count()).select_from(RegistrationRequest)
        .where(RegistrationRequest.status == REG_PENDING)
    ).scalar_one()
    return {"items": [reg_svc.public_view(r) for r in rows], "pending": int(pending)}


@router.get("/users/registrations/settings")
@api(module="users")
def get_registration_setting(admin: User = Depends(require_admin),
                             db: Session = Depends(get_db)):
    return {"allow_register": reg_svc.allow_register(db)}


@router.put("/users/registrations/settings")
@api(module="users")
def put_registration_setting(payload: RegistrationSettingIn,
                             admin: User = Depends(require_admin),
                             db: Session = Depends(get_db)):
    """开/关自助注册。**默认关**，而且关掉之后已有的申请单仍可在队列里处理完。"""
    reg_svc.set_allow_register(db, payload.allow_register)
    return {"allow_register": reg_svc.allow_register(db),
            "message": "自助注册已开放" if payload.allow_register else "自助注册已关闭"}


@router.post("/users/registrations/{reg_id}/approve")
@api(module="users")
def approve_registration(reg_id: int, payload: RegistrationApproveIn,
                         admin: User = Depends(require_admin),
                         db: Session = Depends(get_db)):
    """批准：建账号（口令沿用申请人自己设的那份，哈希直接搬过去）。"""
    req = db.get(RegistrationRequest, reg_id)
    if req is None:
        raise HTTPException(404, "申请不存在")
    if req.status != REG_PENDING:
        raise HTTPException(409, "该申请已处理，不能重复批准")
    if req.password_hash is None:
        # 待审行一定有哈希（submit 时就写了）。走到这里只可能是手工改过库，
        # 那就绝不能放行：没有哈希的账号能建出来，但永远登不进去。
        raise HTTPException(400, "该申请没有口令记录，请让对方重新提交")
    role = db.get(Role, payload.role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")
    if reg_svc.username_taken(db, req.username):
        # 申请挂着的那几天里，管理员可能已经手工建过同名账号。
        raise HTTPException(409, f"账号「{req.username}」已存在，请换一个")
    display = (payload.display_name or "").strip() or req.username
    if len(display) > MAX_DISPLAY_NAME_LEN:
        raise HTTPException(400, f"姓名不能超过 {MAX_DISPLAY_NAME_LEN} 字符")
    user = reg_svc.approve(db, req, display_name=display, role=role, actor=admin)
    return {"user": svc.user_public(db, user),
            "message": f"已批准「{user.username}」，该账号现在可以登录"}


@router.post("/users/registrations/{reg_id}/reject")
@api(module="users")
def reject_registration(reg_id: int, payload: RegistrationRejectIn,
                        admin: User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    """驳回。原因写给管理员自己看（队列里没有第二个人），可空。"""
    req = db.get(RegistrationRequest, reg_id)
    if req is None:
        raise HTTPException(404, "申请不存在")
    if req.status != REG_PENDING:
        raise HTTPException(409, "该申请已处理")
    reason = (payload.reason or "").strip() or None
    if reason is not None and len(reason) > reg_svc.MAX_REASON_LEN:
        raise HTTPException(
            400, f"驳回原因不能超过 {reg_svc.MAX_REASON_LEN} 字符")
    row = reg_svc.reject(db, req, reason=reason, actor=admin)
    return {"item": reg_svc.public_view(row), "message": "已驳回"}


# ---------------------------------------------------------------------------
#  账号
# ---------------------------------------------------------------------------

@router.get("/users", response_model=UserList)
@api(module="users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(User).order_by(User.id)).scalars().all()
    return {"items": [svc.user_public(db, u) for u in rows]}


@router.post("/users")
@api(module="users")
def create_user(payload: UserIn, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    username = normalize_username(payload.username)
    if username is None:
        raise HTTPException(400, "账号不能为空、不能含空格，且不超过 64 字符")
    exists = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(409, f"用户名「{username}」已存在")
    problem = password_problem(payload.password)
    if problem:
        raise HTTPException(400, problem)
    role = db.get(Role, payload.role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")
    display = (payload.display_name or "").strip() or username
    if len(display) > MAX_DISPLAY_NAME_LEN:
        raise HTTPException(400, f"姓名不能超过 {MAX_DISPLAY_NAME_LEN} 字符")
    # scrypt 闸门（同步侧）：建号要跑一次 16 MiB 的 scrypt，与其他口令端点共用
    # 同一道闸。
    if not throttle.acquire_verify_slot_sync():
        raise HTTPException(429, "请求过多，请稍后重试", headers={"Retry-After": "5"})
    try:
        user = User(username=username, display_name=display,
                    password_hash=hash_password(payload.password),
                    role_id=role.id, enabled=bool(payload.enabled))
        db.add(user)
        db.commit()
    finally:
        throttle.release_verify_slot_sync()
    db.refresh(user)
    return svc.user_public(db, user)


@router.put("/users/{user_id}")
@api(module="users")
def update_user(user_id: int, payload: UserUpdateIn,
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账号不存在")

    if payload.role_id is not None and payload.role_id != user.role_id:
        role = db.get(Role, payload.role_id)
        if role is None:
            raise HTTPException(404, "角色不存在")
        _guard_role_change(db, user, role, admin)
        user.role_id = role.id

    if payload.enabled is not None and bool(payload.enabled) != bool(user.enabled):
        if user.id == admin.id and not payload.enabled:
            raise HTTPException(400, "不能停用当前登录的账号")
        if not payload.enabled and svc.user_is_admin(db, user) \
                and _admin_count(db, exclude_user_id=user.id) == 0:
            raise HTTPException(400, "不能停用最后一个管理员")
        user.enabled = bool(payload.enabled)
        if not user.enabled:
            # 停用即刻生效：把已发出的登录态一起收掉，否则旧令牌还能继续用到过期
            svc.purge_user_sessions(db, user.id)

    if payload.display_name is not None:
        display = payload.display_name.strip() or user.username
        if len(display) > MAX_DISPLAY_NAME_LEN:
            raise HTTPException(400, f"姓名不能超过 {MAX_DISPLAY_NAME_LEN} 字符")
        user.display_name = display

    if payload.password:
        problem = password_problem(payload.password)
        if problem:
            raise HTTPException(400, problem)
        if not throttle.acquire_verify_slot_sync():
            raise HTTPException(429, "请求过多，请稍后重试", headers={"Retry-After": "5"})
        try:
            user.password_hash = hash_password(payload.password)
        finally:
            throttle.release_verify_slot_sync()
        svc.purge_user_sessions(db, user.id)   # 重置密码 = 踢掉该账号所有登录态

    db.commit()
    db.refresh(user)
    return svc.user_public(db, user)


@router.delete("/users/{user_id}")
@api(module="users")
def delete_user(user_id: int, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账号不存在")
    if user.id == admin.id:
        raise HTTPException(400, "不能删除当前登录的账号")
    if svc.user_is_admin(db, user) and _admin_count(db, exclude_user_id=user.id) == 0:
        raise HTTPException(400, "不能删除最后一个管理员")
    svc.purge_user_sessions(db, user.id)
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/kick")
@api(module="users")
def kick_user(user_id: int, admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    """踢下线（清掉该账号的全部登录态，账号本身保留）。

    与「停用」的区别：停用是永久禁止登录，踢下线只是让当前的登录失效
    （比如怀疑某个令牌泄露、或想强制对方重新登录）。
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账号不存在")
    n = svc.purge_user_sessions(db, user.id)
    db.commit()
    return {"ok": True, "removed": n}


# ---------------------------------------------------------------------------
#  角色
# ---------------------------------------------------------------------------

@router.get("/roles", response_model=RoleList)
@api(module="users")
def list_roles(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Role).order_by(Role.id)).scalars().all()
    counts = dict(db.execute(select(User.role_id, func.count(User.id))
                             .group_by(User.role_id)).all())
    return {"items": [{**svc.role_public(r), "user_count": counts.get(r.id, 0)}
                      for r in rows]}


@router.post("/roles")
@api(module="users")
def create_role(payload: RoleIn, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    name = (payload.name or "").strip()
    if not name or len(name) > 64:
        raise HTTPException(400, "角色标识不能为空且不超过 64 字符")
    if any(c.isspace() for c in name):
        raise HTTPException(400, "角色标识不能含空格")
    title = (payload.title or "").strip()
    if not title or len(title) > 64:
        raise HTTPException(400, "角色名称不能为空且不超过 64 字符")
    if db.execute(select(Role).where(Role.name == name)).scalar_one_or_none() is not None:
        raise HTTPException(409, f"角色标识「{name}」已存在")
    role = Role(name=name, title=title,
                permissions=normalize_permissions(payload.permissions),
                builtin=False, note=(payload.note or "").strip() or None)
    db.add(role)
    db.commit()
    db.refresh(role)
    return svc.role_public(role)


@router.put("/roles/{role_id}")
@api(module="users")
def update_role(role_id: int, payload: RoleUpdateIn,
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")

    if payload.permissions is not None:
        perms = normalize_permissions(payload.permissions)
        if role.name == ROLE_ADMIN:
            # admin 必须保持「全模块通配」。允许把它改窄 = 允许把唯一的管理入口焊死。
            if not role_can(perms, "users", PERM_WRITE) or perms.get(PERM_WILDCARD) != PERM_WRITE:
                raise HTTPException(400, "管理员角色的权限不可修改（须保持全部模块可写）")
        else:
            # 改权限可能让最后一个管理员失去用户管理权：本角色下**启用**的账号
            # 都会一起失去，所以要减掉它们再看还剩几个管理员。
            if role_can(role.permissions or {}, "users", PERM_WRITE) \
                    and not role_can(perms, "users", PERM_WRITE) \
                    and _admin_count(db) - _enabled_role_users(db, role.id) <= 0:
                raise HTTPException(400, "不能取消最后一个管理员的权限")
            role.permissions = perms

    if payload.title is not None:
        title = payload.title.strip()
        if not title or len(title) > 64:
            raise HTTPException(400, "角色名称不能为空且不超过 64 字符")
        role.title = title
    if payload.note is not None:
        role.note = payload.note.strip() or None

    db.commit()
    db.refresh(role)
    return svc.role_public(role)


@router.delete("/roles/{role_id}")
@api(module="users")
def delete_role(role_id: int, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")
    if role.name == ROLE_ADMIN:
        raise HTTPException(400, "内置管理员角色不能删除")
    n = _count_role_users(db, role.id)
    if n:
        raise HTTPException(400, f"该角色下还有 {n} 个账号，请先改到其他角色")
    db.delete(role)
    db.commit()
    return {"ok": True}


def _count_role_users(db: Session, role_id: int) -> int:
    return db.execute(select(func.count(User.id))
                      .where(User.role_id == role_id)).scalar() or 0


def _enabled_role_users(db: Session, role_id: int) -> int:
    """该角色下**启用**的账号数（停用的账号不占管理入口，改权限不影响它们）。"""
    return db.execute(select(func.count(User.id))
                      .where(User.role_id == role_id, User.enabled.is_(True))).scalar() or 0
