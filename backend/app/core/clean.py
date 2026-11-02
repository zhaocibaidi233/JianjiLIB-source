# -*- coding: utf-8 -*-
"""导入清洗管线：把书商来源数据的常见毛病规整为规范形态。

规则全部来自真实数据实证（、06）：
- NUL/控制字符剥离（某馆配商数据 4 处 NUL 实证）
- 606 等字段连续重复子字段合并（93% 发生率）
- 200 拼音从 $9 迁移到 $A（99.96% 实证）
- 题名拆分（规则初拆副题名/丛书名，丛书名嵌套留给 AI）
- 责任者拆分（多作者、责任方式后缀）
- 出版年规范化（2007.9 / 2017-09 / 2020年 -> 2007）
- 定价规范化：重复货币码前缀去重（CNYCNYCNY29.80 -> CNY29.80，验收实测）
"""
from __future__ import annotations

import re

from .isbd import unify_isbd
from .marc import Field, Record, Subfield

CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
ISBD_TAIL_RE = re.compile(r"\s*[:;=/,.]\s*$")
YEAR_RE = re.compile(r"(19|20)\d{2}")
SERIES_TAIL_RE = re.compile(r"(丛书|系列|文库|读本|书系|译丛|大系|宝典|精选|合集)$")
# 支持的货币码 —— **唯一一份**，`core/validators.py` 也 import 它做定价格式校验。
# 与「字典中心 → 货币类型」的内置默认（services/dictionaries.py::DEFAULT_CURRENCIES）对齐；
# 用户在字典里自加的币种走不到这里（清洗阶段原样放过，由质检提示人工），
# 因为本模块在导入管线上、拿不到 db。新增内置币种时**两处要一起改**。
CURRENCY_CODES = ("CNY", "RMB", "USD", "HKD", "TWD", "JPY", "EUR", "GBP", "KRW", "SGD")
_CUR_ALT = "|".join(CURRENCY_CODES)
# 重复货币码前缀：CNYCNYCNY / CNY CNY / RMB RMB
DUP_CUR_RE = re.compile(rf"^(?:(?:{_CUR_ALT})\s*)+", re.I)
PRICE_VALUE_RE = re.compile(rf"^({_CUR_ALT})?\s*(\d+(?:\.\d+)?)\s*元?$", re.I)

ROLE_WORDS = ["总主编", "总主编", "副主编", "主编", "编著", "编译", "校译", "译", "绘", "改编", "注", "校", "著", "编"]
MULTI_AUTHOR_RE = re.compile(r"[，,、;；/]")
# 701 自动补全只接受这类“第一责任”角色；翻译、绘图、校注等不是主要责任者。
PRIMARY_ROLES = {"著", "编著", "主编", "副主编", "总主编", "编", "编写", "撰"}
SECONDARY_ROLES = {
    "译", "编译", "校译", "绘", "绘图", "改编", "注", "校", "摄影",
    "口述", "整理", "翻译", "插图", "设计", "编绘", "著译", "校注", "点校",
}
ORG_MARKERS = (
    "编委会", "编写组", "委员会", "协会", "学会", "出版社", "出版公司",
    "出版集团", "公司", "集团", "大学", "学院", "图书馆", "研究所",
    "研究院", "研究中心", "编辑部", "工作室", "政府", "教育局", "教育厅",
    "办公室", "医院", "银行", "学校", "中学", "小学", "通讯社", "报社",
    "杂志社", "书店", "新华社",
)
NAME_PLACEHOLDERS = {"佚名", "无名氏", "不详", "无责任者", "阙名"}

# 汉字（含扩展 A / 兼容区）—— 用来判「这串是不是拼音/罗马化形式」。
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def looks_romanized(v: str | None) -> bool:
    """这串值像不像拼音（罗马化形式）。

    判据：**没有汉字 + 至少一个拉丁字母**。比原来那条 `^[A-Za-z][A-Za-z ]+$` 宽 ——
    标准允许 `$9` 里「非汉字成分（外文字母、阿拉伯数字、标点符号等）保留原有形式」，
    实测就有 `《hong lou meng 》de ren wen zhi hui` 这种（旧正则的 `^[A-Za-z]` 起头
    直接把它漏掉了）。
    """
    s = (v or "").strip()
    return bool(s) and not CJK_RE.search(s) and re.search(r"[A-Za-z]", s) is not None


def strip_ctrl(s: str) -> str:
    return CTRL_RE.sub("", s)


# ---------- 题名 ----------

def normalize_title(title: str) -> str:
    """去掉 ISBD 尾随标点与多余空白。"""
    return ISBD_TAIL_RE.sub("", title.strip())


def split_title(title: str) -> tuple[str, str | None, str | None]:
    """规则初拆（正题名, 副题名, 丛书名）。

    实测（06 文档）：发货清单 80% 书名需要拆分；副题名一级拆分规则可靠，
    丛书名嵌套在副题名内的情况规则只能解一部分，剩余交 AI 精拆。
    """
    main, subtitle, series = title.strip(), None, None
    if "：" in main:
        main, subtitle = main.split("：", 1)
        subtitle = subtitle.strip() or None
    # 主题名内的丛书名：末段以丛书词结尾
    parts = [p for p in re.split(r"[.．]", main) if p]
    if len(parts) > 1 and SERIES_TAIL_RE.search(parts[-1]):
        series = parts[-1]
        main = parts[0]
    # 副题名内的丛书名（如 "国之重器.舰船科普丛书"）
    if subtitle:
        sparts = [p for p in re.split(r"[.．]", subtitle) if p]
        if len(sparts) > 1 and SERIES_TAIL_RE.search(sparts[-1]):
            series = sparts[-1]
            subtitle = sparts[0]
    return normalize_title(main), subtitle, series


# ---------- 责任者 ----------

def split_author(author: str) -> tuple[list[str], str | None]:
    """拆分多作者与责任方式后缀：'张志通主编' -> (['张志通'], '主编')"""
    names, role = [], None
    for part in MULTI_AUTHOR_RE.split(author or ""):
        part = part.strip()
        if not part:
            continue
        for w in sorted(set(ROLE_WORDS), key=len, reverse=True):
            if part.endswith(w) and len(part) > len(w):
                role = role or w
                part = part[: -len(w)].strip()
                break
        if part:
            names.append(part)
    return names, role


# ---------- 出版年 ----------

def normalize_year(text: str) -> str | None:
    """'2007.9' / '2017-09' / '2020年' / '二〇二〇' -> '2007'（4位年），取不出返回 None。"""
    m = YEAR_RE.search(text or "")
    return m.group(0) if m else None


def normalize_price(text: str) -> str:
    """规整 010$d 定价：统一货币码、去重重复前缀、补两位小数。

    实证（验收发现）：套录来源会在已有货币码上反复叠加，
    出现 `CNYCNYCNY29.80` 这类值并能通过质检交付给 ILS。

    >>> normalize_price("CNYCNYCNY29.80")  -> "CNY29.80"
    >>> normalize_price("29.8")            -> "CNY29.80"
    >>> normalize_price("￥29.8")          -> "CNY29.80"

    无法识别的复杂值（如 "CNY29.80(全2册)"）原样返回，交给质检提示人工，
    不在清洗阶段冒险改写。
    """
    s = (text or "").strip()
    if not s:
        return ""
    # 全角/半角人民币符号统一为货币码
    s = s.replace("￥", "CNY").replace("¥", "CNY")
    # 重复货币码前缀去重：CNYCNYCNY29.80 / "CNY CNY 29.80" -> CNY29.80
    m_dup = DUP_CUR_RE.match(s)
    if m_dup and m_dup.group(0).strip():
        # 只取第一个货币码（勿用 [A-Za-z]+，会贪婪吃掉 CNYCNYCNY 整体）
        cur = re.match(rf"(?:{_CUR_ALT})", m_dup.group(0).strip(), re.I)
        s = (cur.group(0).upper() if cur else "") + s[m_dup.end():]
    s = s.strip()
    m = PRICE_VALUE_RE.match(s)
    if not m:
        return s                                   # 复杂值原样返回
    cur = (m.group(1) or "CNY").upper()
    num = m.group(2)
    if "." not in num:
        num = f"{int(num)}.00"
    else:
        head, _, frac = num.partition(".")
        num = f"{int(head)}.{frac[:2].ljust(2, '0')}"
    return f"{cur}{num}"


# ---------- 记录级清洗 ----------

def merge_duplicate_subfields(f: Field) -> bool:
    """合并连续重复子字段（606 $a$a恐龙 -> $a恐龙）。返回是否修改。

    连续同码子字段中：仅一个非空值则保留之；多个非空值以'；'连接（保守，
    留待人工确认，不丢数据）。
    """
    changed = False
    out: list = []
    for sf in f.subfields:
        if out and out[-1][0] == sf.code:
            changed = True
            prev_val = out[-1][1]
            vals = [v for v in (prev_val, sf.value) if v]
            out[-1] = (sf.code, "；".join(vals))
        else:
            out.append((sf.code, sf.value))
    if changed:
        f.subfields = [Subfield(code, value) for code, value in out]
    return changed


def _personal_name_701(raw: str | None) -> tuple[str, str] | None:
    """从 200$f 提取可安全写入 701 的个人责任者，返回 (姓名, 责任方式)。

    只在能确认是**单个个人**时返回；机构、多人、译/绘等非主要责任角色一律
    跳过，宁可不补，也不写错检索点。
    """
    text = (raw or "").strip().strip("。.；;")
    if not text or text in NAME_PLACEHOLDERS:
        return None
    if MULTI_AUTHOR_RE.search(text) or "等" in text:
        return None
    if any(ch in text for ch in "()（）[]【】"):
        return None
    if any(ch.isdigit() for ch in text):
        return None

    role = ""
    for suffix in sorted(set(ROLE_WORDS) | PRIMARY_ROLES | SECONDARY_ROLES,
                         key=len, reverse=True):
        match = re.search(rf"\s*{re.escape(suffix)}$", text)
        if match:
            name = text[:match.start()].strip("()（）:：,，.。 ")
            if name:
                role = suffix
                text = name
            break
    if role and role not in PRIMARY_ROLES:
        return None

    text = re.sub(r"\s+", " ", text).strip("()（）[]【】:：,，.。 ")
    if not (2 <= len(text) <= 50):
        return None
    if any(marker in text for marker in ORG_MARKERS):
        return None
    if not (CJK_RE.search(text) or re.search(r"[A-Za-z]", text)):
        return None
    return text, role


def _fill_author_701(rec: Record) -> bool:
    """200$f 有单一主责任个人且无 701 时，补规范检索点。幂等。"""
    if rec.get_fields("701"):
        return False
    f200 = rec.get_first("200")
    if f200 is None:
        return False
    parsed = _personal_name_701(f200.get("f"))
    if parsed is None:
        return False
    name, role = parsed
    field = Field("701", " ", "0", subfields=[Subfield("a", name)])
    if role:
        field.add("4", role)
    rec.fields.append(field)
    return True


def normalize_class_no(value: str | None) -> str:
    """分类号归一：strip + 大写（`i247.5` → `I247.5`）。

    中图法类目字母全大写、数字与点不受影响，整串 upper 是安全的。为什么必须归一：
    书次号计数器按「图书库×分类号」字符串分键，`i247.5` 与 `I247.5` 会 fork 出
    两个各自从 1 编号的计数器，产出的索书号肉眼难分。调用方：clean_record 的
    690$a 清洗（覆盖 UI 保存与批量导入）+ /cataloging/callno 与 callno-preview
    的取号/预览入口（预览键必须与保存键同串，否则预览号和实发号错位）。
    """
    return (value or "").strip().upper()


def clean_record(rec: Record, migrate_pinyin: bool = True) -> dict:
    """导入清洗管线（可复现、可统计）。返回处理统计。

    步骤：控制字符剥离 -> 连续重复子字段合并 -> 010$d 定价规范化
          -> 210$d 出版年规范化 -> 690$a 分类号大写归一
          -> 200 字段 ISBD 标点规范化
          -> （可选）把非标准的 `$A` 拼音归位到标准 `$9`
          -> 200$f 可识别为单一主责任个人时补 701。

    ISBD 标点在这里补：简编表单这类"题名/责任者分栏"的界面会在展示时剥掉
    尾部标点，入库必须由本管线补回，否则数据不符合 CNMARC 著录格式。
    """
    stats = {"ctrl_stripped": 0, "dup_merged": 0, "price_normalized": 0,
             "year_normalized": 0, "class_normalized": 0,
             "isbd_fixed": 0, "pinyin_migrated": 0,
             "author_701_added": 0}
    for f in rec.fields:
        for sf in f.subfields:
            cleaned = strip_ctrl(sf.value)
            if cleaned != sf.value:
                stats["ctrl_stripped"] += 1
                sf.value = cleaned
        if f.data and CTRL_RE.search(f.data):
            stats["ctrl_stripped"] += 1
            f.data = strip_ctrl(f.data)
        if merge_duplicate_subfields(f):
            stats["dup_merged"] += 1
        if f.tag == "010":                          # 定价：重复货币码去重
            for sf in f.subfields:
                if sf.code == "d" and sf.value:
                    fixed = normalize_price(sf.value)
                    if fixed != sf.value:
                        stats["price_normalized"] += 1
                        sf.value = fixed
        if f.tag == "210":                          # 出版年：2007.9 / 2008.09 -> 2007
            for sf in f.subfields:
                if sf.code == "d" and sf.value:
                    y = normalize_year(sf.value)
                    if y and y != sf.value.strip():
                        stats["year_normalized"] += 1
                        sf.value = y
        if f.tag == "690":                          # 分类号：统一大写（见 normalize_class_no）
            for sf in f.subfields:
                if sf.code == "a" and sf.value:
                    fixed = normalize_class_no(sf.value)
                    if fixed != sf.value:
                        stats["class_normalized"] += 1
                        sf.value = fixed
    if unify_isbd(rec):
        stats["isbd_fixed"] = 1
    if migrate_pinyin:
        stats["pinyin_migrated"] = migrate_pinyin_to_9(rec)
    if _fill_author_701(rec):
        stats["author_701_added"] = 1
    return stats


# GB/T 33286—2016 里把 `$9` 定义为「汉语拼音」的字段（**逐个字段的子字段表核对过**）。
#
# 组成：标准**明确列出** `$9` 的字段 + 声明「其他子字段与某字段相同」的继承字段
# （702←701、712←711、722←721）。
# ⚠️ **710 / 720 故意不收**：标准里 710 的子字段表（$a~$h/$p/$3/$4）**没有 `$9`**，
# 而 711 才列 `$9` 并说「其余与 710 相同」—— 疑似标准本身的疏漏，但按「不自己编」
# 的口径就不给它加。要收得先有依据。
PINYIN_SUBFIELD_TAGS = frozenset((
    "200",
    "500", "501", "503", "510", "511", "512", "513", "514", "515", "516",
    "517", "518", "520", "530", "540", "541", "545", "560",
    "700", "701", "702", "711", "712", "721", "722", "730",
))


def _insert_p9_after_first_a(f: Field, value: str) -> None:
    """把 `$9` 插到**第一个 `$a` 之后**。

    标准 §6.3：「`$9` 紧随在所标识的相关子字段之后」。原来的实现是 `remove` 之后
    `add`，把 `$9` 甩到字段末尾 —— 那既改了子字段码、又破了位置。
    没有 `$a` 时退化成追加（总比丢掉强）。
    """
    idx = next((i for i, sf in enumerate(f.subfields) if sf.code == "a"), None)
    if idx is None:
        f.add("9", value)
    else:
        f.subfields.insert(idx + 1, Subfield("9", value))


def migrate_pinyin_to_9(rec: Record) -> int:
    """把**非标准**的 `$A`（拼音）归位到**标准**子字段 `$9`。返回迁移条数。

    GB/T 33286—2016：汉语拼音子字段是 `$9`（§5.11「$9 汉语拼音」、§6.8「含 9 的
    字段/子字段是追加的中国专用字段」、各字段子字段表），**标准全文没有 `$A`**。

    ⚠️ 方向：标准字段是 `$9`，非标准字段是 `$A`。归一化只把
    「像拼音」的 `$A` 合并到标准 `$9`，不反向修改。

    只处理无汉字且含拉丁字母的值；值里含汉字的 `$A` 原样保留，
    那通常是子字段串位等其他问题。若该字段已有 `$9`，只去掉多余的 `$A`，不覆盖。
    """
    n = 0
    for tag in PINYIN_SUBFIELD_TAGS:
        for f in rec.get_fields(tag):
            for sf in list(f.subfields):
                if sf.code != "A" or not looks_romanized(sf.value):
                    continue
                f.subfields.remove(sf)
                n += 1
                if not f.all("9"):
                    _insert_p9_after_first_a(f, sf.value.strip())
    return n

