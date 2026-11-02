# -*- coding: utf-8 -*-
"""启动器「数据库」页的只读探测工具。

只提供两个动作，绝不写库：
  test  连接目标库，返回服务器版本、目标库是否存在
  list  连接维护库 postgres，列出可选的业务库

连接参数从环境读取（启动器通过子进程环境传入，口令不落在命令行上）：
  JIANJI_DB_HOST / JIANJI_DB_PORT / JIANJI_DB_NAME / JIANJI_DB_USER
  JIANJI_DB_PASSWORD（可选；留空时沿用 PGPASSWORD 或 pgpass.conf）

输出：一行 UTF-8 JSON。退出码 0 = 服务器可达（目标库不存在也算可达），1 = 连接失败。
"""
from __future__ import annotations

import json
import os
import sys


def _env(name: str, default: str) -> str:
    value = (os.environ.get(name) or "").strip()
    return value if value else default


def _port() -> int:
    raw = _env("JIANJI_DB_PORT", "5432")
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"端口不是数字：{raw}")
    if not 1 <= value <= 65535:
        raise SystemExit(f"端口超出范围：{raw}")
    return value


def _password() -> str:
    return os.environ.get("JIANJI_DB_PASSWORD") or ""


def _target(dbname: str) -> dict:
    return {
        "host": _env("JIANJI_DB_HOST", "localhost"),
        "port": _port(),
        "user": _env("JIANJI_DB_USER", "postgres"),
        "dbname": dbname,
    }


def _connect(target: dict):
    import psycopg2
    kwargs = {
        "host": target["host"],
        "port": target["port"],
        "user": target["user"],
        "dbname": target["dbname"],
        "connect_timeout": 5,
    }
    password = _password()
    if password:
        kwargs["password"] = password
    return psycopg2.connect(**kwargs)


def _version(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT version()")
        raw = cur.fetchone()[0]
    return str(raw).split(",", 1)[0].strip()


def _where(target: dict) -> str:
    return f"{target['host']}:{target['port']}/{target['dbname']}"


def _probe_target(target: dict) -> dict:
    """先连目标库；失败时再连维护库，区分「服务/口令不通」和「库不存在」。"""
    try:
        conn = _connect(target)
        try:
            return {
                "ok": True,
                "db_exists": True,
                "version": _version(conn),
                "message": f"连接成功：{_where(target)}",
            }
        finally:
            conn.close()
    except Exception as target_error:
        if target["dbname"] == "postgres":
            return _failure(target, target_error)
        try:
            conn = _connect(_target("postgres"))
        except Exception as server_error:
            return _failure(target, server_error)
        try:
            return {
                "ok": True,
                "db_exists": False,
                "version": _version(conn),
                "message": (f"服务器可连接，但目标库 {target['dbname']} 不存在"
                            "（或当前账号无权访问）。"),
            }
        finally:
            conn.close()


def _failure(target: dict, exc: Exception) -> dict:
    detail = str(exc).strip().replace("\n", " ")[:240]
    message = (f"连接失败：{_where(target)}（用户 {target['user']}）。"
               "请依次检查：① PostgreSQL 服务是否已启动；"
               "② 账号与口令是否正确（pgpass.conf 或 JIANJI_DB_PASSWORD）；"
               "③ 目标库是否已创建。")
    if detail:
        message += f" 详情：{detail}"
    return {"ok": False, "db_exists": False, "version": "", "message": message}


def _list_databases() -> dict:
    target = _target("postgres")
    try:
        conn = _connect(target)
    except Exception as exc:
        return _failure(target, exc)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT datname FROM pg_database "
                "WHERE datistemplate = false ORDER BY datname")
            names = [row[0] for row in cur.fetchall()]
        return {
            "ok": True,
            "databases": names,
            "version": _version(conn),
            "message": f"共 {len(names)} 个数据库",
        }
    except Exception as exc:
        return _failure(target, exc)
    finally:
        conn.close()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    action = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "test"
    if action == "test":
        result = _probe_target(_target(_env("JIANJI_DB_NAME", "postgres")))
    elif action == "list":
        result = _list_databases()
    else:
        result = {"ok": False, "message": f"未知动作：{action}（只支持 test / list）"}

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
