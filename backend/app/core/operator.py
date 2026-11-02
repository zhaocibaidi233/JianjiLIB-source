# -*- coding: utf-8 -*-
"""编目员标识。

设计取舍：本项目定位是**单机编目工具**，登录是**可选**的。
但「谁编的」必须知道——旧版系统把「图书编目分类统计（按编目员）」列为编目部门
第一统计，是工作量考评的依据。所以标识按以下优先级取：

  1. **登录账号的姓名**（`request.state.display_name`，由 AuthMiddleware 挂上，
     见 main.py）——启用登录后这就是唯一真源，署名不再靠手填；
  2. 请求头 `X-Operator`——登录未启用时的轻量方案，前端从 `localStorage.operator`
     带上，编目员自己填一次名字即可；
  3. 都没有就落空，统计里归入「(未署名)」，不阻断任何操作。

**为什么登录优先于请求头**：请求头是客户端随便填的。如果登录后仍以请求头为准，
那么一个编目员只要改一下 localStorage 就能把工作量记到同事头上——署名统计
立刻失去意义。所以登录态一旦存在，就压过 X-Operator。

**中文姓名的坑**：HTTP 头字段值只能是 ASCII（httpx 会直接抛
`UnicodeEncodeError`，浏览器/axios 也会拒绝或乱码）。所以约定：
前端用 `encodeURIComponent(姓名)` 编码后放进头，后端 unquote 还原。
未编码的 ASCII 名字（如 `liujing`）unquote 后原样不变，两条路都通。
"""
from __future__ import annotations

from urllib.parse import unquote

HEADER = "X-Operator"
UNSIGNED = "(未署名)"


def logged_in_name(request) -> str | None:
    """已登录身份的署名；没有身份返回 None。

    三种来源（中间件只在**登录启用**且身份有效时才写 `request.state`，所以读到值
    就等于「确实认过身份」）：

      1. 登录账号：姓名优先，回退用户名；
      2. agent 凭据：`agent:名称`—— 机器干的活得署机器的名，
         财产账与转库台账上要能一眼分出人和机器；
      3. 都没有 → None，交给 `X-Operator` 兜底。
    """
    try:
        name = getattr(request.state, "display_name", None) or \
            getattr(request.state, "username", None)
        agent = getattr(request.state, "agent_name", None)
    except Exception:
        return None
    name = (name or "").strip()
    if name:
        return name
    agent = (agent or "").strip()
    return f"agent:{agent}" if agent else None


def operator_of(request) -> str | None:
    """编目员标识：登录账号姓名 → 请求头（percent-decode 后）；空值归一为 None。"""
    name = logged_in_name(request)
    if name:
        return name
    try:
        raw = request.headers.get(HEADER)
    except Exception:
        return None
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        decoded = unquote(raw, encoding="utf-8", errors="strict")
    except Exception:
        decoded = raw          # 不是合法的百分号编码，原样使用
    return decoded.strip() or None
