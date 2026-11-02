# -*- coding: utf-8 -*-
"""维护接口：数据库结构升级。

## 为什么由后端执行，而不是启动器执行

启动器只知道端口，不知道后端**实际连的是哪个库**——`/api/health` 里刻意不返回
用户名与口令（属于敏感信息）。如果启动器自行执行 `alembic upgrade head`，只能依赖
`launcher-config.json` 里保存的 `db_*`；没有保存过时会退回默认库 `jianji`。
一旦升级的库与运行中的库不一致，界面可能显示成功而实际库没有变化。
**升级错误的库比不升级更危险。**

所以改由后端执行：它手里就是运行中的那份连接，`DATABASE_URL` 已经在进程环境里，
`alembic` 用的必然是同一个库。

## 安全

- 要求管理员（与「用户与权限」同一条依赖），不对普通馆员开放；
- 迁移是不可逆操作，接口只做「跑到 head」，不接受任意 revision 参数
  （否则等于给了个「把库降到任意版本」的远程开关）；
- 执行结果原样返回 stdout，失败时把最后几行带给调用方（排障要看）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..core.route_meta import api

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

BACKEND_DIR = Path(__file__).resolve().parents[2]

#: 启动器调用时带这个头（它不持有登录会话）。
LAUNCHER_HEADER = "X-Launcher-Local"


def _launcher_local(request: Request) -> bool:
    """是不是「本机启动器」在调。

    启动器是现场唯一入口，但它没有登录会话——让它走管理员登录反而要在启动器里
    存口令，那是更差的安全取舍。这里放行的条件极窄：

    - 请求来自**回环地址**（`scope["client"]` 是 127.0.0.1 / ::1）；
    - 带启动器标记头；
    - **只在服务绑在回环上时**才认（`lan_mode` 为假）：一旦对外开放，同网段
      任何人都能伪造这个头，那时必须走管理员会话。
    """
    from ..core.deployment import lan_mode

    if lan_mode():
        return False
    if request.headers.get(LAUNCHER_HEADER) != "1":
        return False
    client = request.client.host if request.client else ""
    return client in ("127.0.0.1", "::1", "localhost")


def _require_admin_or_launcher(request: Request,
                               db: Session = Depends(get_db)):
    """管理员会话，或本机启动器。两者都不满足 → 401/403。

    不用 `Depends(require_admin)` 直接挂：那条依赖在「启动器调用」时也会先跑一遍
    会话解析并抛 401，轮不到我们判断启动器。所以先判启动器，再手工走一遍
    「当前用户 → 是否管理员」。
    """
    if _launcher_local(request):
        return None
    from ..services import auth as svc
    from .auth import current_user

    who = current_user(request=request, db=db)
    if not svc.user_is_admin(db, who):
        raise HTTPException(403, "需要管理员权限")
    return who


@router.post("/upgrade-schema")
@api(module="users")
def upgrade_schema(request: Request, db: Session = Depends(get_db),
                   _who=Depends(_require_admin_or_launcher)):
    """跑 `alembic upgrade head`（用**本进程**的连接串）。"""
    from .meta import _code_head

    env = dict(os.environ)
    # 显式传当前连接串：子进程不会自己猜到 DATABASE_URL 是怎么拼出来的
    # （口令可能来自 pgpass / 环境变量 / 启动器注入）。
    from ..db import DATABASE_URL
    env["JIANJI_DATABASE_URL"] = DATABASE_URL
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_DIR), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "升级超时（10 分钟）。大表建索引可能更久，请到命令行执行。")

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        # 只回最后 20 行：alembic 的堆栈很长，前端弹窗放不下
        tail = "\n".join(output.splitlines()[-20:])
        raise HTTPException(500, f"升级失败（退出码 {proc.returncode}）：\n{tail}")

    return {"ok": True, "head": _code_head(), "output": output[-4000:],
            "message": "数据库结构已升级到最新"}
