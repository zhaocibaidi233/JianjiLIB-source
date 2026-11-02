# -*- coding: utf-8 -*-
"""010$d 定价解析 —— **唯一真源**。

为什么单独成模块：定价有两条消费路径，两边必须算得一模一样。
  1. **写入时**：算进 `records.price` / `records.price_currency` 两个冗余列
     （由 `models.RecordORM` 的 before_insert / before_update 事件调用，见下）；
  2. **读取时**：统计（`api/stats.py`）与财产登记表（`api/property.py`）直接聚合冗余列。
若写入侧与读取侧各留一套解析，迟早分叉 —— 而分叉的表现是「统计页的码洋」与
「记录详情里的定价」对不上，属于账目数字出错，且是静默的。

金额与**币种一起**存（不是只存一列金额）：本馆默认币种是可以改的（字典中心），
只存金额的话，改一次币种，库里所有缓存值就全按旧币种算了，而报表数字会看起来照旧。
币种一起存，改币种只是换个过滤条件（见 `api/stats.py` 的口径说明）。
"""
from __future__ import annotations

import re
from decimal import Decimal

from .clean import normalize_price

# 归一化后的值形如 `CNY29.80`（`normalize_price` 负责补码、去重前缀、补两位小数；
# 认不出的复杂值它原样放过）。这里只做「前导币种码 + 紧邻金额」的宽松匹配 ——
# 与改造前 `stats._price_of` 的 `rx.match` 同为**左对齐匹配**，故
# `CNY29.80(全2册)` 这类值旧实现取 29.80，本函数同样取 29.80（不收紧，不改口径）。
#
# 币种码写成 2~6 个字母而**不是**照抄 `clean.CURRENCY_CODES`：字典中心允许用户
# 自加币种，而清洗阶段拿不到 db（`_CUR_ALT` 只覆盖内置币种）。照抄的话，
# 用户自加币种（如 `XYZ100`）的定价会被判成"没有定价"——旧实现因为按
# "默认币种的正则"匹配，反而是能算出来的。
_PRICE_RE = re.compile(r"([A-Za-z]{2,6})\s*(\d+(?:\.\d+)?)")


def first_price(fields: list | None) -> tuple[Decimal, str] | None:
    """取记录里**文档顺序第一条可解析的** `010$d`，返回 `(金额, 币种码)`；一条都没有返回 `None`。

    与改造前 `stats._price_of(fields, cur)` 的关系：只差「同一条记录里出现多种币种」这一种：

    ============================================  ==============================
    010$d 依次为 `USD10` / `CNY30`，默认币种 CNY  改造前       本函数 + 入库后 SQL 过滤
    ============================================  ==============================
    结果                                          30（跳过 USD 那条）   0（只留下 USD 那条）
    ============================================  ==============================

    真实数据里不存在这种记录（资源库 1000 条实测：每条记录恰好 1 个 `$d`，且全是 CNY），
    而"按币种各缓存一份"就要上 JSONB 映射，代价大于收益 —— 故取"第一条可解析"，
    并在 `tests/test_pricing.py` 里把这个规则**钉住**（而不是留着不定义）。
    真遇到这种记录，报表少算一笔，人看得见；比两套实现悄悄对不上要好。
    """
    for item in fields or []:
        if item.get("tag") != "010":
            continue
        for code, value in (item.get("sf") or []):
            if code != "d" or not value:
                continue
            m = _PRICE_RE.match(normalize_price(str(value)))
            if m:
                return Decimal(m.group(2)), m.group(1).upper()
    return None


def price_columns(fields: list | None) -> dict:
    """`fields` -> 冗余列取值。写路径（ORM 事件）用这个，别自己拼。"""
    pair = first_price(fields)
    if pair is None:
        return {"price": None, "price_currency": None}
    amount, currency = pair
    return {"price": amount, "price_currency": currency}


def record_price(orm, currency: str = "CNY") -> float:
    """从**冗余列**取该记录的定价：币种不符（或无定价）返回 `0.0`。

    口径与改造前逐字一致：只认本馆默认币种，非默认币种按 0 计
    （不同币种不能相加，1 美元 + 1 元人民币 ≠ 2 元）。
    内建"绝不抛"：`price_currency` 为空说明还没回填（老库刚升级），此时按 0 算，
    不会让统计页整页 500。
    """
    if (getattr(orm, "price_currency", None) or "").upper() != (currency or "").upper():
        return 0.0
    value = getattr(orm, "price", None)
    return float(value) if value is not None else 0.0
