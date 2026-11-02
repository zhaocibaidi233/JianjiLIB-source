# -*- coding: utf-8 -*-
"""外部 PostgreSQL 资源库套录源：**直连另一个本地数据库**只读检索。

## 它解决什么问题

本系统自带的「本地资源库」只查**本系统连接串**里的 `res` schema（见 `local.py` /
`pg_store.py`）。但编目员手上常还有**另一个** PostgreSQL 库 —— 可能是同一套工具在
别的机器 / 别的校区跑的库，可能是共享的一份大资源库。把它们的数据整批搬进来既慢、
又会过时；**直连现查**拿到的才是当下那一份。

所以本模块把"另一个库"做成一种**套录源**（`type="pg"`）：连接参数与表/列映射全部
在「全局设置 → 套录源」里手填，检索时现连现查。

## 三条硬规矩

1. **只读**：只发 `SELECT`，绝不写对方库（对方可能是别的系统正在用的生产库）。
2. **不静默降级**：连不上 / 表不存在 / 列名写错 —— 一律抛错，由 `search_tiered`
   转成"源错误"候选显示给编目员。**绝不能因为外部库出问题就当"这本书没有"**，
   那会让人重复编目（与 `base.search_tiered` 的既有约定一致）。
3. **标识符白名单校验**：模式名 / 表名 / 列名来自用户输入、要拼进 SQL，必须先过
   `_IDENT_RE`（`[A-Za-z_][A-Za-z0-9_$]*`）。值一律走绑定参数 —— 拼进 SQL 的
   只有**校验过的标识符**，没有任何用户数据。

## 记录形态（`extra.marc_format`，默认 `auto`）

对方库的表不一定是本系统的形状，按下列顺序**自动**识别：

| 形态 | 判定 | 转换 |
|---|---|---|
| `iso` | `marc_col` 有值且是 `bytea`（ISO 2709 原始字节） | `io.iso.read_iso()` 解析首条 |
| `fields` | `fields_col` 有值（JSONB / JSON 文本），配 `leader_col` | 复用 `services.convert.orm_to_record()` |
| `columns` | 上面都没有 | 由 isbn/title/author/出版/分类列**合成**一条最小 CNMARC |

前两种正是本系统 `records` / `res.resource_records` 的形状 —— 所以"另一套本系统"
这个最常见的情形**零配置**就能用（默认值即 `res.resource_records`）。
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from ..core import isbn as isbnlib
from ..core.marc import Field, Record, Subfield
from ..db import like_escape
from .base import Candidate, CatalogSource

# 标识符（模式/表/列名）白名单。**这些名字会被拼进 SQL**，所以只放行最保守的形态；
# 带引号 / 分号 / 空格的"名字"一律拒绝 —— 那是注入，不是表名。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# 记录形态（`extra.marc_format`）
FORMATS = ("auto", "iso", "fields", "columns")

# 裸口令整串替换的**最短长度**。比这短的口令（`p` / `a1`）会命中正常单词里的字母，
# 把报错糊成 `***syco***g2`（实测踩到）—— 短口令靠"连接串精确替换"兜住即可。
_MIN_SCRUB_LEN = 6

# 各列的默认名 —— 与 `models.RecordORM` / `models.ResourceRecordORM` 对齐，
# 于是「指到另一套本系统的库」这种情况直接用默认值即可。
DEFAULT_MAP = {
    "schema": "res",
    "table": "resource_records",
    "id_col": "id",
    "isbn_col": "isbn_norm",
    "title_col": "title_norm",
    "author_col": "author_norm",
    "leader_col": "leader",
    "fields_col": "fields",
    "marc_col": "marc_raw",
    "publisher_col": "",
    "year_col": "",
    "class_col": "",
}


def _ident(name, what: str) -> str | None:
    """校验并返回一个 SQL 标识符；空/None → None；非法 → ValueError。

    `name` 先 `str()` 再判：配置来自 JSONB，用户完全可能填成数字或其他类型，
    此时应给出清楚的"不是合法标识符"，而不是 `AttributeError: 'int' has no strip`。
    """
    name = str(name).strip() if name is not None else ""
    if not name:
        return None
    if not _IDENT_RE.match(name):
        raise ValueError(f"{what}不是合法的数据库标识符：{name!r}"
                         f"（只允许字母/数字/下划线，且不以数字开头）")
    return name


# 引擎缓存：连接串 → Engine。**不用 `lru_cache`** —— 需要一个能遍历、能 `dispose()`
# 的缓存：测试收尾要断开对方库的连接（否则测试库 `DROP DATABASE` 会被占用的连接挡住），
# 生产上换了连接参数也该能把旧引擎关掉。
_ENGINES: dict[tuple[str, int], Engine] = {}
_ENGINE_CACHE_MAX = 32


def _engine_for(url_str: str, timeout: int) -> Engine:
    """按连接串取（或建）引擎。`create_engine` 本身不连接，首次查询才连。

    缓存是为了套录一次检索里"ISBN→题名→责任者"三次查询复用同一个连接池；
    `pool_pre_ping` 让被对方重启掐断的连接自动重连。
    """
    key = (url_str, max(1, int(timeout)))
    eng = _ENGINES.get(key)
    if eng is None:
        if len(_ENGINES) >= _ENGINE_CACHE_MAX:      # 淘汰最旧的一条（dict 保序）
            _, old = _ENGINES.popitem(last=False)
            old.dispose()
        eng = _ENGINES[key] = create_engine(
            url_str, pool_pre_ping=True, pool_size=2, max_overflow=0,
            connect_args={"connect_timeout": key[1]})
    return eng


def dispose_engines() -> None:
    """关闭并清空全部外部库引擎（测试收尾 / 连接参数变更后调用）。"""
    for eng in list(_ENGINES.values()):
        try:
            eng.dispose()
        except Exception:
            pass
    _ENGINES.clear()


def build_url(host: str, port, database: str, user: str, password: str,
              sslmode: str = "") -> URL:
    """拼 PostgreSQL 连接串。用 `URL.create` 而非字符串拼接 —— 口令里的
    `@` / `:` / `/` 等字符会被自动转义（手拼会连到错误的库）。"""
    query = {"sslmode": sslmode} if (sslmode or "").strip() else {}
    return URL.create("postgresql+psycopg2",
                      username=user or None,
                      password=password or None,
                      host=host or "localhost",
                      port=int(port) if port else 5432,
                      database=database or None,
                      query=query)


def config_to_url(cfg) -> URL:
    """从 `SourceConfig` 取连接参数（host/port/database/user/password 是通用字段，
    `sslmode` 放 `extra`）。"""
    return build_url(cfg.host, cfg.port, cfg.database, cfg.user, cfg.password,
                     (cfg.extra or {}).get("sslmode", ""))


class PgExternalSource(CatalogSource):
    """直连外部 PostgreSQL 库的套录源（只读）。

    配置（`SourceConfig`）：`host` / `port` / `database` / `user` / `password`
    为通用连接字段；表与列映射放 `extra`（见 `DEFAULT_MAP`）。
    """

    # ---------- 配置读取 ----------

    @property
    def _x(self) -> dict:
        return self.config.extra or {}

    def _opt(self, key: str, default: str = "") -> str:
        v = self._x.get(key)
        return default if v is None else str(v).strip()

    def _col(self, key: str) -> str | None:
        """取一个列名（默认值来自 `DEFAULT_MAP`），空串表示"该表没有这一列"。"""
        raw = self._x.get(key)
        if raw is None:
            raw = DEFAULT_MAP.get(key, "")
        return _ident(raw, f"列「{key}」")

    def table_ref(self) -> str:
        schema = _ident(self._x.get("schema") or DEFAULT_MAP["schema"], "模式名")
        table = _ident(self._x.get("table") or DEFAULT_MAP["table"], "表名")
        return f'"{schema}"."{table}"'

    def _timeout(self) -> int:
        try:
            return max(1, int(self._x.get("connect_timeout", 5)))
        except (TypeError, ValueError):
            return 5

    def _engine(self) -> Engine:
        url = config_to_url(self.config)
        return _engine_for(url.render_as_string(hide_password=False), self._timeout())

    # ---------- 检索 ----------

    def _fetch(self, sql: str, params: dict) -> list:
        try:
            with self._engine().connect() as conn:
                return list(conn.execute(text(sql), params).fetchall())
        except Exception as e:
            # 消息里可能带上连接串（某些驱动会把 URL 回显），口令一律抹掉
            raise ConnectionError(f"[{self.name}] 外部库查询失败：{self._scrub(e)}") from e

    def _scrub(self, e: Exception) -> str:
        """把错误消息里可能出现的口令抹掉，**但别把消息本身毁掉**。

        ⚠️ 教训：整串替换对**短口令**是灾难 —— 口令 `p` 会把
        `psycopg2.OperationalError` 换成 `***syco***g2`、`port` 换成 `***ort`，
        报错再也读不懂。所以分两步：
          1. 连接串（驱动偶尔会把 DSN 带进异常文本）**精确替换**成脱敏形态 ——
             与口令长短无关，且能处理百分号编码；
          2. 裸口令只在**足够长**时才整串替换（`_MIN_SCRUB_LEN`）。
        """
        msg = str(e).strip() or e.__class__.__name__
        try:
            url = config_to_url(self.config)
            full = url.render_as_string(hide_password=False)
            if full in msg:
                msg = msg.replace(full, url.render_as_string(hide_password=True))
        except Exception:
            pass
        pwd = self.config.password or ""
        if len(pwd) >= _MIN_SCRUB_LEN and pwd in msg:
            msg = msg.replace(pwd, "***")
        return msg

    def search(self, isbn=None, title=None, author=None, limit=10,
               offset=0) -> list[Candidate]:
        """ISBN 精确 → 题名模糊 → 责任者模糊，**前者命中就不往后查**。

        与 `LocalLibrarySource.search` 同一套顺序（一个输入框吃 ISBN/题名/责任者）。

        翻页：`offset` 下推成 SQL `OFFSET`。注意**第 2 页起不再回退到下一路检索**
        ——第一路在这一页取空了，只说明"这一页没有"，不代表"整条路没命中"，
        回退会让翻页突然跳到题名结果上去。
        """
        limit = max(1, int(limit or 10))
        offset = max(0, int(offset or 0))
        rows: list = []
        if isbn:
            forms = isbnlib.all_forms(isbn)
            if forms:
                rows = self._query_isbn(forms, limit, offset)
        if not rows and offset == 0 and title and (title or "").strip():
            rows = self._query_like("title_col", title, limit, offset)
        if not rows and offset == 0 and author and (author or "").strip():
            rows = self._query_like("author_col", author, limit, offset)
        return [self._to_candidate(r) for r in rows[:limit]]

    def _query_isbn(self, forms: set[str], limit: int, offset: int = 0) -> list:
        col = self._col("isbn_col")
        if not col:
            return []
        forms = [f for f in forms if f]
        if not forms:
            return []
        ph = ", ".join(f":f{i}" for i in range(len(forms)))
        sql = (f'SELECT * FROM {self.table_ref()} '
               f'WHERE "{col}" IN ({ph}) ORDER BY 1 LIMIT :lim OFFSET :off')
        params = {f"f{i}": v for i, v in enumerate(forms)}
        params["lim"] = limit
        params["off"] = offset
        return self._fetch(sql, params)

    def _query_like(self, key: str, term: str, limit: int, offset: int = 0) -> list:
        col = self._col(key)
        term = (term or "").strip()
        if not col or not term:
            return []
        # 转义 % _ \，让检索词按字面匹配（与 db.like_escape 同一口径）
        sql = (f'SELECT * FROM {self.table_ref()} '
               f'WHERE "{col}" ILIKE :q ESCAPE \'\\\' ORDER BY 1 LIMIT :lim OFFSET :off')
        return self._fetch(sql, {"q": f"%{like_escape(term)}%", "lim": limit,
                                 "off": offset})

    # ---------- 行 → Record ----------

    def _to_candidate(self, row) -> Candidate:
        rec = self._row_to_record(row)
        m = row._mapping
        id_col = self._col("id_col")
        rid = m.get(id_col) if id_col else None
        return Candidate(
            source_name=self.name,
            source_key=f"pg-{rid if rid is not None else rec.title() or '?'}",
            title=rec.title(),
            author=rec.first_value("200", "f"),
            publisher=rec.first_value("210", "c"),
            year=rec.first_value("210", "d"),
            class_no=rec.first_value("690", "a"),
            isbn=rec.first_value("010", "a"),
            record=rec,
        )

    def _row_to_record(self, row) -> Record:
        m = row._mapping
        fmt = (self._opt("marc_format", "auto") or "auto").lower()
        if fmt not in FORMATS:
            fmt = "auto"

        # ① ISO 2709 原始字节
        if fmt in ("auto", "iso"):
            col = self._col("marc_col")
            raw = m.get(col) if col else None
            if isinstance(raw, str):
                raw = raw.encode("gb18030", "replace")
            if isinstance(raw, (bytes, bytearray, memoryview)) and bytes(raw).strip():
                rec = _first_iso_record(bytes(raw))
                if rec is not None:
                    return rec

        # ② leader + fields（JSONB / JSON 文本）
        if fmt in ("auto", "fields"):
            col = self._col("fields_col")
            raw = m.get(col) if col else None
            if raw:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "replace")
                if isinstance(raw, str):
                    raw = json.loads(raw)
                if isinstance(raw, list) and raw:
                    leader_col = self._col("leader_col")
                    leader = (m.get(leader_col) if leader_col else None) or " " * 24
                    return _record_from_fields(leader, raw)

        # ③ 由列合成最小 CNMARC（对方库不是 MARC 时的兜底）
        return self._synth_record(m)

    def _synth_record(self, m) -> Record:
        """由普通列合成一条最小 CNMARC：010$a / 200$a+$f / 210$c+$d / 690$a。

        只有对方库确实没有 MARC 数据时才走到这里。字段按 tag 升序追加，
        符合 ISO 2709 对目次顺序的期望（见 `实现说明`）。
        """
        rec = Record()

        def val(key: str):
            col = self._col(key)
            v = m.get(col) if col else None
            return "" if v is None else str(v).strip()

        isbn, title, author = val("isbn_col"), val("title_col"), val("author_col")
        publisher, year, class_no = val("publisher_col"), val("year_col"), val("class_col")

        if isbn:
            rec.fields.append(Field("010", subfields=[Subfield("a", isbn)]))
        if title or author:
            f200 = Field("200")
            if title:
                f200.add("a", title)
            if author:
                f200.add("f", author)
            rec.fields.append(f200)
        if publisher or year:
            f210 = Field("210")
            if publisher:
                f210.add("c", publisher)
            if year:
                f210.add("d", year)
            rec.fields.append(f210)
        if class_no:
            rec.fields.append(Field("690", subfields=[Subfield("a", class_no)]))
        return rec

    def fetch(self, candidate: Candidate) -> Record:
        assert candidate.record is not None
        return candidate.record

    # ---------- 探活 ----------

    def health(self) -> dict:
        """探活：连上 + 数一下目标表有多少条。表不存在不算"连不上"，分开报。"""
        base = {"name": self.name, "type": "pg", "enabled": self.config.enabled}
        try:
            with self._engine().connect() as conn:
                server = conn.execute(text("SELECT version()")).scalar()
        except Exception as e:
            return {**base, "status": "fail", "detail": self._scrub(e)}
        out = {**base, "status": "ok", "server": (server or "")[:80],
               "host": self.config.host, "database": self.config.database,
               "table": f"{self._x.get('schema') or DEFAULT_MAP['schema']}."
                        f"{self._x.get('table') or DEFAULT_MAP['table']}"}
        try:
            with self._engine().connect() as conn:
                out["records"] = conn.execute(
                    text(f"SELECT count(*) FROM {self.table_ref()}")).scalar()
        except Exception as e:
            out["records"] = None
            out["table_error"] = self._scrub(e)
        return out


def _first_iso_record(raw: bytes) -> Record | None:
    from ..io.iso import read_iso
    records, _errors = read_iso(raw)
    return records[0] if records else None


def _record_from_fields(leader, fields) -> Record:
    """`leader` + `fields`(JSONB) → `Record`。

    **复用** `services.convert.orm_to_record`（它就是干这个的），只是喂一个轻量对象：
    两处各写一份 JSONB→Record 的解析迟早会漂（那正是"同类逻辑共用归一函数"的要求）。
    """
    from ..services.convert import orm_to_record
    return orm_to_record(SimpleNamespace(leader=leader, fields=fields))
