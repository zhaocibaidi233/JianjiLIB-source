# -*- coding: utf-8 -*-
"""套录记录的本地化改写管线：

来源资源库/外部源的记录不能直接进编目库：
  1. 剥离来源馆 9xx 馆藏/厂商私有字段；
  2. 001 控制号按本馆规则重编，原号存入 035 溯源；
  3. 801 记录来源改写为本馆；
  4. 分类号截取（按需，见下）；
  5. 清洗管线（core.clean.clean_record）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from ..core import clean as cl
from ..core.clc import ClassNoRule, shorten_record_class_nos
from ..core.marc import Field, Record, Subfield


def next_control_no() -> str:
    """本馆 001 控制号：前缀 012 + 日期 + 随机段（MVP 规则，可配置化留待 V1.0）。"""
    return f"012{datetime.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}"


def localize_record(rec: Record, agency: str = "LOCAL",
                    control_no: str | None = None,
                    class_rule: ClassNoRule | None = None) -> tuple[Record, dict]:
    """就地改写。返回 (记录, 统计)。

    `class_rule` 非 None 时，对**所有** `690` 字段的 `$a` 做分类号截取
    （规则见 `core.clc.shorten_class_no` 与）；None = 不截取。
    """
    stats = {"stripped_9xx": 0, "control_no_migrated": False, "cleaned": {}}

    # 1. 剥离 9xx（来源馆藏 905/909 与厂商私有 98x）
    kept, stripped = [], 0
    for f in rec.fields:
        if f.tag.startswith("9"):
            stripped += 1
        else:
            kept.append(f)
    rec.fields = kept
    stats["stripped_9xx"] = stripped

    # 2. 001 -> 035 溯源 + 新 001
    old_no = rec.control_value("001")
    rec.fields = [f for f in rec.fields if f.tag != "001"]
    if old_no:
        rec.fields.append(Field("035", " ", " ", subfields=[Subfield("a", old_no)]))
        stats["control_no_migrated"] = True
    rec.fields.insert(0, Field("001", data=control_no or next_control_no()))

    # 3. 801 记录来源改写为本馆
    f801 = rec.get_first("801")
    if f801 is None:
        f801 = Field("801", "0", " ")
        rec.fields.append(f801)
    if not (f801.get("a") or "").strip():
        f801.add("a", "CN")
    f801.set("b", agency)
    f801.set("c", datetime.now().strftime("%Y%m%d"))

    # 4. 分类号截取（套录专属）。
    #
    # ⚠️ 这一步**刻意**放在 localize_record —— 它**只在套录**（prepare / quick-entry）
    # 时跑；**绝不能**挪进 core.clean.clean_record()：那个函数在**每次保存/导入**时
    # 都会跑，一旦放进去，用户"改了设置再保存一次"就会被再削一次，且不可逆——
    # 库里既有记录也会被改写。用户裁定：**库里已有的记录一律不动**，只有套录进来的
    # 新数据才截取。请勿因"两处都会跑"而图省事把它挪过去。
    #
    # 遍历逻辑在 `core.clc.shorten_record_class_nos()`（唯一定义），与"外部文件
    # 直落图书库"的 `services.batch.import_records_to_db()` 共用，别在这里另写一遍。
    if class_rule is not None:
        # 统计里如实记下"当时用的是哪条规则"——事后想复核"这条号为什么被削成这样"，
        # 只记 `digits` 已经不够了（`dash_digits` / `marks` / `base_digits` 也参与结果）。
        stats["class_no_rule"] = class_rule.as_dict()
        stats["class_shortened"] = shorten_record_class_nos(rec, class_rule)

    # 5. 清洗管线
    stats["cleaned"] = cl.clean_record(rec, migrate_pinyin=True)
    return rec, stats
