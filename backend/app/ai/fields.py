# -*- coding: utf-8 -*-
"""字段自动补全（规则层，毫秒级，ISBN 快速录入的"自动填"引擎）。

分组策略（用户工作流约定）：
  自动填（无需人工管）  正/副题名拆分、语种、出版地（出版者对照）、
                        出版者规范名、出版年规范化、尺寸清理
  人工校对（高亮提示）  分类号（给候选）、页数、价格
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache


from ..core import clean as cl
from ..core import publisher as pub_table
from ..core.isbd import unify_isbd          # ISBD 标点规范已下沉到 core，供清洗层共用
from ..core.marc import Field, Record, Subfield
from ..models import RecordORM, ResourceRecordORM

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
ASCII_TITLE_RE = re.compile(r"^[A-Za-z0-9\s:',.\-]+$")


# ---------- 出版社 → 出版地 对照表（data-driven，从资源库挖掘） ----------

@lru_cache(maxsize=1)
def _publisher_geo_json() -> str:
    """经 JSON 缓存的对照表（lru_cache 需不可变返回值）。"""
    return json.dumps(build_publisher_geo(), ensure_ascii=False)


def build_publisher_geo(min_freq: int = 3) -> dict[str, str]:
    """扫描资源库+编目库 210 字段：出版者($c) -> 最高频出版地($a)。

    简表数据源 1.3 万条记录可覆盖大部分常配出版者。库为空时返回空表（自动填
    跳过出版地，交人工）。
    """
    from ..db import SessionLocal
    counter: dict[str, dict[str, int]] = {}
    db = SessionLocal()
    try:
        for (fields,) in db.query(ResourceRecordORM.fields).yield_per(500):
            _count_geo(fields, counter)
        for (fields,) in db.query(RecordORM.fields).yield_per(500):
            _count_geo(fields, counter)
    finally:
        db.close()
    geo = {}
    for pub, places in counter.items():
        top_place, freq = max(places.items(), key=lambda kv: kv[1])
        if freq >= min_freq:
            geo[pub] = top_place
    return geo


def _count_geo(fields: list, counter: dict) -> None:
    for item in fields or []:
        if item.get("tag") != "210":
            continue
        place = pub = None
        for code, value in item.get("sf", []):
            v = str(value).strip().rstrip(":")
            if code == "a" and v:
                place = v
            elif code == "c" and v:
                pub = v
        if pub and place:
            counter.setdefault(pub, {})
            counter[pub][place] = counter[pub].get(place, 0) + 1


def publisher_geo() -> dict[str, str]:
    """出版者 -> 出版地 对照表。

    **外部数据包优先**：如部署方提供已授权的 `publisher_prefixes.json`
    （`core/publisher.py`），它比从库里挖出来的准。挖掘结果只用来补数据包没有的
    出版者 —— 库里的脏数据不该盖掉权威值。
    """
    geo = dict(pub_table.geo_map())
    for name, place in json.loads(_publisher_geo_json()).items():
        geo.setdefault(name, place)
    return geo


# ---------- 规则层自动补全 ----------
#
# 每一步都抽成**独立可调用的函数**：`autofill_fields` 按既定顺序组合它们（保持老行为），
# `services/optimize.py`（规范中心）也复用同一批函数 —— 规则逻辑**只此一份**，
# 不在两处各写一遍（那样迟早分叉）。

def fill_split_title(rec: Record) -> list[str]:
    """正/副题名拆分：无 200$e 且 $a 含全角冒号 → 拆出 $e。"""
    f200 = rec.get_first("200")
    if f200 is None:
        return []
    a = (f200.get("a") or "").strip()
    if a and not f200.get("e") and "：" in a:
        main, sub, _series = cl.split_title(a)
        if sub and main:
            f200.set("a", main)
            # 保持 ISBD 顺序：$a 后插入 $e
            idx = next(i for i, sf in enumerate(f200.subfields) if sf.code == "a")
            f200.subfields.insert(idx + 1, type(f200.subfields[0])("e", sub))
            return [f"正副题名拆分：{a[:20]} → {main[:14]} / {sub[:12]}"]
    return []


def fill_language(rec: Record, default_lang: str = "chi") -> list[str]:
    """语种缺省：缺 101$a 时补 `default_lang`（题名纯 ASCII 一律按 eng）。"""
    if rec.first_value("101", "a") is not None or rec.get_first("200") is None:
        return []
    title = rec.title() or ""
    lang = "eng" if (title and ASCII_TITLE_RE.match(title)) else (default_lang or "chi")
    f101 = rec.get_first("101") or Field("101", "0", " ")
    if rec.get_first("101") is None:
        rec.fields.append(f101)
    f101.set("a", lang)
    return [f"语种自动填：{lang}"]


def fill_pub_place(rec: Record) -> list[str]:
    """出版地对照补全：缺 210$a 且有 $c → 查出版者→出版地对照表。"""
    f210 = rec.get_first("210")
    if f210 is None or (f210.get("a") or "").strip():
        return []
    pub = (f210.get("c") or "").strip()
    geo = publisher_geo().get(pub)
    if geo:
        f210.subfields.insert(0, type(f210.subfields[0])("a", geo if geo.endswith("：") else geo))
        return [f"出版地对照补全：{pub[:12]} → {geo}"]
    return []


def normalize_pub_year(rec: Record) -> list[str]:
    """出版年规范化（2007.9 → 2007），作用于**全部** 210$d。"""
    out: list[str] = []
    for f210 in rec.get_fields("210"):
        for sf in f210.subfields:
            if sf.code != "d" or not sf.value:
                continue
            d = sf.value.strip()
            if d and not re.fullmatch(r"(19|20)\d{2}", d):
                y = cl.normalize_year(d)
                if y:
                    sf.value = y
                    out.append(f"出版年规范化：{d} → {y}")
    return out


def clean_size_value(rec: Record) -> list[str]:
    """尺寸位错值清除：215$d 写成页数样式（如"154页"）→ 删掉该子字段。"""
    f215 = rec.get_first("215")
    if f215 is None:
        return []
    d215 = (f215.get("d") or "").strip()
    if d215 and "页" in d215:
        f215.subfields = [sf for sf in f215.subfields if sf.code != "d"]
        return [f"尺寸位错误值已清除（{d215}，按缺省处理）"]
    return []


def fill_default_size(rec: Record, size: str = "20cm") -> list[str]:
    """尺寸缺省：215 存在但 $d 为空 → 填 `size`（可在规范中心配置）。"""
    f215 = rec.get_first("215")
    if f215 is None or (f215.get("d") or "").strip():
        return []
    f215.set("d", size or "20cm")
    return [f"尺寸缺省填 {size or '20cm'}"]


def fill_default_binding(rec: Record, binding: str = "平装") -> list[str]:
    """装订缺省：010 有 $a（ISBN）但无 $b → 补 `binding`（默认平装）。

    按 CNMARC 子字段序插在 $a 之后、$d 之前（010 规范序 a→b→d），不追加到末尾 ——
    子字段顺序是这个项目一以贯之的讲究（见实现说明 存量技术债）。
    """
    out: list[str] = []
    for f010 in rec.get_fields("010"):
        if (f010.get("b") or "").strip():
            continue
        if not (f010.get("a") or "").strip():
            continue
        idx = next((i for i, sf in enumerate(f010.subfields) if sf.code == "a"), None)
        pos = idx + 1 if idx is not None else 0
        f010.subfields.insert(pos, type(f010.subfields[0])("b", binding or "平装"))
        out.append(f"装订缺省填 {binding or '平装'}")
    return out


def normalize_isbn_13(rec: Record) -> list[str]:
    """ISBN 10 位转 13 位：010$a 是合法的 ISBN-10 → 就地换成 978 前缀的 ISBN-13。"""
    from ..core import isbn as isbnlib
    out: list[str] = []
    for f010 in rec.get_fields("010"):
        for sf in f010.subfields:
            if sf.code != "a" or not sf.value:
                continue
            d = isbnlib.normalize(sf.value)
            if d and len(d) == 10:
                d13 = isbnlib.to_13(d)
                if d13:
                    sf.value = d13
                    out.append(f"ISBN 10 位已转 13 位：{d} → {d13}")
    return out


# ---------------------------------------------------------------------------
#  定长编码字段的生成（100 / 105 / 106 / 102）
# ---------------------------------------------------------------------------
#
# 为什么要有这一组：CNMARC 里 **100 是必备字段**（``），105 是专著记录
# 应提供的编码数据，102 出版国别 / 106 载体形态也是常规著录项。但它们全是**定长、
# 按字符位取值**的 —— 让编目员手敲等于逼人背代码表，所以「由系统按规则生成」。
#
# ⚠️ 各位的取值口径 = **与本馆已入库真实数据一致**（书商 / 联合编目中心成品数据），
#    位定义以 GB/T 33286 与本模块常量为准。要改**只改下面这几个常量**，别散进函数体。
#    示例：
#        100$a = "20201104d2007    em y0chiy0110    ea"   （36 位）
#        105$a = "f   z   000zy"                          （13 位）
#        102$a = "CN"     106$a = "r"
_100_AUDIENCE = "em "        # 17-19 位 读者对象代码
_100_GOV_PUB = "y0"          # 20-21 位 政府出版物代码
_100_TRANSLIT = "y"          # 25 位    音译代码
_100_CHARSET = "0110"        # 26-29 位 字符集标识
_100_TITLE_LANG = "ea"       # 34-35 位 题名语系（汉语）
_105_DEFAULT = "f   z   000zy"   # 13 位：图表/内容类型/会议/文集/索引/文学体裁/传记


def fill_100(rec: Record, today: str | None = None) -> list[str]:
    """100 通用处理数据（**必备**，36 位定长）：缺则生成。**已有的一律不动**。

    位分配（``）：0-7 入档日期 / 8 出版日期类型 / 9-12 出版年1 /
    13-16 出版年2 / 17-19 读者对象 / 20-21 政府出版物 / 22-24 编目语种 /
    25 音译 / 26-29 字符集 / 30-33 补充 / 34-35 题名语系。
    """
    if rec.get_first("100") is not None:
        return []
    if rec.get_first("200") is None:        # 连题名都没有 → 不是一条书目，别凭空造
        return []
    d = today or datetime.now().strftime("%Y%m%d")
    year = cl.normalize_year(rec.first_value("210", "d") or "") or ""
    a = (d
         + "d"                                        # 出版日期类型：出版年与入档年不同
         + (year.ljust(4)[:4] if year else "    ")     # 9-12 出版年1（缺则留空）
         + "    "                                     # 13-16 出版年2
         + _100_AUDIENCE + _100_GOV_PUB
         + "chi"                                      # 22-24 编目语种
         + _100_TRANSLIT + _100_CHARSET + "    " + _100_TITLE_LANG)
    if len(a) != 36:                                      # 常量被改错时当场炸，别写进库
        raise ValueError(f"100$a 必须 36 位，实际 {len(a)}：{a!r}")
    rec.fields.append(Field("100", " ", " ", subfields=[Subfield("a", a)]))
    return ["100 通用处理数据已生成（36 位定长）"]


def fill_105(rec: Record) -> list[str]:
    """105 编码数据:图书（**13 位**定长）：缺则生成默认值。已有的一律不动。"""
    if rec.get_first("105") is not None or rec.get_first("200") is None:
        return []
    rec.fields.append(Field("105", " ", " ", subfields=[Subfield("a", _105_DEFAULT)]))
    return ["105 编码数据已生成（13 位定长）"]


def fill_106(rec: Record) -> list[str]:
    """106 载体形态（`$a r` = 印刷型文字资料）：缺则补。已有的一律不动。"""
    if rec.get_first("106") is not None or rec.get_first("200") is None:
        return []
    rec.fields.append(Field("106", " ", " ", subfields=[Subfield("a", "r")]))
    return ["106 载体形态已补 r（印刷型文字资料）"]


def fill_102(rec: Record) -> list[str]:
    """102 出版国别（`$a CN`）：缺则补。已有的一律不动。"""
    if rec.get_first("102") is not None or rec.get_first("210") is None:
        return []
    rec.fields.append(Field("102", " ", " ", subfields=[Subfield("a", "CN")]))
    return ["102 出版国别已补 CN"]


def autofill_fields(rec: Record) -> list[str]:
    """就地应用规则层补全。返回应用的动作列表（前端展示"自动填了什么"）。

    只补缺失/可规范化项，绝不覆盖已有正确值。
    这是**固定组合**（快速录入等内部管线用）；编目页的「规范一次」走
    `services/optimize.py` 的**可配置**版本（同一批函数，按用户启用的条目执行）。
    """
    applied: list[str] = []
    applied += fill_split_title(rec)
    applied += fill_language(rec)
    applied += fill_pub_place(rec)
    applied += normalize_pub_year(rec)
    applied += clean_size_value(rec)
    applied += fill_default_size(rec)
    # 定长编码字段（放在出版年规整**之后** —— 100 的出版年取自 210$d）
    applied += fill_102(rec)
    applied += fill_105(rec)
    applied += fill_106(rec)
    applied += fill_100(rec)
    applied.extend(unify_isbd(rec))
    return applied


