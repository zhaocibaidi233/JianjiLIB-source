# -*- coding: utf-8 -*-
"""801 记录来源：本馆机构代码的自动补写。

## 依据

GB/T 33286—2016 `7.10 国际使用块`：

- 801 在**交换书目数据时**必备（不是所有记录无条件必备）；
- 指示符 2 是**功能指示符**：`0` 原始编目机构 / `1` 转录机构 / `2` 修改机构 /
  `3` 发行机构；同一记录可按机构功能重复本字段；
- 子字段：`$a` 国家（GB/T 2659 两位大写，如 `CN`）、`$b` 机构（英文缩写或中文
  全称都行）、`$c` 处理日期（`YYYYMMDD`）、`$g` 编目规则、`$h` 原控制号。

## 其他机构的做法（示例）

- 公共图书馆：`801 #0$aCN$bNLC$c19880220`
- 高校图书馆：`801 #0$aCN$b<机构代码>$d处理日期`
- 职业院校图书馆：套录时保留原编目机构的 801，自己大改后再加一条 `801 #2`
- 部分馆使用 `801#2$acn$b<机构代码>$c编目日期`，由系统自动生成

共同点是日期机器生成、机构代码各馆自定。

## 这里做到哪一步

只做**最小可用**的一步：记录里没有 801、且设置里填了机构代码时，补一条
`801 #0 $aCN $b<代码> $c<今天>`。

**不做 `#2`（修改机构）**：判断"改了多少才算大改"没有客观标准，加错了反而把
别人的记录标成本馆编的。留给人自己决定。
"""
from __future__ import annotations

from datetime import date

from ..core.marc import Field, Subfield

#: 801$a 的国家代码（GB/T 2659）。本系统只处理中文文献，写死 CN。
COUNTRY_CODE = "CN"


def has_source_field(rec) -> bool:
    return rec.get_first("801") is not None


def ensure_source_801(rec, code: str) -> bool:
    """没有 801 就补一条本馆的 `#0`。返回是否补了。

    `code` 为空表示未配置本馆机构代码 —— 此时**什么都不做**（不是补一个空的
    `$b`，那比没有更糟）。
    """
    code = (code or "").strip()
    if not code or has_source_field(rec):
        return False
    from ..io.iso import insert_field_ordered
    insert_field_ordered(rec, Field(
        "801", " ", "0", "",
        [Subfield("a", COUNTRY_CODE), Subfield("b", code),
         Subfield("c", date.today().strftime("%Y%m%d"))],
    ))
    return True
