# -*- coding: utf-8 -*-
"""元信息：健康检查。"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.engine import make_url

from ..db import DATABASE_URL, SessionLocal
from ..core.route_meta import api

router = APIRouter(tags=["meta"])


@lru_cache(maxsize=1)
def _code_head() -> str:
    """代码要求的迁移版本（读一次就够，进程生命周期内不变）。"""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from pathlib import Path

        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        return ScriptDirectory.from_config(cfg).get_current_head() or ""
    except Exception:
        return ""


def schema_state() -> dict:
    """库结构是否落后于代码。

    代码升级后如果数据库没有执行迁移，应用可能仍能启动、页面也能打开，
    但访问新增表或新增列的接口会失败。这里把结论算成两个字段，供启动器直接展示。
    """
    head = _code_head()
    current = None
    try:
        with SessionLocal() as db:
            row = db.execute(text("SELECT version_num FROM alembic_version")).first()
        current = row[0] if row else None
    except Exception:
        pass
    return {"head": head, "current": current or "",
            # 读不到 head（打包漏了 alembic 目录）时不报警，避免假阳性
            "ok": (not head) or current == head}


def _is_loopback(request: Request) -> bool:
    """请求是否来自本机。测试客户端使用 testclient 主机名，一并视为本机。"""
    host = (request.client.host if request.client else "") or ""
    if host in {"testclient", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@router.get("/health")
@api(public=True)
def health(request: Request):
    db_ok = True
    try:
        # 使用 with 保证连接在异常时也能归还，避免连接泄漏。
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    from ..core.deployment import lan_mode
    from ..version import __version__
    target = make_url(DATABASE_URL)
    schema = schema_state() if db_ok else {"ok": True, "head": "", "current": ""}
    payload = {
        "status": "ok",
        "db": db_ok,
        "version": __version__,
        # 库结构是否落后，启动器据此提示升级。
        "schema_ok": bool(schema["ok"]),
    }
    # 数据库定位信息只在回环请求中返回，避免局域网内任意终端探测库地址。
    if _is_loopback(request):
        payload.update({
            "lan_mode": lan_mode(),
            "db_name": target.database or "",
            "db_host": target.host or "",
            "db_port": target.port or 5432,
            "schema_head": schema["head"],
            "schema_current": schema["current"],
        })
    return payload
