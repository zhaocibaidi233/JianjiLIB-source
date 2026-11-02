# -*- coding: utf-8 -*-
"""CNMARC 字段字典（`app/data/cn_marc_fields.json`）的读取层。

**这份字典是什么**：字段号 → 中文名 / 子字段清单 / 是否必备 / 是否控制字段。
**全项目共用一份** —— 编辑器、导入映射、演示文案都不再各写一份字段中文名。

**它不是什么**：它**不是映射依据**。字段字典的声明与实际数据可能有出入
（例如 `905` 的声明与实际子字段布局不同），本系统的字段命名以代码中的显式定义为准。因此：
  - 我方**自有**的字段命名（如 `905$b` 条码号、`995` 系书商字段）走 `io/iso.py` 里
    的人工覆盖层，**不从这里取**；
  - 这里只回答"CNMARC 里 `011$b` 叫什么、`200` 有哪些子字段"这类**规范知识**。

数据是**只读**的：所有读取函数返回内部对象，调用方**不得原地修改**（会污染缓存）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# 本文件在 backend/app/services/ 下，parents[1] 即 backend/app/
FIELDS_FILE = Path(__file__).resolve().parents[1] / "data/cn_marc_fields.json"


@lru_cache(maxsize=1)
def load() -> dict:
    """读取字典（进程内缓存一次）。文件缺失直接报错 —— 它是随包发布的数据文件，
    缺了说明打包漏了，宁可启动即坏也不要静默返回空字典让界面显示一堆无名字段。"""
    if not FIELDS_FILE.exists():
        raise FileNotFoundError(
            f"CNMARC 字段字典缺失：{FIELDS_FILE}（用 scripts/import_marc_fields.py 生成）")
    return json.loads(FIELDS_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _by_tag() -> dict[str, dict]:
    return {f["tag"]: f for f in load()["fields"]}


def all_fields() -> list[dict]:
    """全部字段（按字段号升序）。**只读**，调用方不得修改返回的对象。"""
    return load()["fields"]


def count() -> int:
    return load()["count"]


def source_note() -> str:
    """字典的来源与免责说明（界面上做脚注用）。"""
    return load().get("note", "")


def get_field(tag: str) -> dict | None:
    """按字段号取字段定义；未收录返回 None。

    ⚠️ 未收录 ≠ 不存在：字段字典只声明了它用到的字段，CNMARC 还有未列出的字段。
    """
    return _by_tag().get((tag or "").strip())


def field_name(tag: str) -> str:
    """字段中文名；未收录或无中文名时返回空串（调用方自行降级为只显字段号）。"""
    f = get_field(tag)
    return (f or {}).get("name", "")


def subfields_of(tag: str) -> list[dict]:
    """字段的子字段清单（`[{code, name}]`）；未收录返回空列表。"""
    f = get_field(tag)
    return (f or {}).get("subfields", [])


def subfield_name(tag: str, code: str) -> str:
    """子字段中文名；查不到返回空串。"""
    for sf in subfields_of(tag):
        if sf["code"] == code:
            return sf.get("name", "")
    return ""


def required(tag: str) -> bool:
    """该字段在字段字典声明里是否**必备**（比 CNMARC 规范更宽松，仅 101/200/215/690 四条）。"""
    return bool((get_field(tag) or {}).get("required"))


def is_control(tag: str) -> bool:
    """是否控制字段（`001`–`009`：无指示符、无子字段）。"""
    return bool((get_field(tag) or {}).get("control"))


def all_subfields(named_only: bool = True) -> list[tuple[str, str, str]]:
    """拍平成 `(字段号, 子字段码, 中文名)` 列表，按字段号升序。

    `named_only=True`（默认）只返回**有中文名**的子字段 —— 少数字段（如 `605`）的
    detail 是空的，只能从 `Name` 拿到子字段码而拿不到名，这种"光秃秃的码"进不了
    下拉选项（显示成一个没有说明的 `605$a` 反而误导），故默认滤掉。
    """
    out: list[tuple[str, str, str]] = []
    for f in all_fields():
        if f.get("control"):
            continue
        for sf in f.get("subfields", []):
            name = sf.get("name", "")
            if named_only and not name:
                continue
            out.append((f["tag"], sf["code"], name))
    return out
