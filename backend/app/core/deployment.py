# -*- coding: utf-8 -*-
"""部署形态探测：这次启动到底暴露给谁。

## 为什么需要这个模块

单机模式（只绑 127.0.0.1）按零配置方式使用；一旦绑定到非回环地址，
必须先完成管理员创建，再对外提供服务。本模块负责判断本次启动是否对外监听。

## 为什么不能靠 `request.client.host` 判断来源

局域网形态下前端是 Vite 代理（`vite.config.ts` 的 `server.proxy`），后端看到
的每一个请求源地址都是 127.0.0.1。用来源地址判断「是不是本机」，在局域网
部署下会**恒为真**，等于没有判断。

## 两个互补的信号，取「或」

1. **声明的绑定地址**（`lan_mode()`）：命令行 `--host`、环境变量
   `UVICORN_HOST`、或启动器注入的 `JIANJI_LAN_MODE`。这是「启动方式说
   要绑哪」，不依赖任何请求信息。
2. **实际接住连接的本地地址**（`request_from_lan()`）：ASGI 的
   `scope["server"]`。直连网卡进来时它是 192.168.x.x，回环进来时是 127.0.0.1。
   它能兜住「启动参数没解析对」的情况。

两者都不能单独用：
  - 只用 1：启动方式被包装脚本改写、或用 uvicorn 编程接口启动时会漏判；
  - 只用 2：经前端代理进来的请求，代理连的是 127.0.0.1，永远是回环。

## 解析启动参数时的三个坑（都实测踩过）

  - `--host` 出现多次时，**argparse 取最后一个**。取第一个会在
    `--host 127.0.0.1 --host 0.0.0.0` 这种写法上判反。
  - `UVICORN_HOST` 是 uvicorn 官方支持的环境变量，不走命令行。
  - `--host ""`（空串）**不是**「没指定」，而是 `bind(("", port))` =
    INADDR_ANY，即对外。把它当回环处理是反的。
"""
from __future__ import annotations

import ipaddress
import os
import sys

#: 回环地址的各种写法。
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1"}

#: 明确表示「绑定所有网卡」的写法，一律视为对外。
_WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "*"}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _hosts_from_argv(argv: list[str] | None = None) -> list[str]:
    """取出命令行里所有 `--host` 的值（按出现顺序，支持 `--host X` 与 `--host=X`）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    out: list[str] = []
    for i, arg in enumerate(args):
        if arg == "--host" and i + 1 < len(args):
            out.append(args[i + 1])
        elif arg.startswith("--host="):
            out.append(arg.split("=", 1)[1])
    return out


def _declared_host() -> str | None:
    """启动参数里声明的绑定地址；三个来源都取不到时返回 None。"""
    hosts = _hosts_from_argv()
    if hosts:
        return hosts[-1]                      # argparse：最后一个生效
    env = os.environ.get("UVICORN_HOST")
    if env is not None:
        return env
    return None


def _is_external(host: str) -> bool:
    """这个绑定地址是否对外（非回环）。"""
    normalized = (host or "").strip().lower()
    if normalized == "":
        # 空串 = bind(("", port)) = INADDR_ANY。看着像「没指定」，实际是对外。
        return True
    if normalized in _WILDCARD_HOSTS:
        return True
    if normalized in _LOOPBACK_HOSTS:
        return False
    return True                               # 具体网卡 IP / 域名 → 对外


def lan_mode() -> bool:
    """本次进程是否**声明**要暴露到非回环地址。"""
    if _truthy(os.environ.get("JIANJI_LAN_MODE")):
        return True
    host = _declared_host()
    if host is None:
        return False                          # uvicorn 默认 127.0.0.1
    return _is_external(host)


def request_from_lan(scope) -> bool:
    """这条连接是否从非回环的本地地址进来的（兜底信号）。

    看的是 `scope["server"]`——**本地**套接字地址，不是 `client`。局域网里的
    机器直连网卡时它是 `192.168.x.x`，走代理或本机访问时是 `127.0.0.1`。

    只认**能解析成 IP** 的地址：uvicorn 从 `sockname` 拿到的永远是 IP，而测试
    客户端之类会给出 `testserver` 这种主机名。拿到主机名时不敢下结论（返回
    False，交回 `lan_mode()`），免得把「没解析出来」当成「对外」而误锁。
    """
    server = (scope or {}).get("server")
    if not server:
        return False
    addr = str(server[0] or "").strip().lower()
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return not ip.is_loopback


def describe() -> dict:
    """给 `/api/health` 与启动日志用的一句话描述。不含任何敏感信息。"""
    host = _declared_host()
    if host is None:
        shown = "(默认 127.0.0.1)"
    elif host.strip() == "":
        shown = "(全部网卡)"
    else:
        shown = host
    return {
        "lan_mode": lan_mode(),
        "bind_host": shown,
        "source": ("env" if _truthy(os.environ.get("JIANJI_LAN_MODE"))
                   else ("argv" if _hosts_from_argv()
                         else ("uvicorn_host" if host is not None else "default"))),
    }
