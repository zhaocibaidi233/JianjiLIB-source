# -*- coding: utf-8 -*-
"""从外部清单**反向提取「最大书次号」**，用来抬高本馆的书次号计数器。

**为什么需要它**：书次号计数器（`callno_counters`）只在**本系统内**编目时抬号 ——
`create_holdings()` 自动取号、`set_book_no()` 手工指定时抬、图书转库按搬进来的最大号抬
（`api/transfer.py::_raise_to_max_book_no`）。从**别的系统**接手一批数据时，
库里已经用到 `I247.5/300`，而我们的计数器还是 0，下一本自动取号就从 `1` 开始，
**撞上历史号**（索书号没有唯一约束兜底，撞了也无从发现）。

对标参考系统编目系统的「提取清单最大号」。它那边的做法是：清单只保留索书号一列、
把列名改成「索书号」、中间不能有空行 —— 我们**不学这套脆弱约定**：
读 Excel 走既有的**列名识别 + 手动列映射**（列名随便叫什么都行），
解析靠 `core.callno.parse_call_no()`（与写回 `set_book_no()` 互为逆运算的同一份口径）。
"""
from __future__ import annotations

import re

from ..core.callno import parse_call_no

# 单次最多处理的行数（防误传一个几十万行的文件把请求拖死）。
MAX_LINES = 200_000
# 最多返回 / 抬高的分类号个数（超出的只计数，不逐项返回）。
MAX_ITEMS = 5000
# 返回给界面的"跳过明细"条数上限（其余只计数）。
MAX_SKIPPED_SHOWN = 50

_CLASS_RE = re.compile(r"^[A-Za-z]")


def extract_max_book_no(lines) -> dict:
    """按分类号取最大书次号。**纯函数、不碰数据库**，便于单测。

    ⚠️ **只有纯数字的书次号参与抬号**：计数器就是数字，`L824`（著者号形态）
    这类没有对应的"最大号"，报了也抬不了 —— 它们进 `skipped` 让用户看得见，
    而不是静默丢掉（"我明明上传了 500 行，怎么说只处理了 300 行"）。

    返回：
      - `items`：`[{class_no, max_no, count, sample}]`，按分类号升序；
        `max_no` 是抬号目标，`sample` 是取到该最大号的那一行（便于人工核对）；
      - `skipped` / `skipped_count`：没参与的行与原因；
      - `line_total` / `usable` / `truncated`。
    """
    per: dict[str, dict] = {}
    skipped: list[dict] = []
    line_total = 0
    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        if line_total >= MAX_LINES:
            break
        line_total += 1
        class_no, book_no, _volume = parse_call_no(line)
        if not class_no or not book_no:
            skipped.append({"line": line, "reason": "不是「分类号/书次号」形态"})
            continue
        if not _CLASS_RE.match(class_no):
            skipped.append({"line": line,
                            "reason": f"分类号「{class_no}」不以字母开头"})
            continue
        if not book_no.isdigit():
            skipped.append({"line": line,
                            "reason": f"书次号「{book_no}」不是数字，无法比较大小"})
            continue
        n = int(book_no)
        cur = per.get(class_no)
        if cur is None:
            per[class_no] = {"class_no": class_no, "max_no": n, "count": 1, "sample": line}
        else:
            cur["count"] += 1
            if n > cur["max_no"]:
                cur["max_no"] = n
                cur["sample"] = line
    items = sorted(per.values(), key=lambda d: d["class_no"])
    return {
        "items": items[:MAX_ITEMS],
        "skipped": skipped[:MAX_SKIPPED_SHOWN],
        "skipped_count": len(skipped),
        "line_total": line_total,
        "usable": sum(v["count"] for v in per.values()),
        "truncated": len(items) > MAX_ITEMS,
    }


def lines_from_text(text: str | None) -> list[str]:
    """粘贴文本 → 行列表（按换行切、去掉空行；逗号/分号分隔也认）。

    用户从 Excel 复制一列时，剪贴板里常常是「一行一个」；但也有整段用逗号拼的，
    所以两种分隔符都切一刀 —— 索书号里不会出现逗号或分号。
    """
    out: list[str] = []
    for chunk in re.split(r"[\r\n]+", text or ""):
        for part in re.split(r"[;,，；]", chunk):
            s = part.strip()
            if s:
                out.append(s)
    return out
