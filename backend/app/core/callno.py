# -*- coding: utf-8 -*-
"""索书号生成：分类号 + 书次号（+ 卷册区分号）。

计数器以协议注入（MVP 用内存/数据库表 callno_counters 实现），规则各馆可配置。
"""
from __future__ import annotations

from .volume import VOLUME_SEP


class CallNoCounter:
    """书次号计数器协议。"""

    def current(self, class_no: str) -> int:
        raise NotImplementedError

    def bump(self, class_no: str) -> int:
        """自增并返回新号。"""
        raise NotImplementedError


class MemoryCounter(CallNoCounter):
    """内存计数器（测试/单机用）。"""

    def __init__(self, initial: dict[str, int] | None = None):
        self._d = dict(initial or {})

    def current(self, class_no: str) -> int:
        return self._d.get(class_no, 0)

    def bump(self, class_no: str) -> int:
        self._d[class_no] = self._d.get(class_no, 0) + 1
        return self._d[class_no]


def make_call_no(class_no: str, counter: CallNoCounter, volume: str | None = None) -> str:
    """生成索书号：分类号/书次号[:卷册号]。

    卷册号分隔符是 `:`（即 `core.volume.VOLUME_SEP`）而不是 `/` —— `/` 固定用于
    分隔分类号与书次号，且已被「附加区分号」占用（同责任者同分类号的不同著作，
    如 `I210.4/L824/2`）。详见 `core/volume.py` 顶部注释与 。

    例：make_call_no("I247.5", counter)      -> "I247.5/35"
        make_call_no("I247.5", counter, "2") -> "I247.5/35:2"（多卷书辅助区分）
    """
    seq = counter.bump(class_no.strip())
    no = f"{class_no.strip()}/{seq}"
    if volume:
        no += f"{VOLUME_SEP}{volume}"
    return no


def parse_call_no(call_no: str) -> tuple[str, str, str | None]:
    """把索书号 `分类号/书次号[:卷册号]` 拆成三段 `(分类号, 书次号, 卷册号)`。

    - 分类号 = **第一个 `/` 之前**；
    - 书次号 = 第一个 `/` 之后、`:` 之前（**可自带 `/`**，即「附加区分号」，
      如 `I210.4/L824/2` → 分类号 `I210.4`、书次号 `L824/2`）；
    - 卷册号 = `:` 之后（可空）。

    与 `services.batch.set_book_no()`（写回时按 `:` 截断）口径一致，两边互为逆运算。
    用于「导入时整串给索书号」——不必再让用户把分类号/书次号拆成两列。
    """
    s = (call_no or "").strip()
    if not s:
        return "", "", None
    base, sep, vol = s.partition(VOLUME_SEP)
    class_no, _slash, book_no = base.partition("/")
    volume = (vol.strip() or None) if sep else None
    return class_no.strip(), book_no.strip(), volume
