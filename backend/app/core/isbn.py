# -*- coding: utf-8 -*-
"""ISBN 工具：归一化、校验位、10/13 位互转。"""
from __future__ import annotations

import re

_CLEAN_RE = re.compile(r"[^0-9Xx]")


def normalize(raw: str) -> str | None:
    """去连字符/空格后归一化；返回 10 或 13 位字符串，非法长度返回 None。"""
    d = _CLEAN_RE.sub("", str(raw).upper())
    if len(d) in (10, 13):
        return d
    return None


def is_valid_13(d: str) -> bool:
    if len(d) != 13 or not d.isdigit():
        return False
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d[:12]))
    return (10 - s % 10) % 10 == int(d[12])


def is_valid_10(d: str) -> bool:
    if len(d) != 10:
        return False
    try:
        s = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d))
    except ValueError:
        return False
    return s % 11 == 0


def is_valid(d: str) -> bool:
    return is_valid_13(d) if len(d) == 13 else is_valid_10(d) if len(d) == 10 else False


def to_10(d13: str) -> str | None:
    """978 开头的 ISBN-13 转 ISBN-10。"""
    if not (is_valid_13(d13) and d13.startswith("978")):
        return None
    core = d13[3:12]
    s = sum(int(c) * (10 - i) for i, c in enumerate(core))
    r = (11 - s % 11) % 11
    return core + ("X" if r == 10 else str(r))


def to_13(d10: str) -> str | None:
    """ISBN-10 转 978 前缀的 ISBN-13。"""
    if not is_valid_10(d10):
        return None
    core = "978" + d10[:9]
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core[:12]))
    return core + str((10 - s % 10) % 10)


def canonical(raw: str) -> str | None:
    """规范存储形态：可转换时统一为 ISBN-13，否则保留原形态。

    检索冗余列（isbn_norm）统一用它——否则同一本书会因 ISBN-10/13 形态
    不同而被当作两条记录（验收发现资源库 693 组跨形态重复）。
    """
    d = normalize(raw)
    if d is None:
        return None
    if len(d) == 13:
        return d
    alt = to_13(d)
    return alt if alt else d


def all_forms(raw: str) -> set[str]:
    """一个 ISBN 的所有等价形态（归一 + 互转），用于查重/套录对撞。"""
    d = normalize(raw)
    if d is None:
        return set()
    out = {d}
    if len(d) == 13:
        alt = to_10(d)
    else:
        alt = to_13(d)
    if alt and is_valid(alt):
        out.add(alt)
    return out
