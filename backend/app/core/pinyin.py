# -*- coding: utf-8 -*-
"""汉语拼音列生成（检索冗余列，见 数据模型）。

pypinyin 延迟导入：拼音功能不可用时其余引擎不受影响。
"""
from __future__ import annotations

from pypinyin import Style, pinyin


def to_pinyin(text: str) -> str:
    """全拼（小写、无分隔），非汉字原样保留：通讯与写作 -> tongxunyuxiezuo"""
    return "".join(p[0] for p in pinyin(text, style=Style.NORMAL, errors=lambda c: [c or ""])).lower()


def to_initials(text: str) -> str:
    """拼音首字母（小写、无分隔）：通讯与写作 -> txyxz"""
    return "".join(p[0] for p in pinyin(text, style=Style.FIRST_LETTER, errors=lambda c: [c or ""])).lower()
