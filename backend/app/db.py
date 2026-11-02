# -*- coding: utf-8 -*-
"""数据库连接。

源码不内置明文口令。口令按以下优先级提供：
  1. JIANJI_DATABASE_URL  —— 完整连接串（生产必须，可含口令）
  2. JIANJI_DB_PASSWORD   —— 仅口令，与默认连接串拼接
  3. libpq 标准口令来源        —— ~/.pgpass（Windows: %APPDATA%\\postgresql\\pgpass.conf）
                                 或 PGPASSWORD 环境变量

本机开发：把一次口令写进 pgpass.conf 即可，既免密又不入库。
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

#: 兜底连接串。未设置 JIANJI_DATABASE_URL 时连接本机默认库 jianji；
#: 已有部署请显式设置连接串，或在启动器里保存数据库配置。
_DEFAULT_URL = "postgresql+psycopg2://postgres@localhost:5432/jianji"


def _build_url() -> str:
    url = os.environ.get("JIANJI_DATABASE_URL")
    if url:
        return url
    pwd = os.environ.get("JIANJI_DB_PASSWORD")
    if pwd:
        return _DEFAULT_URL.replace("postgres@", f"postgres:{pwd}@")
    return _DEFAULT_URL          # 无口令：交给 pgpass / PGPASSWORD


DATABASE_URL = _build_url()


def _timeout_options() -> dict:
    """连接级超时（PG 的 `options=-c key=value`）。

    ## 为什么要设：没有超时的数据库连接会把「慢」变成「挂」

    默认三个都是 0（无限等待）：
    - `statement_timeout`：一条被 planner 估错的查询、或一次大表 UPDATE 可以一直跑
      到天亮，占着连接池（默认 5+10）——几个这样的请求就能让全站排队；
    - `lock_timeout`：等锁没有上限时，一个会话的失误会让后面所有请求**静默堆着**，
      用户看到的是"点了没反应"，日志里什么都没有；
    - `idle_in_transaction_session_timeout`：会话开着事务不干活（前端断开、脚本崩了）
      会一直持有锁与快照，autovacuum 也被拖住。

    ## 为什么默认值是这个量级

    `statement_timeout` 取 10 分钟：正常业务语句都在毫秒级，唯一接近这个量级的是
    大文件导入与建索引，给足余量后仍能在"卡死"和"正常慢操作"之间分开。真要跑更久的
    作业（几十万条迁移），用环境变量临时放宽，而不是把默认值调成 0。

    环境变量：`JIANJI_DB_STATEMENT_TIMEOUT` / `..._LOCK_TIMEOUT` /
    `..._IDLE_TIMEOUT`（PG 时长写法，如 `600s`、`30s`；设 `0` 表示关闭）。
    """
    return {
        "statement_timeout": os.environ.get("JIANJI_DB_STATEMENT_TIMEOUT", "600s").strip(),
        "lock_timeout": os.environ.get("JIANJI_DB_LOCK_TIMEOUT", "30s").strip(),
        "idle_in_transaction_session_timeout":
            os.environ.get("JIANJI_DB_IDLE_TIMEOUT", "120s").strip(),
    }


def _connect_args() -> dict:
    opts = _timeout_options()
    parts = [f"-c {k}={v}" for k, v in opts.items() if v and v != "0"]
    if not parts:
        return {}
    return {"options": " ".join(parts)}


engine = create_engine(DATABASE_URL, pool_pre_ping=True,
                       connect_args=_connect_args())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def like_escape(s: str) -> str:
    """转义 LIKE/ILIKE 通配符（`\\` `%` `_`），让搜索词按**字面**匹配。

    用法：`col.ilike(f"%{like_escape(q)}%", escape="\\\\")`。
    不转义的话：用户输 `%` 会命中一切（还绕过 trgm 索引退化成全表扫描）、
    输 `_` 会单字符通配 —— 搜「100%书」「第_章」这类真实题名时语义就是错的。
    `escape="\\\\"`（Python 字面 `"\\"`）告诉 PG 反斜杠是本查询的转义符。
    """
    if not s:
        return s
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ensure_schemas() -> None:
    """确保 res schema 存在（套录资源库使用独立 schema）。"""
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS res"))
        conn.commit()


def create_all() -> None:
    from sqlalchemy import text
    ensure_schemas()
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.commit()
    import app.models  # noqa: F401
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


#: 连不上数据库时，现场最该按顺序排查的三件事。写死在这里，而不是散落在各处
#: 的 except 里——第一次部署撞上的几乎总是这三条之一。
_DB_TROUBLESHOOT = ("① PostgreSQL 服务是否已启动；"
                    "② 目标库是否已创建（可运行 scripts/init_db.py）；"
                    "③ 口令是否正确（pgpass.conf 或 JIANJI_DB_PASSWORD）")


def _where() -> str:
    """当前连接目标，形如 `localhost:5432/jianji`。**不含口令**。"""
    try:
        from sqlalchemy.engine import make_url
        url = make_url(DATABASE_URL)
        return f"{url.host or 'localhost'}:{url.port or 5432}/{url.database or '?'}"
    except Exception:
        return "(连接串无法解析)"


def connection_hint(exc: BaseException) -> str:
    """把「连不上数据库」翻译成现场能看懂的一句话。

    ## 为什么需要这个函数

    中文 Windows 上装 PostgreSQL，服务端 `lc_messages` 默认是
    `Chinese (Simplified)_China.936`（CP936/GBK），而连接用的编码是 UTF-8。
    两者不一致本身不影响查询（普通 SQL 错误的中文能正常显示），但**连接建立
    阶段**的报错由服务端直接给出、psycopg2 按 UTF-8 解码，于是现场看到的
    不是「口令错误 / 数据库不存在」，而是：

        'utf-8' codec can't decode byte 0xd6 in position 67 ...

    结果就是：管理员按 README 去查日志，看到一串编码错误，于是去查编码问题，
    而真正的原因（服务没起 / 库没建 / 口令错）一个字都没露出来。这三条恰好
    是首次部署最常撞上的。

    ## 为什么不去改服务端编码

    改 `lc_messages` 需要动现场数据库配置（还要重启服务），而本项目只是这个
    库的一个客户端；而且改完只解决这一台机器。在应用侧兜底，对已有部署立即
    生效，也不用碰客户的数据库。
    """
    where = _where()
    text = ""
    try:
        text = str(exc)
    except Exception:
        pass
    if isinstance(exc, UnicodeDecodeError) or "codec can't decode" in text:
        return (f"连接数据库 {where} 失败（服务端报错为 GBK 编码，无法按 UTF-8 解析）。"
                f"请依次检查：{_DB_TROUBLESHOOT}")
    name = type(exc).__name__
    if "OperationalError" in name or "connection" in text.lower():
        return f"连接数据库 {where} 失败：{text}。请依次检查：{_DB_TROUBLESHOOT}"
    return f"数据库操作失败（{name}）：{text}"
