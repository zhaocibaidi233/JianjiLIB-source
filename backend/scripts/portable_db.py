# -*- coding: utf-8 -*-
"""便携 PostgreSQL 的命令行助手。

由启动器调用；只负责 initdb / pg_ctl / pg_isready，不承载业务逻辑。
口令只从 JIANJI_PG_PASSWORD 读取，不出现在命令行或日志中。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PG_BIN = ROOT / "runtime" / "pgsql" / "bin"
DEFAULT_PORT = 55432
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class PortableDbError(RuntimeError):
    pass


def pg_bin_dir() -> Path:
    return Path(os.environ.get("JIANJI_PG_BIN", str(DEFAULT_PG_BIN)))


def pg_bin(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    path = pg_bin_dir() / (name + suffix)
    if not path.exists():
        raise PortableDbError(f"找不到 PostgreSQL 工具：{path}")
    return path


def resolve_data_dir(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    # 口令只经 initdb 的临时 pwfile 传递；initdb/pg_ctl/pg_isready 都不需要它，
    # 不能让它随环境块泄漏给子进程。
    env.pop("JIANJI_PG_PASSWORD", None)
    env["PATH"] = str(pg_bin_dir()) + os.pathsep + env.get("PATH", "")
    return env


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
        env=_child_env(),
    )


def _run_detached(argv: list[str], timeout: int = 120) -> tuple[int, str]:
    """跑会留下后台进程的命令（pg_ctl start），输出写临时文件。

    pg_ctl 启动的 postmaster 会继承它的标准句柄：用管道收集输出时写端一直被
    postmaster 攥着，读端等不到 EOF，Windows 上会把调用方永久挂住（连
    subprocess 自己的 timeout 都退不出来——超时后的二次 communicate 仍在等
    EOF）。输出落到临时文件就不存在 EOF 语义，句柄随 pg_ctl 退出即关闭。
    """
    with tempfile.TemporaryFile() as out:
        cp = subprocess.run(
            argv,
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            env=_child_env(),
        )
        out.seek(0)
        text = out.read().decode("utf-8", errors="replace")
    return cp.returncode, text


def _tail(path: Path, lines: int = 20) -> str:
    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(rows[-lines:])


def _initdb_argv(data: Path, pwfile: Path, initdb: Path | None = None) -> list[str]:
    exe = initdb or pg_bin("initdb")
    return [
        str(exe), "-D", str(data), "-U", "postgres",
        "-A", "scram-sha-256", f"--pwfile={pwfile}",
        "-E", "UTF8", "--locale=C",
        "--locale-provider=icu", "--icu-locale=zh-CN",
    ]


def _pgctl_start_argv(data: Path, port: int, log: Path,
                     pg_ctl: Path | None = None) -> list[str]:
    exe = pg_ctl or pg_bin("pg_ctl")
    return [
        str(exe), "-D", str(data), "-l", str(log),
        "-o", f"-p {port} -c listen_addresses=127.0.0.1",
        "-w", "-t", "60", "start",
    ]


def _pgctl_status_argv(data: Path) -> list[str]:
    return [str(pg_bin("pg_ctl")), "-D", str(data), "status"]


def _pgctl_stop_argv(data: Path) -> list[str]:
    return [str(pg_bin("pg_ctl")), "-D", str(data), "-m", "fast",
            "-w", "-t", "60", "stop"]


def _runtime_major() -> str:
    cp = _run([str(pg_bin("postgres")), "--version"], timeout=15)
    if cp.returncode != 0:
        raise PortableDbError("无法读取随包 PostgreSQL 版本")
    match = re.search(r"(\d+)\.", cp.stdout)
    if not match:
        raise PortableDbError("随包 PostgreSQL 版本格式无法解析")
    return match.group(1)


def _version_mismatch(data: Path) -> str:
    try:
        actual = (data / "PG_VERSION").read_text(
            encoding="utf-8", errors="replace").strip().split(".")[0]
    except OSError:
        return ""
    expected = _runtime_major()
    if actual and actual != expected:
        return (f"数据目录由 PostgreSQL {actual} 创建，"
                f"当前运行时为 {expected}；请先备份并导出恢复")
    return ""


def _discard_partial_data_dir(data: Path) -> bool:
    """删除 initdb 失败后残留的半成品数据目录。

    只在目录里没有 PG_VERSION（没有可用的数据目录）时动手；返回是否已清空。
    """
    if (data / "PG_VERSION").exists():
        return False
    if not data.is_dir():
        return not data.exists()
    try:
        shutil.rmtree(data)
    except OSError:
        return False
    return not data.exists()


def is_ready(port: int) -> bool:
    try:
        return _run([str(pg_bin("pg_isready")), "-h", "127.0.0.1",
                     "-p", str(port)], timeout=10).returncode == 0
    except Exception:
        return False


def _status_code(data: Path) -> int:
    if not (data / "PG_VERSION").exists():
        return 4
    try:
        return _run(_pgctl_status_argv(data), timeout=15).returncode
    except Exception:
        return 4


def init_cluster(data: Path) -> str:
    if (data / "PG_VERSION").exists():
        return "already initialized"
    if data.exists() and any(data.iterdir()):
        raise PortableDbError(f"数据目录非空且不是 PostgreSQL 数据目录：{data}")
    # 走到这里数据目录必定缺失或为空，失败时里面的内容都是本次 initdb 留下的。
    was_empty = not data.exists() or not any(data.iterdir())
    data.parent.mkdir(parents=True, exist_ok=True)
    password = os.environ.get("JIANJI_PG_PASSWORD", "")
    if not password:
        raise PortableDbError("初始化便携库需要 JIANJI_PG_PASSWORD")
    fd, name = tempfile.mkstemp(prefix="jianji-pg-", suffix=".txt")
    pwfile = Path(name)
    try:
        os.close(fd)
        pwfile.write_text(password + "\n", encoding="utf-8")
        cp = _run(_initdb_argv(data, pwfile), timeout=180)
        if cp.returncode != 0:
            detail = (cp.stdout + "\n" + cp.stderr).strip()[-2000:]
            message = f"initdb 失败：{detail}"
            if was_empty and _discard_partial_data_dir(data):
                message += "\n已清理失败的数据目录，可重试"
            raise PortableDbError(message)
    finally:
        pwfile.unlink(missing_ok=True)
    if not (data / "PG_VERSION").exists():
        raise PortableDbError(f"initdb 未生成数据目录：{data}")
    return "initialized"


def start_cluster(data: Path, port: int, log: Path) -> str:
    if not (data / "PG_VERSION").exists():
        raise PortableDbError(f"便携库尚未初始化：{data}")
    mismatch = _version_mismatch(data)
    if mismatch:
        raise PortableDbError(mismatch)
    # 端口就绪不等于这个数据目录在跑：同端口上可能有别人的 PostgreSQL。
    # 归属只认 pg_ctl status -D <data>。
    status = _status_code(data)
    if status == 0:
        for _ in range(30):
            if is_ready(port):
                return "already running"
            subprocess.run(["ping", "-n", "2", "127.0.0.1"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        raise PortableDbError("PostgreSQL 进程存在但端口未就绪；请查看日志")
    if is_ready(port):
        raise PortableDbError(
            f"端口 {port} 已被另一个 PostgreSQL 实例占用（不属于数据目录 {data}）；"
            "请为便携库选择其他端口后重试")
    log.parent.mkdir(parents=True, exist_ok=True)
    code, output = _run_detached(_pgctl_start_argv(data, port, log), timeout=120)
    if code != 0:
        detail = output.strip()[-2000:]
        raise PortableDbError(f"pg_ctl start 失败：{detail}\n{_tail(log)}")
    for _ in range(30):
        if is_ready(port):
            return "started"
        subprocess.run(["ping", "-n", "2", "127.0.0.1"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    raise PortableDbError(f"PostgreSQL 启动超时；请查看日志：{log}")


def stop_cluster(data: Path) -> str:
    if not (data / "PG_VERSION").exists():
        return "not initialized"
    if _status_code(data) != 0:
        return "not running"
    cp = _run(_pgctl_stop_argv(data), timeout=120)
    if cp.returncode != 0:
        detail = (cp.stdout + "\n" + cp.stderr).strip()[-2000:]
        raise PortableDbError(f"pg_ctl stop 失败：{detail}")
    return "stopped"


def status(data: Path, port: int) -> dict[str, object]:
    initialized = (data / "PG_VERSION").exists()
    version = ""
    if initialized:
        try:
            version = (data / "PG_VERSION").read_text(
                encoding="utf-8", errors="replace").strip()
        except OSError:
            version = ""
    # 只有这个数据目录确实有进程在跑、且端口就绪，才算 running；
    # 单看端口会被同端口的其他 PostgreSQL 骗到。
    running = bool(initialized and _status_code(data) == 0 and is_ready(port))
    return {"ok": True, "action": "status", "data_dir": str(data),
            "port": port, "initialized": initialized, "running": running,
            "version": version, "message": "ready" if running else "stopped"}


def ensure(data: Path, port: int, log: Path) -> str:
    if not (data / "PG_VERSION").exists():
        init_cluster(data)
    return start_cluster(data, port, log)


def _pin_stdout_utf8() -> None:
    """把 stdout 固定为 UTF-8。

    Windows 下重定向的 stdout 默认用控制台代码页（GBK），启动器按 UTF-8
    解码最后一行 JSON 就会看到乱码；这里在打印前先切到 UTF-8。
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="简籍便携 PostgreSQL")
    parser.add_argument("action", choices=("init", "start", "stop", "status", "ensure"))
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log", default="")
    args = parser.parse_args(argv)
    data = resolve_data_dir(args.data_dir)
    log = Path(args.log).resolve() if args.log else data.parent / "postgresql.log"
    result: dict[str, object] = {"ok": False, "action": args.action,
                                 "data_dir": str(data), "port": args.port,
                                 "message": ""}
    try:
        if args.action == "init":
            result["message"] = init_cluster(data)
        elif args.action == "start":
            result["message"] = start_cluster(data, args.port, log)
        elif args.action == "stop":
            result["message"] = stop_cluster(data)
        elif args.action == "ensure":
            result["message"] = ensure(data, args.port, log)
        else:
            result.update(status(data, args.port))
        result["ok"] = True
    except Exception as exc:
        result["message"] = str(exc)
    _pin_stdout_utf8()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
