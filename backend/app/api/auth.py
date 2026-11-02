# -*- coding: utf-8 -*-
"""登录、登出、当前账号、改密码。

## 哪些接口**不需要登录**

公开的：`/api/auth/status`（前端启动问一次）、`/api/auth/login`、
`/api/auth/setup`（第一次建管理员，有账号后自锁）、`/api/auth/modules`、
`/api/auth/logout`（幂等，见下面）、`/api/auth/register`（自助注册申请，
需管理员在开关里打开）。白名单在 `main.py::PUBLIC_AUTH_PATHS`，
**新增公开接口必须同时改那一行**，否则中间件先把人拦在 401。

其余 `/api/auth/*`（`me` / `password`）要求已登录。

## 登录是默认关闭的（见 services/auth.py）

`users` 表为空 → `enabled=false` → 前端不弹登录框、后端不校验，单机零配置照旧。
建出第一个管理员后自动开启。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..core import throttle
from ..core.perm import modules_catalog
from ..core.security import (MAX_DISPLAY_NAME_LEN, MAX_PASSWORD_LEN,
                             hash_password, normalize_username, password_problem,
                             verify_password)
from ..db import get_db
from ..models import User
from ..services import auth as svc
from ..services import registration as reg_svc
from ..core.route_meta import api

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=MAX_PASSWORD_LEN)


class RegisterIn(BaseModel):
    username: str
    password: str
    note: str | None = None


class SetupIn(BaseModel):
    username: str
    password: str
    display_name: str | None = None


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """取当前登录用户；未登录 / 登录未启用时 401。

    中间件已经把解析结果挂在 `request.state` 上（避免每个接口再查一次库）；
    这里只做「有没有」的判定。中间件与依赖**共用同一个 db 会话**吗？不共用——
    中间件用的是自己开的会话，但两者都从连接池取，且用户对象是 detached 的，
    所以这里按 id 重新取一次当前会话里的实例（跨会话传 ORM 对象会在 commit 后
    拿到过期属性）。
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, "未登录或登录已过期，请重新登录")
    user = db.get(User, user_id)
    if user is None or not user.enabled:
        raise HTTPException(401, "账号不存在或已停用")
    return user


@router.get("/status")
@api(public=True)
def auth_status(request: Request, db: Session = Depends(get_db)):
    """登录状态自述（公开）。

    前端启动时调它一次：
      - `enabled=false` → 登录未启用，直接用（侧栏全开、不显示账号区）；
      - `enabled=true, authenticated=false` → 跳登录页；
      - `needs_setup=true` → 还没有任何账号，显示「创建管理员」表单；
      - `registration_open=true` → 登录页才给「申请注册」入口（= 已启用登录
        **且**管理员打开了开关；两者缺一不可，见 services/registration.py）。
    """
    enabled = svc.auth_enabled(db)
    user_id = getattr(request.state, "user_id", None)
    user = db.get(User, user_id) if user_id is not None else None
    authenticated = bool(enabled and user is not None and user.enabled)
    return {
        "enabled": enabled,
        "needs_setup": not enabled,
        "authenticated": authenticated,
        "user": svc.user_public(db, user) if authenticated else None,
        "session_days": svc.SESSION_DAYS,
        "registration_open": bool(enabled and reg_svc.allow_register(db)),
    }


@router.get("/modules")
@api(public=True)
def auth_modules():
    """模块清单（公开）：给用户管理页渲染权限勾选框用。

    放在 `/auth` 下而不是 `/users/modules`，是为了绕开**路由注册顺序**这个坑——
    `/users/{user_id}` 会把 `/users/modules` 抢先匹配掉（路径参数没带类型转换器时
    任何字符串都能匹配，然后校验失败返 422，而不是继续找后面的路由）。
    路径参数路由会抢先匹配静态段，这类坑只需要踩一次。
    """
    return {"modules": modules_catalog()}


@router.post("/setup")
@api(public=True)
def auth_setup(payload: SetupIn, request: Request, db: Session = Depends(get_db)):
    """首次使用：创建管理员账号（**只在没有任何账号时可用**）。

    这条接口不需要登录（否则就成了「要登录才能创建登录账号」的死锁），
    但一旦存在任何账号就永久关闭（403），所以不存在被人抢注的风险。

    scrypt 闸门：建号要跑一次 16 MiB 的 scrypt，本路由是同步的（线程池），拿不到
    事件循环，用 threading 侧闸门（`throttle.acquire_verify_slot_sync`）——匿名
    并发打 setup 不再把内存打满。
    """
    if svc.auth_enabled(db):
        raise HTTPException(403, "已有账号，无法重复初始化，请用管理员账号登录")
    username = normalize_username(payload.username)
    if username is None:
        raise HTTPException(400, "账号不能为空、不能含空格，且不超过 64 字符")
    problem = password_problem(payload.password)
    if problem:
        raise HTTPException(400, problem)
    display = (payload.display_name or "").strip() or username
    if len(display) > MAX_DISPLAY_NAME_LEN:
        raise HTTPException(400, f"姓名不能超过 {MAX_DISPLAY_NAME_LEN} 字符")
    if not throttle.acquire_verify_slot_sync():
        raise HTTPException(429, "请求过多，请稍后重试", headers={"Retry-After": "5"})
    try:
        try:
            user = svc.seed_first_admin(db, username=username,
                                        password=payload.password,
                                        display_name=display)
        except ValueError as e:
            # 并发窗口里别人先建好了号（seed_first_admin 锁内重查空库）
            db.rollback()
            raise HTTPException(403, str(e))
    finally:
        throttle.release_verify_slot_sync()
    token = svc.create_session(db, user, client=request_client(request))
    invalidate_auth_cache()
    return {"token": token, "user": svc.user_public(db, user),
            "message": "管理员账号已创建，登录功能已开启"}


@router.post("/register")
@api(public=True)
def auth_register(payload: RegisterIn, db: Session = Depends(get_db)):
    """自助注册：只**提交申请**，不产生账号。要管理员在用户管理里批准才能登录。

    这是一条公开接口，所以三道门都得在这里关死：

      1. 开关没开 / 系统还没有任何账号 → 403。后半个条件不是冗余，它挡的是
         「第一份申请被批准 → 系统当场变成必须登录，而唯一的新账号没有管理员
         角色 → 谁也都进不了用户管理」这个死锁（详见 services/registration.py）。
      2. 用户名与口令的规则**与建管理员完全一致**（同一组函数），不另立标准。
      3. 账号名已被占用 / 已有待审申请 → 统一一句「该账号名不可用」。
         `login` 那边刻意不区分「用户不存在」与「密码错误」，就是为了不白送一个
         账号枚举接口；这里回一句「该用户名已存在」会把同样的信息泄出去。
    """
    if not reg_svc.registration_open(db):
        raise HTTPException(403, "当前未开放自助注册，请联系管理员开通账号")
    username = normalize_username(payload.username)
    if username is None:
        raise HTTPException(400, "账号不能为空、不能含空格，且不超过 64 字符")
    problem = password_problem(payload.password)
    if problem:
        raise HTTPException(400, problem)
    note = (payload.note or "").strip() or None
    if note is not None and len(note) > reg_svc.MAX_NOTE_LEN:
        raise HTTPException(400, f"备注不能超过 {reg_svc.MAX_NOTE_LEN} 字符")
    if reg_svc.username_taken(db, username) or reg_svc.pending_of(db, username) is not None:
        raise HTTPException(400, "该账号名不可用，请换一个再提交")
    # scrypt 闸门（同步侧）：注册是公开接口，hash_password 的 16 MiB 开销不该被
    # 并发打满（与 /setup 同一道闸，见 throttle.acquire_verify_slot_sync 注释）。
    if not throttle.acquire_verify_slot_sync():
        raise HTTPException(429, "请求过多，请稍后重试", headers={"Retry-After": "5"})
    try:
        row = reg_svc.submit(db, username=username,
                             password_hash=hash_password(payload.password), note=note)
    finally:
        throttle.release_verify_slot_sync()
    return {"ok": True, "id": row.id,
            "message": "申请已提交，管理员批准后可用该账号和密码登录"}


def _login_blocking(db: Session, request: Request, username: str | None,
                    password: str, throttle_key: str) -> dict:
    """登录的**阻塞**部分：查库 + scrypt + 签发会话。跑在线程池里。

    拆出来的唯一原因见 `auth_login`：闸门要在事件循环上排队，所以路由必须是
    async；而查库与 scrypt 都是阻塞调用，必须离开事件循环。这段代码本身与
    改造前逐行等价。
    """
    user = None
    if username is not None:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    bad = HTTPException(401, "账号或密码不正确")
    if user is None or not user.enabled:
        # 仍然跑一次哈希（verify_password 内部用哑哈希补齐成本）
        verify_password(password, None)
        _record_login_failure(throttle_key)
        raise bad
    if not verify_password(password, user.password_hash):
        _record_login_failure(throttle_key)
        raise bad

    throttle.LOGIN_BY_USER.reset(throttle_key)
    token = svc.create_session(db, user, client=request_client(request))
    return {"token": token, "user": svc.user_public(db, user)}


@router.post("/login")
@api(public=True)
async def auth_login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    """登录。账号不存在 / 密码不对 / 已停用 → **统一** 401 文案。

    不区分「用户不存在」与「密码错误」：那等于免费提供一个账号枚举接口
    （先试出哪些用户名存在，再针对性爆破）。**但只统一文案是不够的**——
    真正的枚举通道是响应时间：账号不存在时若不跑哈希，会比密码错误快几个
    数量级。所以 `verify_password` 在 `stored` 为空时会用固定哑哈希跑满一次
    完整校验（见 core/security.py），成本与真实校验对齐。

    限速见 core/throttle.py：按账号 15 分钟 5 次。**不按来源**——局域网形态
    下前端是代理，后端看到的来源恒为 127.0.0.1，按来源计数会把全校锁死。

    ## 为什么这条路由是 async

    同步路由跑在 Starlette 的线程池里（默认 40 个）。如果在线程里等并发闸门，
    等闸门的请求**同样占着线程**——40 个并发登录就能把线程全堵住，`/api/health`
    这种无关接口排在后面被拖到 6 秒。改成 async 后排队发生在事件循环上，不占
    线程；阻塞的查库与 scrypt 交给 `run_in_threadpool`。
    """
    username = normalize_username(payload.username)
    throttle_key = username or "-"

    wait = throttle.LOGIN_BY_USER.retry_after(throttle_key)
    if wait > 0:
        raise HTTPException(429, f"尝试过于频繁，请 {wait} 秒后再试",
                            headers={"Retry-After": str(wait)})

    if not await throttle.acquire_verify_slot():
        raise HTTPException(429, "登录请求过多，请稍后重试",
                            headers={"Retry-After": "5"})
    try:
        return await run_in_threadpool(_login_blocking, db, request, username,
                                       payload.password, throttle_key)
    finally:
        throttle.release_verify_slot()



@router.post("/logout")
@api(public=True)
def auth_logout(request: Request, db: Session = Depends(get_db)):
    """登出：删掉这条会话。没带令牌也返 200（幂等，前端不必判断）。"""
    svc.destroy_session(db, svc.token_from_request(request))
    return {"ok": True}


@router.get("/me")
@api(module="auth")
def auth_me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """当前账号（含摊平后的模块权限，前端侧栏按它过滤菜单）。"""
    return svc.user_public(db, user)


@router.post("/password")
@api(module="auth")
def auth_change_password(payload: PasswordIn, request: Request,
                         user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    """改自己的密码。要验旧密码——否则拿到一个没锁屏的浏览器就能改掉密码。

    改完**踢掉该账号其它会话**（`purge_user_sessions`），但保留当前这条：
    密码可能就是因为在别处泄露才改的，旧会话必须失效。

    旧密码校验与新密码哈希各跑一次 scrypt，同样走同步侧并发闸门——已登录的低权限
    账号连环改密不应能把内存打满。
    """
    if not throttle.acquire_verify_slot_sync():
        raise HTTPException(429, "请求过多，请稍后重试", headers={"Retry-After": "5"})
    try:
        if not verify_password(payload.old_password, user.password_hash):
            raise HTTPException(400, "原密码不正确")
        problem = password_problem(payload.new_password)
        if problem:
            raise HTTPException(400, problem)
        if payload.new_password == payload.old_password:
            raise HTTPException(400, "新密码不能与原密码相同")
        from ..core.security import hash_password
        user.password_hash = hash_password(payload.new_password)
    finally:
        throttle.release_verify_slot_sync()
    svc.purge_user_sessions(db, user.id)
    db.commit()
    # 当前这条也一起没了 → 重新发一条，前端换掉本地令牌即可继续用
    token = svc.create_session(db, user, client=request_client(request))
    return {"ok": True, "token": token, "message": "密码已修改"}


def request_client(request: Request) -> str | None:
    try:
        return (request.headers.get("User-Agent") or "")[:256] or None
    except Exception:
        return None


def _record_login_failure(key: str) -> None:
    """记一次登录失败；跨过阈值的那一刻打一条日志（只打一次，不刷屏）。

    现场排障时，「馆员说登不上」十有八九能在这条日志里直接看到答案。
    """
    was_locked = throttle.LOGIN_BY_USER.retry_after(key) > 0
    throttle.LOGIN_BY_USER.fail(key)
    if not was_locked and throttle.LOGIN_BY_USER.retry_after(key) > 0:
        log.warning("[auth] 账号 %s 连续登录失败已达上限，临时锁定", key)


# ---------------------------------------------------------------------------
#  中间件用的开关缓存
# ---------------------------------------------------------------------------
#
# 「有没有账号」每个请求都要问一次。直接查库是阻塞调用，而中间件跑在事件循环里，
# 每条请求都同步查一次库会卡住并发（虽然是单机工具，但没必要留这个雷）。
# 缓存 5 秒 + 账号变动时显式失效：既避免频繁查库，又不会出现「刚建完管理员，
# 刷新还是登录未启用」的困惑。

_ENABLED_CACHE: dict[str, object] = {"value": None, "at": 0.0}
_ENABLED_TTL = 5.0


def auth_enabled_cached(db: Session) -> bool:
    import time
    now = time.monotonic()
    value = _ENABLED_CACHE["value"]
    if value is not None and now - float(_ENABLED_CACHE["at"]) < _ENABLED_TTL:
        return bool(value)
    # 查询异常向上传播，不写入缓存，也不刷新旧值的时间戳。
    fresh = svc.auth_enabled(db)
    _ENABLED_CACHE["value"] = fresh
    _ENABLED_CACHE["at"] = time.monotonic()
    return fresh


def invalidate_auth_cache() -> None:
    _ENABLED_CACHE["value"] = None
    _ENABLED_CACHE["at"] = 0.0
