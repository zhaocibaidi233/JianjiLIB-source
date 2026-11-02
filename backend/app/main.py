# -*- coding: utf-8 -*-
"""简籍（Jianji）后端 API。启动: uvicorn app.main:app --port 8000

安全约定：
- CORS 默认只放行本机（localhost/127.0.0.1）；局域网或域名访问需用
  JIANJI_CORS_ORIGINS 显式列出允许的来源。
- 单机首次初始化时按本机零配置流程运行；创建管理员后按角色的模块权限校验。
- 设置 JIANJI_ACCESS_TOKEN 后启用共享密钥校验，请求需带 X-Access-Token 头。
- 共享密钥与登录权限二者任一通过即可；共享密钥适合受控的脚本或外部集成。
"""
from __future__ import annotations

import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .api import api_router

# 应用日志最小配置：uvicorn 只配置它自己的 logger，root 无人接管——不做这一步，
# 下面的 warning 要么无时间戳裸输出、要么（INFO 级）直接被吞。launcher-logs/
# 收的是进程 stdout/stderr，logging 默认写 stderr，一样能被启动器收到。
# 之前用裸 print 的另一层代价：没有级别、没有时间、没有来源，靠消息里的
# [oplog]/[audit] 手工前缀辨认——现在格式统一由这里给。
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s：%(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# 无需鉴权的端点：探活与接口自述
PUBLIC_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}

# 登录相关里**不要求已登录**的端点（见 api/auth.py 模块 docstring）。
# 注意 `/api/auth/me` `/api/auth/password` **不在**这里——它们必须有身份。
#
# `/api/auth/logout` 在列表里：登出永远应该成功。会话已经没了 / 令牌无效 /
# 压根没带令牌，结果都是「你已经是登出状态」，正是调用者想要的。若让中间件
# 先拦成 401，前端那个「尽力登出、无论如何清本地」的调用就得吞一个错误，
# 也违背了 auth_logout 自己写明的「幂等返 200」。
PUBLIC_AUTH_PATHS = {"/api/auth/status", "/api/auth/login", "/api/auth/setup",
                     "/api/auth/modules", "/api/auth/logout",
                     # 自助注册申请（审批前不产生账号，见 services/registration.py）。
                     # 这条**必须**同步加进集合，否则中间件先把匿名请求拦成 401，
                     # 登录页那个「申请注册」入口点就是死的。
                     "/api/auth/register"}

ACCESS_TOKEN = os.environ.get("JIANJI_ACCESS_TOKEN", "").strip()

#: 共享密钥绑定的角色标识（`roles.name`）。留空 = 不绑定，保持「全权」的老行为。
#:
#: 为什么给这个开关：共享密钥原本跳过**全部**模块权限校验——一把钥匙能读能写
#: 能删，且不区分模块。给脚本用当然方便，但它一旦泄露，损失是整站级的。绑定
#: 一个角色后，它最多只有该角色的权限（比如编目员：能编目，不能管用户）。
ACCESS_TOKEN_ROLE = os.environ.get("JIANJI_ACCESS_TOKEN_ROLE", "").strip()

#: 共享密钥在操作日志里的署名。写死成固定字符串，是为了让操作日志与工作量统计里
#: 「用接口凭据改的」一眼可辨，而不是混进某个馆员的名字里（见 _mark_shared_token）。
SHARED_TOKEN_OPERATOR = "接口凭据(共享密钥)"

#: 共享密钥在审计表里显示的名字（`agent_audit_logs.token_name`）。
SHARED_TOKEN_NAME = "共享密钥"

#: 共享密钥审计行的 `token_id`。真凭据的主键从 1 起，0 是保留哨兵；
#: 这一列本来就没有外键（凭据可删、日志要留），所以塞 0 不会破坏约束。
SHARED_TOKEN_AUDIT_ID = 0

# 局域网形态下，管理员账号建出来之前只放行这两条：前端要靠它们渲染
# 「请先在本机创建管理员」的提示，其余一律挡住（见 AuthMiddleware._lan_guard）。
_LAN_BOOTSTRAP_ALLOWED = {"/api/health", "/api/auth/status"}

# 接口文档页不以 /api 开头，但会把全部路由与参数结构摊开。空库窗口期一并挡住，
# 否则「除探活与状态外一律 403」这句话就不成立（文档页仍可读）。
_LAN_DOC_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}



class AccessTokenMiddleware(BaseHTTPMiddleware):
    """可选共享密钥鉴权。

    仅在 JIANJI_ACCESS_TOKEN 非空时生效——默认不设，保持单机零配置可用。

    ⚠️ 本中间件与 `AuthMiddleware` 都认 `X-Access-Token`，但**本中间件跑在
    `AuthMiddleware` 之后**（见 create_app 里的顺序说明），所以这里要主动让路，
    否则「登录令牌」会被拿去和「共享密钥」比对而误判 401：

      * 路径在 `PUBLIC_AUTH_PATHS` 里 → 放行。否则配了共享密钥的部署连
        `/api/auth/status` 都取不到，登录页直接变一堵墙。
      * `request.state.user_id` / `agent_id` 已有值 → AuthMiddleware 已经完成
        身份与权限校验，放行，没必要再过共享密钥这一道。
    """

    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request, call_next):
        # 预检请求由 CORS 中间件处理、不携带自定义头，直接放行
        if request.method == "OPTIONS":
            return await call_next(request)
        # 共享密钥只保护 API；静态文件、登录页与文档页不归它管。
        # 修复前这里会把 /assets/* 一起拦成 401，配了密钥的生产前端直接白屏。
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        # 读者门户使用独立令牌，绝不参与馆员共享密钥校验
        if request.url.path.startswith("/api/portal"):
            return await call_next(request)
        # 登录相关端点必须放行（否则登录页取不到状态；详见类 docstring）
        if request.url.path in PUBLIC_AUTH_PATHS:
            return await call_next(request)
        # AuthMiddleware 已完成身份及模块权限校验，人和 agent 均无需共享密钥。
        if (getattr(request.state, "user_id", None) is not None
                or getattr(request.state, "agent_id", None) is not None):
            return await call_next(request)
        # 只认 X-Access-Token 头。旧实现还接受 ?token= 查询串——URL 会进
        # 访问日志 / 浏览器历史 / Referer，等于把共享密钥写到各处日志里；
        # 前端（api.ts 请求头注入）与测试从来没用过它，已移除。
        supplied = request.headers.get("X-Access-Token") or ""
        # 双方都编码成 bytes：compare_digest 对含非 ASCII 的 str 会抛 TypeError
        # （500），bytes 恒安全（错误令牌 → 401）。
        if not hmac.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8")):
            return JSONResponse({"detail": "未授权：缺少或错误的接口凭据"},
                                status_code=401)
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """登录态与模块权限校验。

    放行条件（任一）：
      1. 路径在 PUBLIC_PATHS / PUBLIC_AUTH_PATHS 里；
      2. 带了正确的共享密钥 `X-Access-Token`（给脚本 / agent 客户端，保持兼容）；
      3. 尚未创建管理员时，按首次初始化流程放行（仅限单机）；
      4. 令牌有效且该角色对本模块有足够档位。

    ## 为什么用中间件而不是 FastAPI 依赖

    依赖要挂在每个路由或每个 router 上，模块和路径较多，新增路由时容易漏挂权限。
    中间件按路径前缀统一判定，新增路由只要落在已登记前缀下就自动受控；
    忘登记模块则拒绝而不是放行（见 core/perm.py），测试会遍历所有路由检查覆盖。

    ## 为什么把用户 id 挂在 request.state 上

    `operator_of()` 需要「当前登录人」来署名，它是普通函数、拿不到依赖注入。
    中间件在这里解析一次、挂到 `request.state.user_id`，下游各取所需，避免每条
    请求重复查库。
    """

    async def dispatch(self, request, call_next):
        started = time.perf_counter()      # agent 审计要记耗时，被拦下的也算
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)
        # 非 /api 路径（静态文件、文档页）不归本中间件管。
        # 例外：接口文档页虽然不在 /api 下，但它把全部路由结构摊开，空库窗口期
        # 必须和业务接口一样挡住。
        if path in _LAN_DOC_PATHS:
            guard = await run_in_threadpool(self._lan_guard, request)
            if guard is not None:
                return guard
            return await call_next(request)
        if not path.startswith("/api"):
            return await call_next(request)
        if path in PUBLIC_PATHS:
            return await call_next(request)
        # 局域网守卫：绑到非回环地址、且还没建出任何账号 → 只放行探活与状态。
        # 必须放在门户分支**之前**：读者门户走独立鉴权、不经过下面的身份解析，
        # 放后面会漏掉 /api/portal/**（那里面能读到读者资料与借阅记录）。
        guard = await run_in_threadpool(self._lan_guard, request)
        if guard is not None:
            return guard
        # 配了共享密钥时，空库窗口期的 /api/auth/setup 也必须带密钥。
        # auth_enabled=false 的放行分支在下面，不能只靠 PUBLIC_AUTH_PATHS 挡。
        if path == "/api/auth/setup" and ACCESS_TOKEN:
            supplied = request.headers.get("X-Access-Token") or ""
            if not hmac.compare_digest(supplied.encode("utf-8"),
                                       ACCESS_TOKEN.encode("utf-8")):
                return JSONResponse(
                    {"detail": "已配置共享密钥，初始化管理员也必须携带 X-Access-Token"},
                    status_code=401)
        # 读者门户有独立的读者会话与鉴权，不走上方的馆员权限中间件；
        # 各路由通过 current_reader 依赖自行强制登录（登录/状态除外）。
        if path.startswith("/api/portal"):
            return await self._tracked(request, call_next)
        # 公开的 auth 端点**不拦**，但仍要尽力解析身份：`/api/auth/status` 就是
        # 靠它回答「我这条令牌还有效吗」。少了这一步，中间件在这里直接返回，
        # request.state 上永远没有 user_id，status 便恒报 authenticated=false
        # ——前端每次刷新页面都会把带着有效令牌的用户弹回登录页。
        public_auth = path in PUBLIC_AUTH_PATHS

        # 共享密钥优先：脚本 / agent 客户端没有登录态，走这条路。
        # 权限：默认仍是「全权」（向后兼容），可用 JIANJI_ACCESS_TOKEN_ROLE
        # 绑定一个角色来收口；无论哪种，**每一条请求都记审计**。
        if ACCESS_TOKEN:
            supplied = request.headers.get("X-Access-Token") or ""
            if hmac.compare_digest(supplied.encode("utf-8"),
                                   ACCESS_TOKEN.encode("utf-8")):
                self._mark_shared_token(request)
                return await self._shared_token_dispatch(request, call_next, started)

        # 身份解析是**同步 DB 代码**（psycopg2），必须挪进线程池：dispatch 本身
        # 跑在事件循环上，这里的 SessionLocal() / SELECT 一旦变慢（锁等待、慢查询），
        # 卡住的不是这一条请求而是**整个应用的并发** —— 比任何单个慢路由影响面大。
        # `_resolve_identity` 原子地完成「解析 + 决定 + 挂 request.state」，
        # 回到事件循环时会话已关、身份已就绪，dispatch 里没有任何 DB 调用。
        agent, decision = await run_in_threadpool(
            self._resolve_identity, request, public_auth)

        if decision is not None:
            # **被拦下的请求也要记**：「谁在反复尝试越权」恰恰是审计最该回答的问题。
            if agent is not None:
                await run_in_threadpool(self._audit, agent, request.method, path,
                                        decision.status_code,
                                        (time.perf_counter() - started) * 1000.0)
            return decision

        try:
            response = await self._tracked(request, call_next)
        except Exception:
            # 审计写在 `_tracked` **之后**的话，异常路径直接跳过——「出事的调用
            # 查不到」恰是审计最该覆盖的场景。按共享密钥路径（下方
            # `_shared_token_dispatch`）的做法补记一条 500。
            if agent is not None:
                try:
                    await run_in_threadpool(
                        self._audit, agent, request.method, path, 500,
                        (time.perf_counter() - started) * 1000.0)
                except Exception:            # 审计失败不能盖住原始异常
                    log.warning("agent 审计写入失败（异常路径）", exc_info=True)
            raise
        if agent is not None:
            await run_in_threadpool(self._audit, agent, request.method, path,
                                    response.status_code,
                                    (time.perf_counter() - started) * 1000.0)
        return response

    @staticmethod
    def _resolve_identity(request, public_auth: bool):
        """解析登录态 / agent 令牌并给出放行与否的决定（**同步方法，线程池里跑**）。

        返回 `(agent, decision)`；身份标量挂到 `request.state` 上（供 `operator_of`
        署名等下游使用），不跨会话传递 ORM 对象。

        ⚠️ 千万不要把 `await call_next(request)` 写进本方法的调用链：
           会话在返回前必须 close（见下面 try/finally）。若在会话未关时先去跑
           路由，close() 会回滚到自己的 savepoint，把**路由刚提交的数据一起抹掉**
           —— 实测症状：setup 建好的管理员被抹掉 → auth_enabled 恒为 false，
           test_auth.py 一片 401/403 假失败。「决定」与「派发」分离正是为此。
        另外，生产上一条请求可能很慢（导入、批处理），跨 call_next 握着连接
        也会白占连接池。
        """
        from .db import SessionLocal
        from .services import auth as svc
        from .api.auth import auth_enabled_cached

        agent = None            # AgentToken 的三个标量（agent 凭据）
        decision: JSONResponse | None = None
        db = None
        try:
            db = SessionLocal()
            if auth_enabled_cached(db):
                token = svc.token_from_request(request)
                user = svc.resolve_session(db, token)
                if user is None and token:
                    # 不是登录会话 → 试 agent 凭据（长期令牌，写在 MCP 客户端配置里）。
                    # 两者**共用 svc.check_path** 这一份权限判定，agent 不会比
                    # 同角色的人多一件权限。
                    from .services import agents as agents_svc
                    agent = agents_svc.resolve_agent_token(db, token)
                if user is not None and not public_auth:
                    ok, reason = svc.check_request(db, user, request.method, request.url.path)
                    if not ok:
                        decision = JSONResponse({"detail": reason}, status_code=403)
                elif agent is not None and not public_auth:
                    ok, reason = svc.check_path(db, agent.role_id, request.method,
                                                request.url.path,
                                                who=f"agent：{agent.name}")
                    if not ok:
                        decision = JSONResponse({"detail": reason}, status_code=403)
                if decision is None:
                    if user is not None:
                        # 供 operator_of 署名（见模块 docstring）
                        request.state.user_id = user.id
                        request.state.username = user.username
                        request.state.display_name = user.display_name
                    elif agent is not None:
                        # agent 署名走 `agent:名称`，财产账与转库台账上要能看出
                        # 是人还是机器干的（见 core/operator.py）
                        request.state.agent_id = agent.id
                        request.state.agent_name = agent.name
                    elif not public_auth:
                        decision = JSONResponse(
                            {"detail": "未登录或登录已过期，请重新登录"},
                            status_code=401)
            # 登录未启用 → 不解析身份、decision 仍为 None，直接放行（完全不拦）
        except Exception:
            # 数据库不可用时包括公开登录端点也失败关闭，绝不泄漏连接/SQL细节。
            decision = JSONResponse({"detail": "权限校验暂不可用，请稍后重试"},
                                    status_code=503)
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    decision = JSONResponse({"detail": "权限校验暂不可用，请稍后重试"},
                                            status_code=503)
        return agent, decision

    @staticmethod
    def _lan_guard(request):
        """局域网 + 还没有任何账号 → 挡住除探活/状态之外的一切请求。

        ## 这条守卫解决的是什么

        单机模式下，尚未创建管理员时按首次初始化流程运行；绑定到局域网后，
        必须先完成管理员创建，再对外提供服务。守卫在管理员创建前挡住业务接口，
        避免未初始化状态暴露给校园网。

        ## 为什么这里也挡 setup

        经代理时无法可靠判断请求来源，因此要求**先关闭局域网访问、在本机完成
        管理员创建、再开启局域网**。前端登录页会显示对应提示，引导操作者完成初始化。

        ## 两个信号取「或」

        `lan_mode()` 看启动参数声明的绑定地址，`request_from_lan()` 看这条连接
        实际落在哪个本地地址。单独任何一个都会漏：前者可能解析不到（包装脚本
        改写参数、编程方式启动），后者对经代理进来的请求永远看到回环。
        """
        from .core.deployment import lan_mode, request_from_lan

        if request.url.path in _LAN_BOOTSTRAP_ALLOWED:
            return None
        if not (lan_mode() or request_from_lan(request.scope)):
            return None

        from .api.auth import auth_enabled_cached
        from .db import SessionLocal

        try:
            db = SessionLocal()
            try:
                enabled = auth_enabled_cached(db)
            finally:
                db.close()
        except Exception:
            # 数据库不可用：交给下面的身份解析统一给 503，这里不抢先下结论
            return None
        if enabled:
            return None
        log.warning("[lan-guard] 已开放局域网但尚未创建管理员，拦截 %s",
                    request.url.path)
        return JSONResponse(
            {"detail": "本机尚未创建管理员账号。请先关闭「局域网访问」开关，"
                       "在本机浏览器完成管理员初始化，再重新开启局域网访问。"},
            status_code=403)

    @staticmethod
    def _mark_shared_token(request) -> None:
        """共享密钥命中后，挂一个**固定且可辨**的署名。

        共享密钥走的是「跳过身份解析」这条快路，原来 `request.state` 上什么都
        没有，于是 `operator_of()` 退回读 `X-Operator` 请求头——那是客户端随便
        填的。结果就是：用接口凭据做的每一次写入，在操作日志与编目工作量统计里
        都挂着一个可以任意伪造的名字，事后无法归因。

        这里把署名钉成常量，让「谁干的」至少能分出「是人还是接口凭据」。
        注意这不改变能力边界：共享密钥仍然不能调用 `/api/auth/me` 这类需要
        真实身份的路由（`current_user` 只看 `request.state.user_id`）。
        """
        request.state.shared_token = True
        request.state.display_name = SHARED_TOKEN_OPERATOR

    @staticmethod
    async def _shared_token_dispatch(request, call_next, started):
        """共享密钥命中的统一处理：先按绑定角色判定，再跑业务，最后记审计。

        审计必须覆盖**异常路径**——500 恰恰是最需要留痕的那种，漏了它等于
        「出事的调用查不到」。
        """
        decision = await run_in_threadpool(AuthMiddleware._shared_token_decision,
                                           request)
        if decision is not None:
            await run_in_threadpool(AuthMiddleware._audit_shared, request.method,
                                    request.url.path, decision.status_code,
                                    (time.perf_counter() - started) * 1000.0)
            return decision
        try:
            response = await AuthMiddleware._tracked(request, call_next)
        except Exception:
            await run_in_threadpool(AuthMiddleware._audit_shared, request.method,
                                    request.url.path, 500,
                                    (time.perf_counter() - started) * 1000.0)
            raise
        await run_in_threadpool(AuthMiddleware._audit_shared, request.method,
                                request.url.path, response.status_code,
                                (time.perf_counter() - started) * 1000.0)
        return response

    @staticmethod
    def _shared_token_decision(request):
        """绑定了角色就按该角色的模块权限判定；没绑定返回 None（放行）。

        `JIANJI_ACCESS_TOKEN_ROLE` 填**角色标识**（`roles.name`，如 `editor`），
        不是中文名——中文名可以改，标识才是权限判定认的那个。

        角色找不到时返回 503 而不是放行：配置写错却悄悄退化成「全权」，正是
        这类开关最危险的失效方式。
        """
        if not ACCESS_TOKEN_ROLE:
            return None
        from .db import SessionLocal
        from .services import auth as svc

        try:
            db = SessionLocal()
            try:
                role = svc.role_by_name(db, ACCESS_TOKEN_ROLE)
                if role is None:
                    log.error("[token] JIANJI_ACCESS_TOKEN_ROLE=%s 找不到该角色，"
                              "共享密钥请求一律拒绝", ACCESS_TOKEN_ROLE)
                    return JSONResponse(
                        {"detail": "接口凭据绑定的角色不存在，"
                                   "请联系系统管理员"},
                        status_code=503)
                ok, reason = svc.check_path(db, role.id, request.method,
                                            request.url.path,
                                            who="接口凭据(共享密钥)")
                if not ok:
                    return JSONResponse({"detail": reason}, status_code=403)
                # 下游需要按「共享密钥绑的是哪个角色」裁剪敏感字段（工作台的读者
                # 姓名/证号，见 api/dashboard.py::_may_see_reader_identity）。
                # 这里只留角色 id，不把 ORM 对象带出去。
                request.state.shared_role_id = role.id
                return None
            finally:
                db.close()
        except Exception as e:
            log.warning("[token] 共享密钥权限校验失败：%s", e)
            return JSONResponse({"detail": "权限校验暂不可用，请稍后重试"},
                                status_code=503)

    @staticmethod
    def _audit_shared(method: str, path: str, status: int, ms: float) -> None:
        """给共享密钥请求记一行审计（与 agent 同一张表）。

        这一条是安全审计里「只解决了归因、没解决留痕」的那半个洞：原来共享密钥
        的调用只进 `record_logs`（记录级变更），接口层面的调用完全不留痕，出了
        事没法回答「它到底调了什么」。现在与 agent 一样逐条记。

        `token_id` 用 0 作哨兵、`token_name` 记「共享密钥」，管理员在
        「接口凭据」页的审计列表里能直接看到，不用去翻服务器日志。
        写失败一律吞掉：审计不能把正常响应变成 500。
        """
        from .db import SessionLocal
        from .services import agents as agents_svc

        try:
            db = SessionLocal()
            try:
                agents_svc.record_audit(db, token_id=SHARED_TOKEN_AUDIT_ID,
                                        token_name=SHARED_TOKEN_NAME,
                                        method=method, path=path, status=status,
                                        duration_ms=int(ms))
            finally:
                db.close()
        except Exception as e:
            log.warning("[audit] 写共享密钥审计失败（%s %s）：%s", method, path, e)

    @staticmethod
    async def _tracked(request, call_next):
        """跑业务，并按结果落/弃**记录级操作日志**。

        采集靠 ORM 事件（`models/__init__.py`）往 `core/oplog.py` 的 contextvar
        里塞条目，这里负责决定"这段收集到底算不算数"：

          - 状态码 < 400 → 业务已提交，落库；
          - 状态码 >= 400（质检没过 / 条码撞号 / 越权）→ **丢弃**：
            业务回滚了就等于没发生，留下"改过"的日志比不留更误导人
            （用户会去查一条其实没变的记录，然后怀疑整个日志不可信）。
          - 未捕获异常 → 丢弃后原样抛出（500 也不该留下"改过"的痕迹）。
        """
        from .core import oplog

        oplog.begin()
        try:
            response = await call_next(request)
        except Exception:
            oplog.discard()
            raise
        if response.status_code < 400:
            # 落库是同步 DB 调用，与身份解析同理必须离开事件循环（见 dispatch）
            await run_in_threadpool(AuthMiddleware._flush_oplog, request)
        else:
            oplog.discard()
        return response

    @staticmethod
    def _flush_oplog(request) -> None:
        """把收集到的变更写进 `record_logs`。

        另开一个短会话：身份解析那个会话早已关闭，而日志**不该**和业务请求共用
        事务（业务回滚时日志会跟着没）——同 `_audit` 的理由。
        写失败一律吞掉：日志是旁路，不能把正常响应变成 500。
        """
        from .core.operator import operator_of
        from .db import SessionLocal
        from .services import record_logs as logs_svc

        try:
            who = operator_of(request)
        except Exception:
            who = None
        try:
            db = SessionLocal()
            try:
                logs_svc.flush(db, who)
            finally:
                db.close()
        except Exception as e:
            log.warning("[oplog] 落库失败（%s %s）：%s",
                        request.method, request.url.path, e)

    @staticmethod
    def _audit(agent, method: str, path: str, status: int, ms: float) -> None:
        """给 agent 请求补一行审计（**只记 agent**，见 services/agents.py）。

        另开一个短会话：身份解析那个会话已经关了，而且审计**不能**和业务请求
        共用事务 —— 业务回滚时日志也跟着没了，那就不是审计了。
        写失败一律吞掉：审计不能把正常响应变成 500。
        """
        from .db import SessionLocal
        from .services import agents as agents_svc

        try:
            db = SessionLocal()
            try:
                agents_svc.record_audit(db, token_id=agent.id, token_name=agent.name,
                                        method=method, path=path, status=status,
                                        duration_ms=int(ms))
            finally:
                db.close()
        except Exception as e:
            log.warning("[audit] 写审计失败（%s %s）：%s", method, path, e)



#: JSON 请求体上限（1 MiB）。导入走 multipart，各自有单文件上限；这里只挡 JSON。
JSON_BODY_LIMIT = 1024 * 1024


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """在解析之前按 `Content-Length` 拒掉超大 JSON 请求体。

    ## 为什么需要一个全局上限

    公开端点（`/api/auth/login`、`/api/portal/auth/login`）不需要任何凭据就能打到，
    而它们的处理路径是「读完整个 body → JSON 解析 → 查库 → scrypt」。没有上限时，
    一个几百 MB 的 body 就能把 worker 的 CPU / 内存占满——不需要账号，也不需要
    猜口令。登录端点的口令长度上限（128）只挡住了「进 scrypt 之后」这一段。

    ## 为什么只对 `application/json` 生效

    上传接口是 multipart，各自已经有单文件上限（ISO / xlsx 都有明确阈值）。在这里
    按 JSON 的阈值一刀切会误伤正常导入。缺失 `Content-Length`（分块传输）不在这里
    处理：前端 axios 总会带长度，ASGI server 自身也有兜底限制。
    """

    async def dispatch(self, request: Request, call_next):
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype == "application/json":
            raw = (request.headers.get("content-length") or "").strip()
            if raw.isdigit() and int(raw) > JSON_BODY_LIMIT:
                return JSONResponse(
                    {"detail": f"请求体过大（上限 {JSON_BODY_LIMIT // 1024} KB）"},
                    status_code=413)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """补一组基础安全响应头。

    为什么这几个而不是一整套 CSP：`X-Frame-Options: DENY` 挡点击劫持（诱导管理员
    在看不见的 iframe 里点「删除」），`nosniff` 挡「把 JSON 当脚本执行」这一类
    内容嗅探，`Referrer-Policy` 避免把内网地址带到外链。这三个都不会影响页面
    正常渲染。

    ## CSP

    前端入口 `index.html` **没有内联脚本**（唯一 `<script>` 是 `/src/main.ts` 模块
    引用），Vite 产物全为外部文件，所以 `script-src 'self'` 是安全的——这条是
    CSP 里真正值钱的部分：页面真出现 XSS 注入点时，内联 `<script>` 与内联事件
    处理器一律不执行，令牌（localStorage）不会被一句话偷走。

    样式仍放 `'unsafe-inline'`：设计系统令牌与组件级 `style` 属性大量使用，先做
    样式外提的成本远大于收益，而 `style-src` 从来不是 XSS 的主通道（脚本才是）。
    `img-src` 放开 `data:`（index.html 的内联 SVG favicon）与 `blob:`（导出下载）；
    `connect-src 'self'` 锁定 API 同源。`frame-ancestors 'none'` 与 X-Frame-Options
    语义重复但更现代，一起带上。

    若未来引入内联脚本（如启动页引导），必须改用 nonce/hash，**不要**退回
    `'unsafe-inline'`。
    """

    CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
           "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    async def dispatch(self, request, call_next):
        # 请求 id：现场报「刚才点保存报错了」时，日志里只有时间戳根本串不起来
        # （一屏几百行访问日志）。客户端带了就沿用（前端/网关可以把同一次操作
        # 的多个请求串成一条链），没带就生成一个，统一回写到响应头。
        rid = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Request-Id", rid)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Content-Security-Policy", self.CSP)
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """FastAPI lifespan：启动时把上个进程遗留的后台任务判死（见 recover_interrupted_jobs）。"""
    _recover_interrupted_jobs()
    _check_db_role()
    _check_schema_head()
    _check_route_declarations()
    yield


def _check_route_declarations() -> None:
    """建索引 + 校验「路由声明」与旧权限表是否一致。

    ## 两步改造的收尾

    第一步只加声明、判定仍读旧表；第二步（本函数所在版本）**建索引供判定读取**，
    旧表退化为回退。两边必须一致，否则：

    - 声明比旧表宽 → 改造后静默放权（最危险，所以启动时逐条比对并报错）；
    - 声明比旧表窄 → 改造后原本能用的功能 403（好查，但也该提前发现）。

    ## 为什么现在报错但不阻断启动

    索引已经建好、判定已经切到声明，所以不一致**不会**让权限行为变得不可预测
    （判定只看声明，旧表只在索引没建时兜底）。这里记 ERROR 是让人尽快修掉那张
    旧表，而不是把服务停掉——现场停服比权限归属写错更糟。
    """
    from .core import route_meta

    route_meta.build_index(app)
    problems = route_meta.check_declarations(
        app, public_paths=PUBLIC_PATHS, public_auth_paths=PUBLIC_AUTH_PATHS)
    if problems:
        for p in problems:
            log.error("[perm] 路由声明与旧权限表不一致：%s", p)
        log.error("[perm] 共 %d 处不一致。判定已改读声明，旧表仅作回退；"
                  "请修正声明或旧表，两者应当同口径。", len(problems))
    else:
        log.info("[perm] 路由声明与旧权限表一致（%d 条已声明）。",
                 len(route_meta._ROUTES))


def _check_schema_head() -> None:
    """启动时对比「库里的迁移版本」与「代码里的 head」，落后就打 WARNING。

    ## 为什么需要这条

    2026-10-26 真机踩到：代码升到 1.1.9（模型新增了 `loans.operator_renew`），
    但库还停在旧迁移上。应用照常启动、页面能开，**只有碰到那张表的接口 500**
    （工作台、馆藏、借还全挂），日志里是一句 `UndefinedColumn: 字段
    loans.operator_renew 不存在`。现场看到的是"系统坏了"，不是"少跑了一步升级"。

    ## 为什么只 WARNING 不阻断

    迁移本身要停机窗口（`CREATE INDEX CONCURRENTLY`、数据清洗），启动时自动跑会把
    「服务起不来」变成「服务起来一半」。这里只负责**把原因说到脸上**：日志里明确写
    「库结构落后，请执行 alembic upgrade head」，而不是让人去猜那句 UndefinedColumn。

    读不到版本（空库/连不上）时静默返回：那是 `init_db.py` 的场景，不该在这里报错。
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text as _text

        from .db import engine

        cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        with engine.connect() as conn:
            row = conn.execute(_text("SELECT version_num FROM alembic_version")).first()
        current = row[0] if row else None
        if head and current != head:
            log.warning(
                "[schema] 数据库结构落后于代码：库里是 %s，代码要求 %s。"
                "受影响的功能会直接 500（典型：字段不存在）。"
                "请在停机窗口执行数据库升级命令后重启。",
                current or "（空）", head)
    except Exception as exc:       # 空库、无 alembic 目录、连接问题都走这里
        log.debug("[schema] 跳过迁移版本检查：%s", exc)


def _check_db_role() -> None:
    """启动时看一眼：当前是不是用 PostgreSQL **超级用户**连的库。

    为什么值得单独查一次：`init_db.py` 建库时用的是 postgres 账号，README 里的
    示例连接串也是它，于是现场十有八九一直拿超级用户跑应用。单机自用问题不大，
    一旦这台机器上有别的服务、或者口令泄露，攻击者拿到的是**整个数据库实例**
    （含其他库、能 `COPY ... FROM PROGRAM` 执行系统命令），而不是本库的几张表。

    只警告、不阻断：换账号要改连接串、迁所有权、调 `pg_hba`，现场做不了那么快；
    把它变成启动失败会让人直接放弃升级。日志里说清「该建什么角色」更有用。

    数据库连不上时静默跳过——那是另一条路径在管（`connection_hint`）。
    """
    from sqlalchemy import text

    from .db import SessionLocal

    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT current_user, "
                "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"
            )).first()
        finally:
            db.close()
    except Exception:
        return
    if not row:
        return
    user, is_super = row[0], bool(row[1])
    if is_super:
        log.warning("[db] 当前以 PostgreSQL 超级用户「%s」连接本库。建议为应用单独建角色："
                    "只授予本库的增删改查与建表权限，不给 SUPERUSER / CREATEDB —— "
                    "超级用户一旦泄露，攻击者拿到的是整个数据库实例，而不只是这个库。",
                    user)


def _recover_interrupted_jobs() -> None:
    """启动时把遗留的 running/pending 后台任务判死（执行体随上个进程没了）。

    数据库暂时不可用**不能**挡住应用启动 —— 打印一条日志即可，下次重启再试。
    """
    from .db import SessionLocal
    from .services.batch import recover_interrupted_jobs
    try:
        db = SessionLocal()
        try:
            n = recover_interrupted_jobs(db)
            if n:
                log.info("[startup] 已把 %d 个遗留后台任务标记为中断", n)
        finally:
            db.close()
    except Exception as e:
        from .db import connection_hint
        log.warning("[startup] 遗留后台任务状态恢复失败：%s", connection_hint(e))


def _mount_frontend(app: FastAPI) -> bool:
    """把前端构建产物 `frontend/dist` 挂到根路径，实现**单端口**生产部署。

    ## 为什么要有这条

    原来生产环境跑的是 `vite`（开发服务器）：它不压缩、常驻文件监听、把
    `/src/**` 源码原样暴露，而且它自己没有任何鉴权，却把请求原封不动转发给
    后端——安全边界实际落在了一个为热更新设计的进程上。构建产物 `dist` 一直
    在包里，只是没有启动路径在用。

    挂上之后：一个进程、一个端口，静态资源与 `/api` 同源，前端不再需要代理。

    ## 开关

    默认在 `frontend/dist` 存在时启用；`JIANJI_SERVE_STATIC=0` 可关掉
    （开发时想强制看 dev server 的产物时用）。dist 不存在就静默跳过——开发机
    上常常还没 build 过。

    ## 为什么要有 SPA 兜底

    `/admin/records/12` 这类前端路由在磁盘上没有对应文件，纯 StaticFiles 会
    404。前端是 history 模式路由，必须回落到 `index.html` 交给 vue-router。
    只对「找不到文件」兜底，`/api/**` 已在路由表前面匹配，不受影响。
    """
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    if (os.environ.get("JIANJI_SERVE_STATIC", "").strip().lower()
            in {"0", "false", "no", "off"}):
        return False
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not dist.is_dir() or not (dist / "index.html").is_file():
        return False

    class _SPA(StaticFiles):
        async def get_response(self, path, scope):
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404:
                    # /api/** 的 404 不能被兜底成 index.html：那会把「接口不存在」
                    # 变成 200 + 一坨 HTML，前端拿到的是解析失败而不是一个明确的
                    # 404，排查时看不出是路径写错了。
                    if (scope.get("path") or "").startswith("/api"):
                        raise
                    # 构建产物（/assets/*.js|css 等带扩展名的文件）找不到时必须
                    # 真 404，**不能**兜底成 index.html：
                    # 浏览器拿到 HTML 当 JS 解析 → 语法错误 → 整页白屏，
                    # 而控制台只报一句看不懂的 "Unexpected token '<'"。
                    # 2026-10-26 真机：升级后浏览器还引用旧 hash 的 chunk，
                    # 用户看到的就是「这一页崩了」。
                    if _looks_like_asset(scope.get("path") or ""):
                        raise
                    return await super().get_response("index.html", scope)
                raise
            # 入口 HTML 必须每次回源校验：它里面引用的是带 hash 的 chunk 名，
            # 一旦被浏览器缓存住，新版本发布后仍会去要旧 chunk（上面那条会 404）。
            # 带 hash 的 /assets/* 相反，可以长缓存。
            _apply_cache_headers(response, scope.get("path") or "")
            return response

    # 挂在最后：路由表按顺序命中，/api/** 与 /docs 先匹配，其余才落到静态资源
    app.mount("/", _SPA(directory=str(dist), html=True), name="ui")
    return True


def _looks_like_asset(path: str) -> bool:
    """这个路径像不像构建产物（而不是前端路由）。

    判据是**最后一段带扩展名**：`/assets/index-abc.js`、`/favicon.ico` 是资源；
    `/admin/records/12`、`/admin/users` 是路由。前端路由不带扩展名。
    """
    last = path.rstrip("/").rsplit("/", 1)[-1]
    return "." in last and not last.startswith(".")


def _apply_cache_headers(response, path: str) -> None:
    """静态资源的缓存策略。

    - `index.html`（含 SPA 兜底返回的那份）：`no-cache` —— 每次回源校验，
      否则升级后浏览器仍加载旧入口、引用已不存在的旧 chunk。
    - `/assets/**`：内容寻址（文件名带 hash），可以 `immutable` 长缓存。
    """
    if path.endswith(".html") or path in ("", "/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return
    if path.startswith("assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"


def create_app() -> FastAPI:
    from .core.deployment import lan_mode
    from .core.logging_filters import install_access_log_redaction
    from .version import __version__

    # 访问日志脱敏：`/api/circulation/reader?card_no=...` 这类 URL 不该把借书证号
    # 写进 launcher.log（日志会被界面展示、被排障打包外发）。见 core/logging_filters。
    install_access_log_redaction()

    # 接口文档页在**对外监听**时直接关掉：`/openapi.json` 会把 186 条路径（226 个操作）与
    # 全部参数结构摊开，对校内使用者没有任何价值，对攻击者是一份地图。
    # 单机形态保留（开发与排障要用）。
    _docs = not lan_mode()
    app = FastAPI(title="简籍（Jianji）API", version=__version__,
                  description="CNMARC 编目 / 套录 / ISO·Excel 交换 / AI 辅助",
                  docs_url="/docs" if _docs else None,
                  redoc_url="/redoc" if _docs else None,
                  openapi_url="/openapi.json" if _docs else None,
                  lifespan=_lifespan)

    # CORS：默认仅本机；显式配置时按配置放行
    origins_env = os.environ.get("JIANJI_CORS_ORIGINS", "").strip()
    if origins_env:
        allow_origins = [o.strip() for o in origins_env.split(",") if o.strip()]
        allow_origin_regex = None
    else:
        allow_origins = []
        allow_origin_regex = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"

    app.add_middleware(CORSMiddleware, allow_origins=allow_origins,
                       allow_origin_regex=allow_origin_regex,
                       allow_methods=["*"], allow_headers=["*"])

    app.include_router(api_router)

    # ⚠️ `add_middleware` 是**后加的先执行**（Starlette 反向包装）：
    # 这里的顺序是 CORS → AccessToken → Auth，即 Auth 最先看到请求。
    # 于是「未登录」的 401 / 「无权限」的 403 都在 CORS 之前产生，响应里不带
    # CORS 头——前端走 Vite 代理（同源）不受影响；直连 8000 的跨域调用会看到
    # 浏览器的 CORS 报错而不是「请先登录」。若将来要支持跨域直连，把 CORS 挪到
    # 最后 add 即可（本文件与 backend/tests/test_auth.py 都按现顺序断言）。
    if ACCESS_TOKEN:
        app.add_middleware(AccessTokenMiddleware, token=ACCESS_TOKEN)

    app.add_middleware(AuthMiddleware)

    # 请求体上限排在 Auth 之后 add → 跑在 Auth **里面**，但仍在路由之前：
    # 先过身份（401/403 立刻回，不读 body），再过大小。放在安全响应头之前 add，
    # 于是 413 也会带上 nosniff / anti-framing。
    app.add_middleware(BodyLimitMiddleware)

    # 最后 add 的跑在**最外层**（Starlette 反向包装），所以安全响应头能覆盖
    # 到 401/403 与静态资源——那些响应恰恰也需要 nosniff 与 anti-framing。
    app.add_middleware(SecurityHeadersMiddleware)

    # 启动横幅：版本 + 部署形态。学校现场排查时，launcher.log 里这两行就能回答
    # 「装的是哪一版」「这次是单机还是局域网」——以前两个问题都得去翻配置文件。
    from .core.deployment import describe as _describe_deployment
    _dep = _describe_deployment()
    log.info("简籍（Jianji）后端 %s 启动中：绑定 %s（%s）%s",
             __version__, _dep["bind_host"],
             "局域网模式" if _dep["lan_mode"] else "仅本机",
             "；已启用共享密钥鉴权" if ACCESS_TOKEN else "")
    if _dep["lan_mode"]:
        log.warning("[lan] 局域网模式下同网段设备均可访问；"
                    "管理员账号建好之前，除探活与状态外的接口会被拒绝。")

    if ACCESS_TOKEN:
        if ACCESS_TOKEN_ROLE:
            log.info("[token] 共享密钥已绑定角色「%s」：按该角色的模块权限判定，"
                     "每条请求记审计。", ACCESS_TOKEN_ROLE)
        else:
            log.warning("[token] 共享密钥**未绑定角色**：它将跳过全部模块权限校验"
                        "（一把钥匙读写全站）。建议改用「接口凭据」(agent token)，"
                        "或联系管理员配置接口凭据角色。"
                        "无论哪种，共享密钥的每条请求都已记入审计。")

    if _mount_frontend(app):
        log.info("[ui] 已托管 frontend/dist（单端口模式：直接访问本服务端口即可）")

    return app


app = create_app()
