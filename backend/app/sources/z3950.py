# -*- coding: utf-8 -*-
"""Z39.50 套录源——可编辑配置 + yaz-client 子进程封装。

实测结论（历史技术验证记录）：
- 协议与账号已验证：某外部源(dp2Capo)、某外部源(K-Int) 认证+ISBN检索均通过；
- 实现路径：子进程调用 yaz-client（YAZ 5.35），记录经 -m 选项落盘为
  ISO 2709，再由自研解析器（app.io.iso）读取——全链路已实测打通。

全部源参数（host/port/database/user/password）走 SourceConfig，UI 可编辑，
换源不改代码。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..core.marc import Record
from ..io.iso import read_iso
from .base import Candidate, CatalogSource

# Bib-1 Use 属性（Z39.50-1995）
USE_ATTRS = {"isbn": 7, "issn": 8, "title": 4, "author": 1003,
             "subject": 21, "any": 1016, "control_no": 12}

_DEFAULT_YAZ = r"C:\Program Files (x86)\YAZ\bin\yaz-client.exe"
_HITS_RE = re.compile(r"Number of hits:\s*(\d+)")
_AUTH_FAIL_RE = re.compile(r"Bad Userid|Connection rejected|Authentication system error", re.I)
# 检索词/连接参数里的危险字符：控制字符可向 yaz-client 的 stdin 注入额外会话命令，
# `@` 会改变 PQN 查询树，`\` 与 `"` 干扰解析。检索词一律清洗后再拼。
_TERM_BAD = re.compile(r"[\x00-\x1f@\\\"]")


def _clean_term(value: str | None) -> str:
    return _TERM_BAD.sub(" ", value or "").strip()


#: 目标字符集 → 实际用于编码检索词的 Python 编码。
#:
#: `gb2312` / `gbk` 统一用 **gb18030** 编：gb18030 是两者的超集，对 GBK 范围内的字
#: 编码结果完全一致，但能编出 gb2312 编不了的字（`·`、生僻人名用字）——用 gb2312
#: 硬编会直接抛 UnicodeEncodeError，把一条本来能查的书变成"检索失败"。
_CHARSET_ENCODING = {
    "": "gb18030",
    "gb2312": "gb18030", "gbk": "gb18030", "gb18030": "gb18030",
    "cp936": "gb18030", "euc-cn": "gb18030",
    "utf-8": "utf-8", "utf8": "utf-8",
    "big5": "big5", "big5-hkscs": "big5hkscs",
}


def _query_encoding(charset: str | None) -> str:
    key = (charset or "").strip().lower()
    return _CHARSET_ENCODING.get(key, key or "gb18030")


def _yaz_client_path() -> str:
    for cand in (os.environ.get("JIANJI_YAZ_CLIENT"),
                 shutil.which("yaz-client"),
                 _DEFAULT_YAZ):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError(
        "未找到 yaz-client：请安装 YAZ（https://ftp.indexdata.com/pub/yaz/win32/）"
        "或设置环境变量 JIANJI_YAZ_CLIENT 指向 yaz-client.exe")


def _decode(raw: bytes | str | None) -> str:
    """yaz-client 的输出按 UTF-8 解；解不了就用替换符——**绝不能因为解不开就抛**，
    那会把「源返回了一段怪字节」变成「整个检索失败」。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def parse_yaz_output(text: str) -> dict:
    """解析 yaz-client 会话输出（纯函数，便于测试）。"""
    m = _HITS_RE.search(text)
    return {
        "hits": int(m.group(1)) if m else None,
        "auth_failed": bool(_AUTH_FAIL_RE.search(text)),
        "closed": "Target closed connection" in text,
    }


# pre_commands 黑名单：pre_commands 由「全局设置」写权限的
# 管理员配置，**原样进 yaz-client 的 stdin**。yaz-client 支持 `!` shell 逃逸；
# `open` / `auth` 会覆盖本次会话已配置的目标与凭据。黑名单只拦这三类，
# set_charset / syntax / elements 等正常调优命令不受影响。
_PRE_CMD_FORBIDDEN = re.compile(r"^\s*[!]|^\s*(sh|open|auth)(\s|$)", re.IGNORECASE)


def validate_pre_commands(extra: dict | None) -> list[str]:
    """取出并校验 `extra.pre_commands`；含黑名单命令时抛 `ValueError`。"""
    cmds = [str(c) for c in ((extra or {}).get("pre_commands") or []) if str(c).strip()]
    if any("\r" in c or "\n" in c for c in cmds):
        raise ValueError(
            "pre_commands 不允许包含换行符：yaz-client 会把换行后的内容当成新命令执行")
    bad = [c for c in cmds if _PRE_CMD_FORBIDDEN.match(c)]
    if bad:
        raise ValueError(
            f"pre_commands 含不允许的命令（{bad[0][:60]}…）："
            "不允许 shell 逃逸（!）与 open/auth（会覆盖本会话的目标与凭据）")
    return cmds


class Z3950Source(CatalogSource):
    """Z39.50 源。配置示例见 SourceConfig docstring（历史技术验证记录实测账号可用）。"""

    def build_pqn(self, isbn: str | None = None, title: str | None = None,
                  author: str | None = None) -> str:
        """构造 PQF/PQN 检索式：ISBN 精确优先，否则题名+责任者 AND。

        词元先经 `_clean_term` 清洗（换行/`@` 等会注入会话命令或改写查询树）。
        """
        if isbn:
            return f'@attr 1={USE_ATTRS["isbn"]} {_clean_term(isbn)}'
        parts = []
        if title:
            parts.append(f'@attr 1={USE_ATTRS["title"]} {_clean_term(title)}')
        if author:
            parts.append(f'@attr 1={USE_ATTRS["author"]} {_clean_term(author)}')
        if not parts:
            raise ValueError("至少提供 isbn/title/author 之一")
        return parts[0] if len(parts) == 1 else f"@and {parts[0]} {parts[1]}"

    def _run_session(self, pqn: str, max_records: int, timeout: float,
                     offset: int = 0) -> tuple[str, bytes]:
        """执行一次 yaz-client 会话。返回 (输出文本, 落盘记录字节)。

        认证与目标地址经 **stdin 会话命令**（`auth` / `open`）下发——旧写法把
        `-u user/password` 放在 argv，口令对本机进程列表可见。
        """
        exe = _yaz_client_path()
        cfg = self.config
        fd, dump_path = tempfile.mkstemp(suffix=".mrc", prefix="z3950_")
        os.close(fd)                              # 先关句柄再删（Windows 文件锁）
        Path(dump_path).unlink(missing_ok=True)   # -m 由 yaz-client 重新创建
        args = [exe, "-m", dump_path]
        commands = "\n".join([
            *( [f"auth {_clean_term(cfg.user)}/{_clean_term(cfg.password)}"]
               if cfg.user else [] ),
            f"open tcp:{_clean_term(cfg.host)}:{cfg.port}/{_clean_term(cfg.database)}",
            *validate_pre_commands(cfg.extra),
            f"find {pqn}",
            # 逐条请求：v2 目标对“count 超过结果集”的 Present 会整体拒绝
            # （诊断 [13] out of range），逐条 show 的越界诊断无害。
            # 翻页靠 `offset` 前移起始位置：`show {offset+i}+1`。
            *[f"show {offset + i}+1" for i in range(1, max_records + 1)],
            "quit",
        ])
        try:
            # ⚠️ **检索词必须按目标字符集发送**。yaz-client 把 stdin 的字节原样塞进
            # 检索请求，不做编码转换；而我们的字符串是 Unicode。实测同一台 GBK 目标：
            # 题名「三体」按 UTF-8 发出去 **0 命中**，按 GBK 发出去 **123 命中**。
            # ISBN 全是数字，这个坑只在中文检索时才暴露——所以之前"按 ISBN 能查、
            # 按书名查不到"看起来像两个问题，其实是同一个。
            payload = commands.encode(_query_encoding(cfg.charset), errors="replace")
            proc = subprocess.run(args, input=payload, capture_output=True,
                                  timeout=timeout)
            out = _decode(proc.stdout) + _decode(proc.stderr)
        except subprocess.TimeoutExpired as e:
            out = _decode(e.stdout) + "\n[yaz-client 超时]"
        dump = Path(dump_path)
        data = dump.read_bytes() if dump.exists() else b""
        dump.unlink(missing_ok=True)
        return out, data

    def search(self, isbn=None, title=None, author=None, limit=10,
               offset=0) -> list[Candidate]:
        pqn = self.build_pqn(isbn=isbn, title=title, author=author)
        timeout = float(self.config.extra.get("timeout", 30))
        out, data = self._run_session(pqn, limit, timeout, offset=max(0, int(offset or 0)))
        parsed = parse_yaz_output(out)
        if parsed["auth_failed"]:
            raise ConnectionError(f"[{self.name}] 认证失败/被拒（账号或口令错误）")
        if parsed["hits"] is None:
            raise ConnectionError(f"[{self.name}] 未获得检索结果（源不可达或协议异常）")
        if parsed["hits"] == 0 or not data:
            return []
        records, _errors = read_iso(data)
        out_cands = []
        for i, rec in enumerate(records[:limit]):
            out_cands.append(Candidate(
                source_name=self.name, source_key=f"{parsed['hits']}#{i}",
                title=rec.title(),
                author=rec.first_value("200", "f"),
                publisher=rec.first_value("210", "c"),
                year=rec.first_value("210", "d"),
                class_no=rec.first_value("690", "a"),
                isbn=rec.first_value("010", "a"),
                record=rec,
            ))
        return out_cands

    def fetch(self, candidate: Candidate) -> Record:
        assert candidate.record is not None
        return candidate.record

    def health(self) -> dict:
        """探活：发起一次无害检索（全 0 ISBN），认证通过即视为健康。"""
        try:
            timeout = float(self.config.extra.get("timeout", 30))
            out, _ = self._run_session(f'@attr 1={USE_ATTRS["isbn"]} 0000000000000', 1, timeout)
            parsed = parse_yaz_output(out)
            status = "ok" if not parsed["auth_failed"] and parsed["hits"] is not None else "fail"
            return {"name": self.name, "type": "z3950", "enabled": self.config.enabled,
                    "status": status, "detail": parsed}
        except Exception as e:
            return {"name": self.name, "type": "z3950", "enabled": self.config.enabled,
                    "status": "fail", "detail": str(e)}
