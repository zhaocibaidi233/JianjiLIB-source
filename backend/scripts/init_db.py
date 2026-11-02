# -*- coding: utf-8 -*-
"""一键初始化数据库：建库 -> 表结构（Alembic）-> 灌中图法类表。

**给刚拿到本项目的人用**。表结构走 **Alembic 版本化**（`migrations/`，基线
`63643b01b390` 与 `create_all()` 的产物逐字节等价），只自动处理可确定版本的库：

  - **空库**：`alembic upgrade head` 建全部表；
  - **未登记版本的非空库**：拒绝自动接入，避免跳过真实迁移；
  - **已接入 Alembic 的库**：`alembic upgrade head` 应用未打的版本。

之后的 schema 变更一律 `alembic revision --autogenerate`，不再手写加列脚本
（旧的 `migrate_*.py` 保留只为兜底老库）。

幂等：可反复执行。已建好的库、已加的列、已灌的类表都不会重复处理。

前置条件：
  - 本机已装 PostgreSQL 并已启动
  - 口令按优先级提供（见 app/db.py）：
      1. JIANJI_DATABASE_URL   完整连接串
      2. JIANJI_DB_PASSWORD    仅口令
      3. libpq 标准来源            %APPDATA%\\postgresql\\pgpass.conf 或 PGPASSWORD

用法：
    python backend/scripts/init_db.py                # 初始化（幂等，固定 22 个一级大类）
    python backend/scripts/init_db.py --check        # 只体检，不写任何东西
    python backend/scripts/init_db.py --skip-clc     # 跳过灌类表
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

# Windows 控制台默认 GBK；启动器与 CLI 都按 UTF-8 读取，最后的中文提示
# （含 ⚠）否则会在成功初始化之后抛 UnicodeEncodeError。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _target() -> dict:
    """从 app.db 取连接目标（与运行时完全一致，避免"脚本连的库和程序连的库不是一个"）。"""
    from app.db import DATABASE_URL
    parts = urlsplit(DATABASE_URL)
    return {
        "url": DATABASE_URL,
        "host": parts.hostname or "localhost",
        "port": parts.port or 5432,
        "user": unquote(parts.username or "postgres"),
        "password": unquote(parts.password) if parts.password else None,
        "dbname": unquote((parts.path or "/").lstrip("/")) or "postgres",
    }


def _connect(target: dict, dbname: str):
    import psycopg2
    kw = {"host": target["host"], "port": target["port"], "user": target["user"],
          "dbname": dbname, "connect_timeout": 5}
    if target["password"]:
        kw["password"] = target["password"]
    return psycopg2.connect(**kw)


def _ensure_database(target: dict) -> str:
    """确保目标库存在。返回 'created' / 'exists'。"""
    try:
        conn = _connect(target, "postgres")
    except Exception as e:
        from app.db import connection_hint
        raise SystemExit(
            f"\n连不上 PostgreSQL（{target['host']}:{target['port']}，用户 {target['user']}）。\n"
            f"{connection_hint(e)}\n\n"
            "口令可按以下任一方式提供：\n"
            "   - 环境变量 JIANJI_DB_PASSWORD\n"
            "   - %APPDATA%\\postgresql\\pgpass.conf 写一行：localhost:5432:*:postgres:<口令>\n"
            "   - 环境变量 JIANJI_DATABASE_URL 给完整连接串\n")
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target["dbname"],))
            if cur.fetchone():
                return "exists"
            from psycopg2 import sql
            cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(target["dbname"])))
            return "created"
    finally:
        conn.close()


def _child_env() -> dict:
    """子脚本统一用 UTF-8 输出。

    中文 Windows 的默认编码是 GBK；`text=True` 会用 locale 解码子进程输出，
    而子脚本（seed_clc 等）按 UTF-8 打印中文时就会在这里抛
    UnicodeDecodeError —— 初始化明明成功了一半，父进程却直接崩掉。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(script: str, *args: str) -> int:
    """用同一个解释器跑子脚本（保证依赖与解释器一致）。"""
    path = HERE / script
    if not path.exists():
        print(f"   [跳过] 找不到 {script}")
        return 0
    r = subprocess.run([sys.executable, str(path), *args],
                       cwd=str(BACKEND), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_child_env())
    for line in (r.stdout or "").splitlines():
        print("   " + line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines()[-8:]:
            print("   ! " + line)
    return r.returncode


def _alembic(args: list[str]) -> int:
    """跑 alembic 子命令。连接目标由 migrations/env.py 解析，与本脚本一致。"""
    r = subprocess.run([sys.executable, "-m", "alembic", *args],
                       cwd=str(BACKEND), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_child_env())
    for line in (r.stdout or "").splitlines():
        print("   " + line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines()[-10:]:
            print("   ! " + line)
    return r.returncode


def _scalar(target: dict, sql: str) -> int:
    conn = _connect(target, target["dbname"])
    with conn.cursor() as cur:
        cur.execute(sql)
        n = cur.fetchone()[0]
    conn.close()
    return n


def _table_exists(target: dict, table: str) -> bool:
    """当前 schema 里有没有某张表（只看 public，alembic_version 就在那）。"""
    try:
        return _scalar(target,
                       "SELECT count(*) FROM information_schema.tables "
                       f"WHERE table_schema = current_schema() AND table_name = '{table}'") > 0
    except Exception:
        return False


def _has_user_tables(target: dict) -> bool:
    return _scalar(target,
                   "SELECT count(*) FROM information_schema.tables "
                   "WHERE table_schema IN ('public', 'res')") > 0


def _find_pg_tool(name: str) -> str | None:
    """找 pg_dump/pg_restore：PATH 优先，再找 Windows 默认安装目录。"""
    found = shutil.which(name)
    if found:
        return found
    candidates: list[Path] = []
    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_key)
        if root:
            candidates.extend(Path(root).glob(f"PostgreSQL/*/bin/{name}.exe"))
    return str(sorted(candidates, reverse=True)[0]) if candidates else None


def _backup_database(target: dict, backup_dir: Path) -> Path:
    """pg_dump 非空库并立即用 pg_restore -l 校验；任一步失败都抛异常。"""
    dump_tool = _find_pg_tool("pg_dump")
    restore_tool = _find_pg_tool("pg_restore")
    if not dump_tool:
        raise RuntimeError(
            "找不到 pg_dump。请把 PostgreSQL 的 bin 目录加入 PATH，"
            "或明确使用 --no-backup 跳过备份")
    if not restore_tool:
        raise RuntimeError(
            "找不到 pg_restore，无法校验备份是否可读；请把 PostgreSQL 的 bin "
            "目录加入 PATH，或明确使用 --no-backup")

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"{target['dbname']}_{stamp}.dump"
    env = os.environ.copy()
    if target.get("password"):
        env["PGPASSWORD"] = target["password"]

    dump = subprocess.run(
        [dump_tool, "-h", str(target["host"]), "-p", str(target["port"]),
         "-U", str(target["user"]), "-w", "-Fc", "-f", str(path),
         str(target["dbname"])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if dump.returncode != 0:
        path.unlink(missing_ok=True)
        tail = "\n".join((dump.stderr or "").splitlines()[-6:])
        raise RuntimeError(f"pg_dump 失败：{tail or '退出码 ' + str(dump.returncode)}")

    verify = subprocess.run(
        [restore_tool, "-l", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if verify.returncode != 0:
        path.unlink(missing_ok=True)
        tail = "\n".join((verify.stderr or "").splitlines()[-6:])
        raise RuntimeError(
            f"pg_restore -l 校验失败：{tail or '退出码 ' + str(verify.returncode)}")

    size_mb = path.stat().st_size / 1024 / 1024
    print(f"   已备份：{path}（{size_mb:.1f} MB，已校验可读）")
    return path


def _count_clc(target: dict) -> int:
    """已有多少条类目（0 = 还没灌过）。"""
    try:
        conn = _connect(target, target["dbname"])
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM clc_classes")
            n = cur.fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


#: 固定种子：22 个中图法一级大类。
CLC_SEED_FILE = BACKEND / "app" / "data" / "clc_min.json"


def _clc_target_count() -> int:
    """种子文件应灌的目标条数（读不到文件时 0）。"""
    try:
        rows = json.loads(CLC_SEED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return sum(1 for r in rows if str(r.get("class_no") or "").strip())


def _clc_plan(have: int, target: int, *, force: bool) -> str:
    """按目标条数决定 CLC 种子动作：`seed` / `incremental` / `replace` / `skip`。

    - 空表（或读不到目标数）→ `seed`（全量重灌，空表没有可丢的）；
    - `0 < have < target` → `incremental`（**只补缺**，不覆盖手工补的类名）；
    - `have >= target` → `skip`（多出来的可能是记录登记的自编类号，不清表）；
    - `--force-clc` → `replace`（用户明确要求，保持原行为）。
    """
    if force:
        return "replace"
    if target <= 0:
        return "skip" if have > 0 else "seed"
    if have <= 0:
        return "seed"
    if have < target:
        return "incremental"
    return "skip"


def main() -> int:
    ap = argparse.ArgumentParser(description="一键初始化数据库")
    ap.add_argument("--check", action="store_true", help="只体检，不写任何东西")
    ap.add_argument("--skip-clc", action="store_true", help="跳过灌中图法类表")
    ap.add_argument("--force-clc", action="store_true",
                    help="类表已有内容也强制清空并重灌固定 22 个一级大类")
    ap.add_argument("--no-backup", action="store_true",
                    help="跳过升级前自动备份（不建议；仅供一次性/临时库）")
    ap.add_argument("--backup-dir", default=None,
                    help="备份目录（默认 包根/backups/）")
    args = ap.parse_args()

    target = _target()
    print("目标库 :", f"{target['host']}:{target['port']}/{target['dbname']}",
          f"(用户 {target['user']})")
    print("类表   :", CLC_SEED_FILE)

    if args.check:
        try:
            conn = _connect(target, target["dbname"])
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema IN ('public','res')")
                tables = cur.fetchone()[0]
            conn.close()
            print(f"体检   : 可连接，已有 {tables} 张表")
        except Exception as e:
            from app.db import connection_hint
            print(f"体检   : 目标库不可用。{connection_hint(e)}")
        return 0

    print("\n[1/3] 建库")
    state = _ensure_database(target)
    print("   " + ("已存在，跳过" if state == "exists" else f"已创建 {target['dbname']}"))

    print("\n[2/3] 表结构（Alembic 版本化，基线 migrations/versions/63643b01b390）")
    if state == "exists" and _has_user_tables(target):
        if args.no_backup:
            print("   升级前备份：按 --no-backup 跳过（不建议用于生产库）")
        else:
            print("   现有库有数据，先备份并校验：")
            backup_dir = (Path(args.backup_dir) if args.backup_dir
                          else BACKEND.parent / "backups")
            try:
                _backup_database(target, backup_dir)
            except Exception as e:
                print(f"   备份失败，已中止升级：{e}")
                return 1
    if _table_exists(target, "alembic_version"):
        # 已接入 Alembic：只需应用还没打的版本
        print("   库已接入 Alembic，应用未执行的版本：")
        if _alembic(["upgrade", "head"]) != 0:
            return 1
    elif _has_user_tables(target):
        print("   拒绝初始化：目标库已有业务表，但未登记 Alembic 版本。")
        print("   请使用新的空库，或核对实际结构并完成专门的迁移接入；不能自动标记为最新版。")
        return 1
    else:
        print("   空库：alembic upgrade head 建全部表")
        if _alembic(["upgrade", "head"]) != 0:
            return 1

    if args.skip_clc:
        print("\n[3/3] 灌中图法类表：按参数跳过")
    else:
        # 按**目标条数**判断，不再"有 1 行就跳过"：只灌过简表或灌了一半的库
        # 会被当成完整库，之后分类检索/类名一直缺。部分库走**增量补缺**，
        # 不清表（`seed_clc.py --replace` 会连手工补的类名一起抹掉）。
        have = _count_clc(target)
        target_count = _clc_target_count()
        plan = _clc_plan(have, target_count, force=args.force_clc)
        if plan == "skip":
            print(f"\n[3/3] 灌中图法一级大类：已有 {have} 条（目标 {target_count} 条），跳过"
                  f"（要重灌加 --force-clc）")
        elif plan == "incremental":
            print(f"\n[3/3] 灌中图法一级大类：只有 {have}/{target_count} 条，增量补灌")
            if _run("seed_clc.py") != 0:
                return 1
        else:
            print(f"\n[3/3] 灌中图法一级大类（目标 {target_count} 条）")
            if _run("seed_clc.py", "--replace") != 0:
                return 1

    print("\n完成。下一步：")
    print("   启动     双击根目录 JianjiLauncher.exe（或按 README 第三节用命令行启动）")
    print("   打开     http://127.0.0.1:8000")
    print("")
    print("请先在本机浏览器完成「创建管理员」，再开启「局域网访问」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
