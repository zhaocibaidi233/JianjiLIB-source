# -*- coding: utf-8 -*-
"""质检规则引擎：数据驱动的单记录校验规则集。

规则库与严重级别定义见 实现说明。
设计原则：规则可注册/启停（P1 支持用户自定义），核心内置默认集。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .clean import CURRENCY_CODES
from .isbn import is_valid, normalize as isbn_normalize
from .marc import Record

LEVEL_ERROR, LEVEL_WARNING, LEVEL_INFO = "ERROR", "WARNING", "INFO"


@dataclass
class Finding:
    rule_id: str
    level: str
    message: str          # 精确描述（含字段号，供导出/审计）
    tag: str = ""
    rule_name: str = ""   # 规则通俗名，如「出版年格式」
    hint: str = ""        # 面向非专业用户的说明与修复建议
    plain: str = ""       # 一句话的通俗问题（不含字段号/技术细节），界面主行

    @property
    def readable(self) -> str:
        """给界面用的一句话：规则名 + 具体问题。"""
        return f"{self.rule_name}：{self.message}" if self.rule_name else self.message


@dataclass
class Rule:
    id: str
    name: str             # 通俗名，直接展示给编目员
    level: str
    fn: Callable[[Record], list[tuple[str, str]]]   # 返回 [(tag, message)]
    hint: str = ""        # 这条规则为什么重要 / 怎么修
    plain: str = ""       # 一句话的通俗问题（界面主行；缺省时前端回退到 message）

    def run(self, rec: Record) -> list[Finding]:
        return [Finding(self.id, self.level, msg, tag, self.name, self.hint, self.plain)
                for tag, msg in self.fn(rec)]


def _check_isbn(rec: Record):
    out = []
    for f in rec.get_fields("010"):
        raw = (f.get("a") or "").strip()
        if not raw:
            continue
        d = isbn_normalize(raw)
        if d is None:
            out.append(("010", f"010$a 无法解析为 ISBN: {raw[:20]}"))
        elif not is_valid(d):
            out.append(("010", f"010$a 校验位错误：{raw[:20]}"))
    return out


def _check_price(rec: Record):
    """010$d 定价：货币码 + 数字，重复货币码（CNYCNY...）视为异常。

    验收实证：套录来源叠加货币码产生 `CNYCNYCNY29.80`，旧规则集无定价校验，
    该值能通过质检并交付给下游 ILS。级别取 WARNING——真实书商定价格式
    五花八门（如 "CNY29.80(全2册)"），定 ERROR 会误伤大量可入库记录。
    """
    import re
    # 货币码最多出现一次，后跟数字（可带 1~2 位小数）。
    # 白名单与清洗管线**共用同一份**（clean.CURRENCY_CODES），避免两处各列一遍、漏改。
    pat = re.compile(rf"^(?:{'|'.join(CURRENCY_CODES)})?\d+(?:\.\d{{1,2}})?$", re.I)
    out = []
    for v in rec.all_values("010", "d"):
        v = v.strip()
        if not v:
            continue
        if not pat.match(v):
            out.append(("010", f"010$d 定价格式异常：{v[:20]}"))
    return out


def _check_required_title(rec: Record):
    if not rec.first_value("200", "a"):
        return [("200", "缺少 200$a 正题名")]
    return []


def _check_required_pub(rec: Record):
    # ⚠️ 出版者/出版年必须**跨全部 210 字段**取值：书商导出数据常把 210 拆成多条
    # （一条只带 $a 出版地、另一条带 $c/$d），只看**第一个** 210 字段会把"其实有
    # 出版社与出版年"的记录判成缺项并**拒收**。
    if rec.get_first("210") is None:
        return [("210", "缺少 210 出版发行项")]
    out = []
    if not (rec.first_value("210", "c") or "").strip():
        out.append(("210", "缺少 210$c 出版者"))
    if not (rec.first_value("210", "d") or "").strip():
        out.append(("210", "缺少 210$d 出版年"))
    return out


def _check_pub_year(rec: Record):
    import re
    out = []
    for v in rec.all_values("210", "d"):
        v = v.strip()
        if v and not re.fullmatch(r"(19|20)\d{2}", v):
            out.append(("210", f"210$d 非 4 位年份：{v}"))
    return out


def _check_no_identifier(rec: Record):
    if rec.get_first("010", "a") is None and rec.get_first("091", "a") is None:
        return [("010", "缺少任何标识号（010 ISBN / 091 统一书号）")]
    return []


def _check_100(rec: Record):
    out = []
    for f in rec.get_fields("100"):
        a = f.get("a") or ""
        if len(a) != 36:
            out.append(("100", f"100$a 长度 {len(a)} ≠ 36"))
        elif not a[:8].isdigit():
            out.append(("100", "100$a 入档日期位非数字"))
    return out


def _check_105(rec: Record):
    """105$a 是 13 位定长编码（图表/内容特征/会议/纪念文集/索引/文学体裁/传记）。

    注：曾误写成 != 28，导致所有正常记录都被报"长度 13 ≠ 28"。
    实测真实书商数据 3000 条，105$a 无一例外都是 13 位。
    """
    out = []
    for f in rec.get_fields("105"):
        a = f.get("a") or ""
        if len(a) != 13:
            out.append(("105", f"105$a 长度 {len(a)} ≠ 13"))
    return out


def _check_isbd_title_punct(rec: Record):
    """200$e（副题名）存在时，$a 应以 ' :' 收尾；$f 存在时 $a/$e 应以 ' /' 收尾。"""
    out = []
    for f in rec.get_fields("200"):
        a = (f.get("a") or "").rstrip()
        e = f.get("e")
        has_f = (f.get("f") or "").strip() != ""
        if not a:
            continue
        if e and not a.endswith(":"):
            out.append(("200", "200$a 与 $e 之间缺少 ISBD 标点 ' :'"))
        if (e or has_f) and not (a.endswith("/") or (e or "").rstrip().endswith("/")):
            out.append(("200", "题名与责任者之间缺少 ISBD 标点 ' /'"))
    return out


def _check_class_no(rec: Record):
    import re
    out = []
    for v in rec.all_values("690", "a"):
        v = v.strip()
        if v and not re.match(r"^[A-Z]", v):
            out.append(("690", f"690$a 分类号结构异常（应字母开头）: {v}"))
    return out


def _check_no_subject(rec: Record):
    if not rec.get_fields("606") and not rec.get_fields("610"):
        return [("606", "无任何主题词（606/610）")]
    return []


def _check_ctrl_chars(rec: Record):
    import re
    pat = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
    out = []
    for f in rec.fields:
        for sf in f.subfields:
            if pat.search(sf.value):
                out.append((f.tag, f"{f.tag}${sf.code} 含控制字符"))
                break
    return out


def _check_dup_subfields(rec: Record):
    """连续重复子字段代码（书商数据实证 93%，606$a$a）。"""
    out = []
    for f in rec.fields:
        codes = [sf.code for sf in f.subfields]
        for i in range(1, len(codes)):
            if codes[i] == codes[i - 1]:
                out.append((f.tag, f"{f.tag} 字段子字段代码连续重复（${codes[i]}）"))
                break
    return out


def _check_author_link(rec: Record):
    f200 = rec.get_first("200")
    if f200 and (f200.get("f") or "").strip() and not rec.get_fields("701"):
        return [("701", "200$f 有第一责任者但无 701 字段")]
    return []


# 章回数 / 卷数：阿拉伯数字开头（如「120回」「第3卷」）。
# 只认「数字 + 回/卷/册/篇/集/章」结尾，**不查**题名本身以汉字数字结尾的情形
# ——《二十四史》《一千零一夜》《三体》这类题名极多，一律不查才不会大面积误报。
_ARABIC_VOLUME = re.compile(r"\d+\s*(?:回|卷|册|篇|集|章)$")


def _check_title_trailing_punct(rec: Record):
    """题名末尾不应带句号（套录/复制带进来的著录残留）。

    只认**全角句号「。」**：半角「.」在英文缩写（如 "etc."）里合法，报了会误伤；
    中文图书正题名以「。」结尾则必是残留。问号/叹号不查
    ——《这样好不好？》这类题名本身就带，属正常著录。
    """
    out = []
    for f in rec.get_fields("200"):
        a = (f.get("a") or "").rstrip()
        if a.endswith("。"):
            out.append(("200", f"200$a 以句号结尾：…{a[-12:]}"))
    return out


def _check_volume_numerals(rec: Record):
    """章回数、卷数建议用汉字著录（如「一百二十回」），并与正题名空一格。

    只查 200$a / 200$h 末尾的「阿拉伯数字 + 回/卷/册/篇/集/章」；
    「与正题名空一格」不做自动判定——题名与章回之间没有可靠分隔符，
    强行拆分会把《二十四史》这类题名判错（见）。
    """
    out = []
    for f in rec.get_fields("200"):
        for code in ("a", "h"):
            v = (f.get(code) or "").strip()
            if v and _ARABIC_VOLUME.search(v):
                out.append(("200", f"200${code} 章回数 / 卷数用了阿拉伯数字：…{v[-12:]}"))
                break
    return out


def _check_language(rec: Record):
    """101$a 正文语种建议只著录一个主要语种（旧版系统说明书著录规定）。

    CNMARC 允许 101$a 重复著录多语种，本馆规范要求收敛为主要语种一个；
    中外文对照读物可例外，故只提示、不阻断。
    """
    vals = [v.strip() for v in rec.all_values("101", "a") if (v or "").strip()]
    if len(vals) > 1:
        return [("101", f"101$a 著录了 {len(vals)} 个语种：{' / '.join(vals[:3])}")]
    return []


def _check_required_100(rec: Record):
    """100 通用处理数据是 CNMARC **必备**字段（``）。

    缺了它，记录在联合编目 / 别家系统里属于**结构不完整**（GB/T 33286 的必备性要求）。
    **只提示、不阻断**：简编流程下编目员可能先存草稿，开启「自动规范」会补出来
    （`ai/fields.py::fill_100`）。
    """
    if rec.get_first("100") is None:
        return [("100", "缺少 100 通用处理数据（CNMARC 必备字段）")]
    return []


def _check_source_801(rec: Record):
    """801 记录来源（`$a` 国家 `$b` 机构 `$c` 日期）—— 套录/交换数据应标明来源。

    本馆自编记录缺它不影响入库，故只作 INFO 提示；套录进来的记录由
    `services/localize.py` 自动改写为本馆来源。
    """
    if rec.get_first("801") is None:
        return [("801", "缺少 801 记录来源")]
    return []


DEFAULT_RULES: list[Rule] = [
    Rule("R0101", "ISBN 校验位", LEVEL_ERROR, _check_isbn,
         "ISBN 最后一位是校验位，错一位即不通过。请按书上印的 ISBN 重新输入；"
         "无 ISBN 的旧书可留空，不影响入库。",
         plain="ISBN 校验位不正确"),
    Rule("R0102", "定价格式", LEVEL_WARNING, _check_price,
         "定价建议写成「CNY29.80」这种格式。重复的货币码（如 CNYCNY29.80）保存时会自动清理。",
         plain="定价格式不规范"),
    Rule("R0201", "必备正题名", LEVEL_ERROR, _check_required_title,
         "每本书都必须有正题名，请在左侧「正题名」一栏填写。",
         plain="未填写题名"),
    Rule("R0202", "必备出版项", LEVEL_ERROR, _check_required_pub,
         "出版信息不完整。至少要填出版者和出版年，出版地能补则补。",
         plain="出版信息不完整"),
    Rule("R2101", "出版年格式", LEVEL_ERROR, _check_pub_year,
         "出版年请填 4 位数字，例如 2020。「2020.5」「2020年」这类写法保存时会自动规整为 2020。",
         plain="出版年请填 4 位数字"),
    Rule("R1001", "100 字段合法性", LEVEL_ERROR, _check_100,
         "100 字段前 8 位是入档日期，必须是数字（如 20201004）。套录数据异常时可让自动规范重算。",
         plain="100 字段的内容不符合标准格式"),
    Rule("R0901", "控制字符", LEVEL_ERROR, _check_ctrl_chars,
         "数据里混进了看不见的控制字符（多见于复制粘贴）。保存时会自动清除，无需手工处理。",
         plain="数据中包含不可见的控制字符"),
    Rule("R0902", "重复子字段", LEVEL_ERROR, _check_dup_subfields,
         "同一字段里出现了重复的子字段（例如两个 $a）。保存时会自动合并。",
         plain="同一个字段里出现了重复内容"),
    Rule("R0103", "标识号缺失", LEVEL_WARNING, _check_no_identifier,
         "没有 ISBN 会影响查重和后续套录命中，建议补充。",
         plain="该书没有 ISBN"),
    Rule("R1002", "105 字段合法性", LEVEL_WARNING, _check_105,
         "105 字段描述图书形态（有无插图、开本等），标准长度为 13 位。",
         plain="105 字段长度不符合标准"),
    Rule("R2001", "ISBD 标点", LEVEL_WARNING, _check_isbd_title_punct,
         "题名与责任者之间的标点要符合 ISBD 规范（如「/」前空一格）。开启自动规范会帮你补齐。",
         plain="题名与责任者之间的标点不规范"),
    Rule("R6901", "分类号结构", LEVEL_WARNING, _check_class_no,
         "中图法分类号以一个大写字母开头，例如 I247.5、K825.4。",
         plain="分类号不符合中图法结构"),
    Rule("R6001", "主题词缺失", LEVEL_WARNING, _check_no_subject,
         "补充主题词能明显提升读者检索命中率，建议至少填 1～2 个。",
         plain="未填写主题词"),
    Rule("R7001", "责任者连接", LEVEL_INFO, _check_author_link,
         "200$f 填了责任者，建议同时在 701 字段做规范著录，便于按责任者检索。",
         plain="责任者未做规范著录"),
    # ---- 著录规则细化 ----
    # 一律 WARNING/INFO：只提示、不阻断保存与导入（ERROR 才会被拒收）。
    Rule("R1010", "语种著录", LEVEL_WARNING, _check_language,
         "正文语种建议只著录一个主要语种。若是中外文对照读物，可忽略此提示。",
         plain="著录了多个语种"),
    Rule("R2002", "题名结尾标点", LEVEL_WARNING, _check_title_trailing_punct,
         "题名末尾不加句号，多为套录或复制时带入，删除即可。",
         plain="题名末尾有句号"),
    Rule("R2003", "章回数写法", LEVEL_INFO, _check_volume_numerals,
         "章回数、卷数建议用汉字著录（如「一百二十回」），并与题名空一格。",
         plain="章回数或卷数写成了阿拉伯数字"),
    # ---- 标准合规补充 ----
    # 同样只提示不阻断。100 在 CNMARC 里是**必备**字段，缺了记录结构不完整；
    # 801 是记录来源，本馆自编记录缺它不影响入库，故降为 INFO。
    Rule("R1003", "必备通用处理数据", LEVEL_WARNING, _check_required_100,
         "该字段记录入档日期、出版年、编目语种等定长代码，是书目记录的必备部分。"
         "开启「自动规范」可自动补齐，无需手工填写。",
         plain="缺少通用的处理数据"),
    Rule("R8010", "记录来源缺失", LEVEL_INFO, _check_source_801,
         "该字段标明书目来源机构。本馆自编记录缺它不影响使用，"
         "但套录或上交联合编目时建议补上，便于溯源。",
         plain="未标明书目来源"),
]


def validate(rec: Record, rules: list[Rule] | None = None) -> list[Finding]:
    """运行规则集，返回全部发现（调用方按级别过滤/汇总）。"""
    findings: list[Finding] = []
    for rule in (rules or DEFAULT_RULES):
        findings.extend(rule.run(rec))
    return findings
