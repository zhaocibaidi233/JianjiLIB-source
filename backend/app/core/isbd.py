# -*- coding: utf-8 -*-
"""ISBD 标点规范（200 字段）。

CNMARC 要求标点归属**前一个子字段的末尾**：
    200 $a正题名 /$f责任者
    200 $a正题名 :$e副题名 /$f责任者
即 $a 在接 $e 时以 " : " 收尾，接责任者（$f/$g）时以 " / " 收尾。

注意：这里的标点属于**著录格式**，不是题名的一部分。简编表单这类
"题名/责任者分栏录入"的界面应当在展示时剥掉尾部标点，保存时由本模块补回，
否则用户会误以为「/」是题名内容。
"""
from __future__ import annotations

from .marc import Record

AUTHOR_CODES = ("f", "g")


def strip_isbd_tail(value: str) -> str:
    """剥掉子字段末尾的 ISBD 标点与空白（供展示层使用）。"""
    v = (value or "").rstrip()
    while v and v[-1] in ":/":
        v = v[:-1].rstrip()
    return v


def unify_isbd(rec: Record) -> list[str]:
    """按 ISBD 重算 200 字段尾标点（确定性修复质检 R2001）。

    规则：$a 以 " : " 收尾接 $e；$e 以 " / " 收尾接 $f/$g；
    无后继时尾标点清除。已有正确标点不动。
    """
    applied: list[str] = []
    for f200 in rec.get_fields("200"):
        sf_a = next((s for s in f200.subfields if s.code == "a"), None)
        sf_e = next((s for s in f200.subfields if s.code == "e"), None)
        has_author = any(
            (s.code in AUTHOR_CODES and s.value.strip()) for s in f200.subfields)
        changed = False
        if sf_a is not None and sf_a.value.strip():
            cleaned = strip_isbd_tail(sf_a.value)
            tail = " : " if (sf_e is not None and sf_e.value.strip()) else (
                " / " if has_author else "")
            new_val = cleaned + tail if tail else cleaned
            if new_val != sf_a.value:
                sf_a.value = new_val
                changed = True
        if sf_e is not None and sf_e.value.strip():
            cleaned = strip_isbd_tail(sf_e.value)
            new_val = cleaned + (" / " if has_author else "")
            if new_val != sf_e.value:
                sf_e.value = new_val
                changed = True
        if changed:
            applied.append("200 字段 ISBD 标点已按规范补齐/清理")
    return applied
