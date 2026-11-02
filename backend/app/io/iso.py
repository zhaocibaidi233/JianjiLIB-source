# -*- coding: utf-8 -*-
"""ISO 2709 文件读写：编码自动识别 + 容错解析 + 规范写出。"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..core.marc import (MarcWriteError, ParsedItem, Record, iter_iso,
                         record_to_iso)

MAX_PARSE_ERRORS = 1000

#: 导入/预演响应里最多带多少条错误明细。**计数不受此限制** —— 列表可以截，
#: `parse_error_count` / `parse_partial` 必须如实（见 `parse_response_fields`）。
PARSE_ERRORS_IN_RESPONSE = 20


def detect_encoding(data: bytes) -> str:
    """按首条记录头标第 9 位判断；'a' 为 UTF-8，其余按 GB18030（国内存量数据默认）。"""
    for item in iter_iso(data):
        if item.record is not None:
            return "utf-8" if item.record.leader[9:10] == "a" else "gb18030"
        break  # 只看第一条
    return "gb18030"


def read_iso(source: bytes | str | Path) -> tuple[list[Record], list[dict]]:
    """读取 ISO 2709 文件/字节串。返回 (记录列表, 错误列表)。

    错误条目: {"index": 序号, "error": 描述, "warning"?: True}——坏记录不阻断整批。
    `warning=True` 表示记录**已容错接收**（如末尾无 0x1D），只是留痕；
    只有没有该键的条目才是"这条没进来"。
    错误数有上限（MAX_PARSE_ERRORS）：垃圾数据不该把内存吃光。
    """
    data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    records, errors = [], []
    for item in iter_iso(data):
        if item.record is not None:
            records.append(item.record)
            if item.error:  # 容错接收但留痕（如末尾无 0x1D）
                errors.append({"index": item.index, "error": item.error,
                               "warning": True})
        else:
            errors.append({"index": item.index, "error": item.error or "未知解析错误"})
        if len(errors) >= MAX_PARSE_ERRORS:
            errors.append({"index": -1,
                           "error": f"解析错误已达上限 {MAX_PARSE_ERRORS} 条，剩余内容未读取"
                                    "（文件可能不是 ISO 2709 数据）"})
            break
    return records, errors


def parse_summary(records: list[Record], errors: list) -> dict:
    """解析结果摘要：错误数 / 警告数 / 总条目数 / 是否部分成功。

    - `parse_error_count`：**没进来**的条目数（不含容错接收的警告）；
    - `parse_warning_count`：已入库但留痕的条目数（如末尾无 0x1D）；
    - `parse_total`：本次识别到的条目数 = 成功记录 + 失败条目；
    - `parse_partial`：有失败条目或触发错误上限 → True。**为 True 时导入结果
      不是文件的完整副本**，界面必须提示；
    - `parse_errors_truncated`：还有错误明细没随响应返回（含上限截断）。
    """
    failed = warnings = 0
    capped = False
    for e in errors:
        if isinstance(e, dict):
            if e.get("index") == -1:
                capped = True
                continue
            if e.get("warning"):
                warnings += 1
                continue
        failed += 1
    return {
        "parse_error_count": failed,
        "parse_warning_count": warnings,
        "parse_total": len(records) + failed,
        "parse_partial": bool(failed or capped),
        "parse_errors_truncated": bool(capped or len(errors) > PARSE_ERRORS_IN_RESPONSE),
    }


def parse_response_fields(records: list[Record], errors: list) -> dict:
    """API 响应里的解析字段（导入 / 预演共用一份，口径不会两处漂移）。

    明细只回前 `PARSE_ERRORS_IN_RESPONSE` 条，但计数与截断标志覆盖**全部**错误 ——
    旧实现只回前 10 条且不计数，坏掉的几十条看起来像不存在。
    """
    return {"parse_errors": list(errors[:PARSE_ERRORS_IN_RESPONSE]),
            **parse_summary(records, errors)}


def read_iso_detailed(source: bytes | str | Path) -> list[ParsedItem]:
    """需要逐条错误信息的场景使用（保留全部 ParsedItem）。"""
    data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    return list(iter_iso(data))


# ---------- 字段查看 / 字段映射（2026-10-07 用户要求） ----------
#
# 背景：ISO 没有"列"的概念，字段本身就是对应关系。但书商数据常带我们不认的字段
# （用户举例：数据在 `909$b`，我们没有 909$b），需要先让用户**看到**文件里有哪些
# 字段，再决定"把哪个字段指到我们的哪个字段上"（`909$b` → `905$f`）。

_TAGCODE_RE = re.compile(r"^(\d{3})\$([0-9A-Za-z])$")


def parse_tag_code(text: str) -> tuple[str, str] | None:
    """`"905$f"` → `("905", "f")`；不合法返回 None（调用方按"跳过"处理）。"""
    m = _TAGCODE_RE.match((text or "").strip())
    return (m.group(1), m.group(2)) if m else None


def iso_field_stats(records: list[Record]) -> list[dict]:
    """统计这批记录里出现过的**子字段**（`tag$code`），按出现次数倒序。

    这是做字段映射的依据：用户先看到"文件里有 `909$b`，共 812 处"，才会想到
    把它指到我们的 `905$f` 上。
    """
    stats: dict[tuple[str, str], dict] = {}
    for rec in records:
        for f in rec.fields:
            for sf in f.subfields:
                key = (f.tag, sf.code)
                st = stats.get(key)
                if st is None:
                    st = stats[key] = {"tag": f.tag, "code": sf.code,
                                       "key": f"{f.tag}${sf.code}", "count": 0,
                                       "sample": (sf.value or "")[:60]}
                st["count"] += 1
    return sorted(stats.values(), key=lambda d: (-d["count"], d["key"]))


# ---- 书商/国产系统导出数据的「兼容备选字段」 ----
#
# 部分书商导出数据中，`909` 与 `985` 是**每册一条**字段；不同系统放置的位置
# 可能不同，所以需要把它们映射到本系统的字段上（如 `909$a` 条码号 → `905$b`）。
#
# ⚠️ 三条元组：来源 `tag$code` / 目标 `tag$code` / 说明。顺序即**优先级** ——
# 同一个目标被多个源命中时，靠前的赢（见 `suggest_mapping`）。
#
# ⚠️ 2026-10-09：目标随我方 905 布局改为**标准子字段** —— 条码进 `$b`（登录号）、
# 索书号进 `$s`（旧布局是条码 `$h`、索书号 `$b`）。改这里就等于改"书商字段往哪落"。
ISO_COMPAT_FIELDS: list[tuple[str, str, str]] = [
    ("909$a", "905$b", "条码号（909 是每册一条）"),
    ("909$x", "905$s", "索书号"),
    ("909$J", "905$c", "排架号"),
    ("909$c", "010$d", "定价"),
    # 985 与 909 内容重叠（同一份数据的两套兼容字段），只作备选
    ("985$a", "905$b", "条码号（985 备选）"),
    ("985$h", "905$b", "条码号（985 备选）"),
    ("985$d", "010$d", "定价（985 备选）"),
    # 995 是另一家常用的私有字段（原先 code 里已按它取条码）
    ("995$a", "905$b", "条码号（995 备选）"),
    ("995$f", "905$b", "条码号（995 备选）"),
    ("995$h", "905$b", "条码号（995 备选）"),
]


def compat_field_options() -> list[dict]:
    """兼容字段表（供界面展示"这类字段我们认得，会这样关联"）。"""
    return [{"source": src, "target": dst, "label": label}
            for src, dst, label in ISO_COMPAT_FIELDS]


def suggest_mapping(present_keys, reasons: dict[str, str] | None = None) -> dict[str, str]:
    """按兼容字段表给出**自动关联**建议（源 `tag$code` → 目标 `tag$code`）。

    两条唯一性规则（用户 2026-10-07：「可以让必要的关联具有唯一性」）：

    1. **一个目标只给一个源**：`909` 与 `985` 都带条码，谁先命中谁占 `905$b`，
       另一个不再自动关联 —— 否则两个源都往同一处搬，目标里会多出一份重复值；
    2. **目标在文件里本来就有值 → 跳过**：文件自带 `010$d` 定价时，不再把 `909$c`
       搬过去（那只会产生重复子字段）。按 `tag$code` 逐个子字段精细判断。

    `reasons`：传入一个 dict 时，把**文件里有、但没被自动关联**的源字段的**原因**
    填进去（`{"909$a": "905$b 在文件里已有值，不需要搬运"}`）。

    为什么要这个：规则 2 会让"905 已经齐全"的文件一条建议都没有，界面只显示
    "0 个已映射"、点「应用建议」也没反应，用户会以为坏了。
    把原因摆出来，才知道是"不用搬"而不是"搬不了"。

    只给建议，界面上仍可逐条改。
    """
    present = {str(k) for k in (present_keys or [])}
    out: dict[str, str] = {}
    claimed: dict[str, str] = {}
    for src, dst, _label in ISO_COMPAT_FIELDS:
        if src not in present or src == dst:
            continue
        if dst in present:
            if reasons is not None:
                reasons[src] = f"{dst} 在文件里已有值，不需要搬运"
            continue
        if src in out:
            continue
        if dst in claimed:
            if reasons is not None:
                reasons[src] = f"{dst} 已经由 {claimed[dst]} 提供"
            continue
        out[src] = dst
        claimed[dst] = src
    return out


def record_rows_json(rec: Record) -> list[dict]:
    """一条记录 → **一行一个 MARC 字段**（子字段在同行横排）。

    用户 2026-10-07：「marc 预览字段占一行，子字段在同行横排就行，现在这样不直观」。
    `subfields` 给界面渲染用（可给 `$` 码上色），`text` 是纯文本一行。
    """
    rows: list[dict] = []
    for f in rec.fields:
        if f.is_control():
            rows.append({"tag": f.tag, "ind": "", "control": True,
                         "subfields": [], "text": (f.data or "").strip()})
            continue
        subs = [[sf.code, sf.value] for sf in f.subfields]
        rows.append({"tag": f.tag, "ind": f"{f.ind1}{f.ind2}", "control": False,
                     "subfields": subs,
                     "text": " ".join(f"${code}{value}" for code, value in subs)})
    return rows


# 我方**人工命名**的字段（供"指定到我们的字段"下拉，也用于判断"这个字段本来就是我们的"）。
# **不是白名单**——用户也可以直接手填；这张表只是把常用著录项列出来，省得逐个手打。
#
# 2026-10-07 按真实数据补齐：原先只有简编表单那几十项，导致 `606$x`（学科复分）、
# `100$a`（入档数据）、`510$a`（并列题名）、`801$b`（编目机构）这类**标准 CNMARC 字段**
# 在映射表里被判成"不认识"，看起来像没对上（用户反馈：「对上的为什么还是没关联」）。
#
# 2026-10-08 起改为**字典驱动**：这张人工表只保留"我方语义与旧版系统声明不同"的条目
# （如 `905$s` 索书号 —— 旧版系统的 905 声明里没有它，它的 `$b` 是登录号）与**非 CNMARC** 的书商字段
# （`995` 系），其余标准子字段由 `services/marc_fields` 从 `字段字典文件` 字典**自动补齐**
# （见 `_target_fields()`）。所以这里**变短是正常的**，不是漏了。
_CURATED_ISO_TARGETS: list[tuple[str, str, str]] = [
    # 标识号
    ("010", "a", "ISBN"), ("010", "b", "装订"), ("010", "d", "定价"),
    ("011", "a", "ISSN"),
    ("091", "a", "统一书号"),
    # 编码信息（定长字段）
    ("100", "a", "入档数据"), ("101", "a", "语种"),
    ("102", "a", "出版国别"), ("102", "b", "出版国代码"),
    ("105", "a", "编码数据（图书）"), ("106", "a", "形态特征"),
    # 题名与责任者
    ("200", "a", "正题名"), ("200", "b", "一般资料标识"), ("200", "d", "并列题名"),
    ("200", "e", "副题名"), ("200", "f", "第一责任者"), ("200", "g", "其他责任者"),
    ("200", "h", "分卷号"), ("200", "i", "分卷名"), ("200", "9", "拼音($9→$A)"),
    ("205", "a", "版次"),
    ("210", "a", "出版地"), ("210", "c", "出版者"), ("210", "d", "出版年"),
    ("215", "a", "页数"), ("215", "c", "图表"), ("215", "d", "尺寸"), ("215", "e", "附件"),
    ("225", "a", "丛题名"), ("225", "f", "丛编责任者"), ("225", "h", "丛编分册"),
    ("225", "v", "丛编卷次"),
    ("300", "a", "附注"), ("330", "a", "内容提要"),
    # 连接款目块（4XX）—— ⚠️ 2026-10-09 更正：**410 才是「丛编」**，461 是「总集」。
    # 国图编目园地与西安交大图书馆的 CNMARC 4XX 定义：
    #   410 丛编 —— 「225 字段第 1 指示符的值为 0 和 2 时，书目记录中应包含相应的 410 字段」
    #   461 总集 / 462 分集 / 463 单册 —— 「当单册或分集独立记录时，以 461 记载总集的资料」
    # 此前这里把 461 标成"丛编连接"是**标错**（461 是总集）。真库里的 461 来自书商数据。
    ("410", "1", "丛编连接标识"), ("410", "a", "丛编（连接）"),
    ("461", "1", "总集连接标识"), ("461", "a", "总集（连接）"),
    ("510", "a", "并列题名"), ("510", "9", "拼音($9→$A)"),
    # 主题
    ("606", "a", "主题词"), ("606", "x", "学科复分"), ("606", "y", "地区复分"),
    ("606", "z", "年代复分"), ("606", "j", "形式复分"),
    # 分类
    ("690", "a", "分类号"), ("690", "v", "版次"),
    # 责任者
    ("701", "a", "责任者"), ("701", "c", "责任者修饰"), ("701", "4", "责任方式"),
    ("701", "9", "拼音($9→$A)"),
    ("702", "a", "其他责任者"), ("702", "c", "责任者修饰"), ("702", "4", "责任方式"),
    ("711", "a", "团体责任者"), ("711", "4", "责任方式"),
    # 记录来源
    ("801", "a", "记录来源国"), ("801", "b", "编目机构"), ("801", "c", "编目日期"),
    # 馆藏相关：**必须人工保留**——905 是我方馆藏字段，`$s` 索书号与 `$c` 排架号在
    # 旧版系统的字段字典声明里没有（它写的是 `$a$b$c$d$e$v$y`，其中 `$b` 是登录号），
    # 字典补不出来。⚠️ **2026-10-09 起我方按标准把条码放 `$b`、索书号放 `$s`**；
    # 旧布局的 `905$h`（条码）已不再产生，故**不再列成目标**（读取侧仍兼容）。
    ("905", "a", "馆藏地点"), ("905", "b", "登录号（条码）"), ("905", "c", "排架号"),
    ("905", "s", "索书号"), ("905", "v", "卷册号"),
    # 2026-10-09 补（对标参考系统编目系统）：`905$Z` 著者号 / `905$S` 四角号是**别的编目
    # 系统**塞进 905 的约定子字段。我方数据模型没有这两个概念，但**必须认得**：
    #   ① 认得了才在字段映射界面显示「同名对应」，不再被当成"不认识的脏子字段"
    #      （`/import/iso/preview` 的 `known` 就是拿 `target_field_options()` 比的）；
    #   ② `services/record_projection.py::EXTRA_905_CODES` 会在 905 重建时把它们**保留**下来，
    #      否则"导入图书库"这一步会随 905 一起静默丢掉（905 由馆藏表重建，见 README 规则 5）。
    ("905", "Z", "著者号"), ("905", "S", "四角号"),
    # 995 是**书商私有字段**（非 CNMARC），字典里没有，只能人工列
    ("995", "a", "995$a"), ("995", "f", "995$f"), ("995", "h", "995$h"),
]


@lru_cache(maxsize=1)
def _target_fields() -> list[tuple[str, str, str]]:
    """人工命名 + 字典补齐后的**目标字段全表**（顺序：先人工常用项，再按字段号加字典项）。

    字典补齐只加"有中文名"的子字段（见 `services.marc_fields.all_subfields`）——
    光秃秃的码（如 `605$a`）做成选项没有说明，反而误导。
    人工项**永远优先**：同一个 `tag$code` 若两边都有，用我方 label（`905` 那几条尤其如此，
    字典给的名字与我们的语义对不上）。
    """
    from ..services import marc_fields as mf

    have = {f"{t}${c}" for t, c, _ in _CURATED_ISO_TARGETS}
    out = list(_CURATED_ISO_TARGETS)
    for tag, code, name in mf.all_subfields():
        key = f"{tag}${code}"
        if key in have:
            continue
        have.add(key)
        out.append((tag, code, name))
    return out


def target_field_options() -> list[dict]:
    """给界面用的目标字段清单：`[{key:"905$f", tag, code, label}]`。

    = 我方人工命名 ∪ CNMARC 字段字典（2026-10-08 起字典驱动，见 `_target_fields()`）。
    """
    return [{"key": f"{tag}${code}", "tag": tag, "code": code, "label": label}
            for tag, code, label in _target_fields()]


def insert_field_ordered(rec: Record, field) -> None:
    """按 tag 升序把新字段插到合适位置（保持 MARC 字段顺序，不整表重排）。"""
    idx = len(rec.fields)
    for i, f in enumerate(rec.fields):
        if f.tag > field.tag:
            idx = i
            break
    rec.fields.insert(idx, field)


def _mapping_line(left: str, right: str, pretty: str | None, n: int) -> str:
    """一行映射说明（`apply_field_mapping` 的逐条消息与 `format_mapping_totals` 的合计
    **共用这一份渲染**）—— 两边各写一遍格式，迟早一边改了另一边没改。

    - 同 tag 内换子字段码：`909$a → 909$b（1 处）`；
    - 跨 tag 搬运：`909 → 905（$b→$f、$x→$g，3 处）`。
    """
    return (f"{left} → {right}（{pretty}，{n} 处）" if pretty
            else f"{left} → {right}（{n} 处）")


def format_mapping_totals(tally: dict) -> list[str]:
    """把 `apply_field_mapping(tally=…)` 累出来的**整批合计**渲染成界面那行文案。

    键是 `(left, right, pretty)` 三元组，值是"整批搬了多少处"。
    """
    return [_mapping_line(left, right, pretty, n)
            for (left, right, pretty), n in tally.items()]


def apply_field_mapping(rec: Record, mapping: dict | None,
                        *, tally: dict | None = None) -> list[str]:
    """按 `{"源 tag$code": "目标 tag$code"}` 把子字段**搬运**到我们的字段上。

    用户 2026-10-07：「要导入的数据在 909b 里，我们没有 909b 就可以将它指定到 905f 上」；
    随后的真实数据（示例数据）里 `909` 是**每册一条**的重复字段。

    `tally`：可选的**整批累加器**（键 `(left, right, pretty)`，值 = 处数）。
    整批导入**必须**用它来出汇总文案，别去"去重收集逐条消息"——
    那样 50 条记录产生的同一条消息会被并成一条，而里面的数字是**首条记录**的，
    看起来像总数（实测：导 150 册显示"9 处"，真值 450）。见 `format_mapping_totals()`。

    ⚠️ **一条源字段 → 一条目标字段**（不是把所有值堆进同一个字段）。两个理由：
      1. "每册一条"的语义本来就是一行对一行：3 条 909（3 册）应该得到 3 条 905
         —— 我们的 `create_holdings` 也正是这么写的（一张 905 一册）；
      2. `clean_record` 会把**同一字段里连续同码子字段**合并成一条（原意是修
         `606 $a$a恐龙` 这类脏数据），把 3 个条码堆进同一个 905 会被并成
         `10000001；10000002；10000003` 一条 —— 多册信息当场丢失（实测踩到）。

    其它规则：
    - 值落到目标后**从源处移除**；源字段被移空则整字段删掉（用户意图就是"我们没有
      909b"，留下它只会让导出多出一个谁都不认的字段）；
    - 目标字段**优先复用文件里已有的**（避免凭空多出一批），但要挑一个「还没有这些
      子字段码」的 —— 保证同码子字段不会连续出现、也就不会被清洗合并；
    - 目标已有同值子字段不重复追加（**重复执行幂等**）；
    - 语法非法 / 源不存在 → 跳过，不抛异常（一条映射写错不该让整批导入失败）。
    """
    from ..core.marc import Field, Subfield  # noqa: F401  (Subfield 供类型可读性)
    # 先把映射按 (源 tag, 目标 tag) 分组：同一组才存在"第 k 条对第 k 条"的关系
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for src, dst in (mapping or {}).items():
        s, d = parse_tag_code(src), parse_tag_code(dst)
        if not s or not d or s == d:
            continue
        groups.setdefault((s[0], d[0]), []).append((s[1], d[1]))

    applied: list[str] = []
    for (src_tag, dst_tag), pairs in groups.items():
        if src_tag == dst_tag:                       # 同 tag 内换子字段码：就地改名
            n = 0
            for f in rec.fields:
                for src_code, dst_code in pairs:
                    for sf in f.subfields:
                        if sf.code == src_code and f.get(dst_code) is None:
                            sf.code = dst_code
                            n += 1
            if n:
                applied.append(_mapping_line(f"{src_tag}${pairs[0][0]}",
                                             f"{dst_tag}${pairs[0][1]}", None, n))
                if tally is not None:
                    key = (f"{src_tag}${pairs[0][0]}", f"{dst_tag}${pairs[0][1]}", None)
                    tally[key] = tally.get(key, 0) + n
            continue

        src_fields = [f for f in rec.fields if f.tag == src_tag]
        targets = [f for f in rec.fields if f.tag == dst_tag]
        moved = 0
        for sf_src in src_fields:
            payload: dict[str, list[str]] = {}
            for src_code, dst_code in pairs:
                for sf in sf_src.subfields:
                    if sf.code == src_code and (sf.value or "").strip():
                        payload.setdefault(dst_code, []).append(sf.value)
            if not payload:
                continue
            # 摘掉源子字段（源字段被摘空就整字段删掉）
            for src_code, _dst in pairs:
                sf_src.subfields = [s for s in sf_src.subfields if s.code != src_code]
            # 选目标：优先复用已有的、且这些子字段码都还没有的那一条
            target = next((c for c in targets
                           if all(c.get(code) is None for code in payload)), None)
            if target is None:
                target = Field(dst_tag, " ", " ")
                insert_field_ordered(rec, target)
                targets.append(target)
            for code, vals in payload.items():
                have = {s.value for s in target.subfields if s.code == code}
                for v in vals:
                    if v not in have:
                        target.add(code, v)
                        have.add(v)
                        moved += 1
            if not sf_src.subfields and not sf_src.data and sf_src in rec.fields:
                rec.fields.remove(sf_src)
        if moved:
            pretty = "、".join(f"${a}→${b}" for a, b in pairs)
            applied.append(_mapping_line(src_tag, dst_tag, pretty, moved))
            if tally is not None:
                key = (src_tag, dst_tag, pretty)
                tally[key] = tally.get(key, 0) + moved
    return applied


def apply_export_mapping(rec: Record, mapping: dict | None,
                         const: dict | None = None) -> list[str]:
    """导出时按**命名预设**把我们的字段**补写**一份到下游厂家的字段上。

    ⚠️ **与 `apply_field_mapping()` 语义相反**，两份映射不可混用：

    | | 导入（`apply_field_mapping`） | 导出（本函数） |
    |---|---|---|
    | 动作 | **搬运**：值移过去、源处删掉 | **补写**：源字段**一律保留**，只多写一份 |
    | 为什么 | 用户意图是"我们没有 909b"，留着只会让导出多出没人认的字段 | 见下 |

    导出必须"补写"而不是"搬运"，两个理由：

    1. **导出件首先是标准 CNMARC**：`010$d` 定价、`200$a` 题名这些标准字段不能被搬走，
       多出来的 `909` 只是给下游 ILS 认的"兼容副本"，它不认识也无害；
    2. **搬运会删源，预设写错就永久丢数据**：把 `010$d` 搬到 `909$c` 之后，导出件里
       定价就没了 —— 导出的东西是交给别人的，**宁多勿缺**（错了顶多被忽略）。

    ## ★ 分组规则（2026-10-09 重写：从"每个映射各建一条"改成"每条源字段建一条"）

    老实现**每个映射项各建一条目标字段**：一条两册的记录映射
    `905$b→909$a`、`905$s→909$x`、`905$c→909$J` 会产出 **6 条 909**（正确是 **2** 条）——
    下游按 909 条数数册数，会把 2 册读成 6 册。新规则：

    1. 按**目标 tag** 分组；组内再按**源 tag** 分。
    2. **驱动源 tag** = 组里**字段实例最多**的源 tag（如 `905` 每册一条）。
       对驱动 tag 的**每个实例**建**一条**目标字段。
    3. 其余源 tag：**只有 1 个实例**的 → **广播**进每一条目标字段（如 `010$d` 定价
       要出现在每册的 909 里）；实例数与驱动**相同**的 → **按位对齐**（第 i 个配第 i 个）；
       其余（跨 tag 且数量对不上）→ **不猜，跳过**。
       ⚠️ 按位对齐只对**同 tag** 才有意义（跨 tag 无从知道"第几册"），故不猜。
    4. `const`（`{目标tag$code: 固定值}`）广播进该 tag 的每一条目标字段；
       若该 tag **一个源都没有**（如 `982`）→ **新建一条**只装常量的字段。

    映射值可以是**字符串或列表**（一个源可以补写到多个目标，如条码同时进
    `909$a`/`909$b`/`985$a`/`985$h`）。**子字段顺序 = 映射里声明的顺序**
    （这样能照着目标系统的习惯排，如书商的 `a b c d t x e J`）。

    语法非法 / 源不存在 → 跳过，不抛（一条预设写错不该让整批导出失败）。
    """
    from ..core.marc import Field

    def _by_target(mp: dict) -> dict[str, list[tuple[str, str, str]]]:
        """`{目标tag: [(源tag, 源code, 目标code), …]}`（保序、去非法项）。"""
        out: dict[str, list[tuple[str, str, str]]] = {}
        for src, dsts in (mp or {}).items():
            s = parse_tag_code(src)
            if not s:
                continue
            for dst in (dsts if isinstance(dsts, (list, tuple)) else [dsts]):
                if not isinstance(dst, str):
                    continue
                d = parse_tag_code(dst)
                if not d or src == dst:
                    continue
                out.setdefault(d[0], []).append((s[0], s[1], d[1]))
        return out

    def _fields(tag: str) -> list:
        return [f for f in rec.fields if f.tag == tag]

    groups = _by_target(mapping or {})
    const_by_tag: dict[str, list[tuple[str, str]]] = {}
    for k, v in (const or {}).items():
        c = parse_tag_code(k)
        if c and str(v or "").strip():
            const_by_tag.setdefault(c[0], []).append((c[1], str(v)))

    applied: list[str] = []
    for dst_tag in sorted(set(groups) | set(const_by_tag)):
        pairs_by_src: dict[str, list[tuple[str, str]]] = {}
        for src_tag, src_code, dst_code in groups.get(dst_tag, []):
            pairs_by_src.setdefault(src_tag, []).append((src_code, dst_code))
        src_fields = {t: _fields(t) for t in pairs_by_src}
        csubs = const_by_tag.get(dst_tag, [])
        made = 0

        if pairs_by_src:
            # 驱动 = 实例最多的源 tag（并列时取声明顺序靠前的，dict 保序）
            driver = max(pairs_by_src, key=lambda t: len(src_fields[t]))
            n = len(src_fields[driver])
            for i in range(n):
                f = Field(dst_tag, " ", " ")
                for src_code, dst_code in pairs_by_src[driver]:
                    for v in src_fields[driver][i].all(src_code):
                        if (v or "").strip():
                            f.add(dst_code, v)
                for t, pairs in pairs_by_src.items():
                    if t == driver:
                        continue
                    insts = src_fields[t]
                    if len(insts) == 1:
                        src_i = insts[0]
                    elif len(insts) == n:
                        src_i = insts[i]
                    else:
                        continue                      # 跨 tag 且数量对不上 → 不猜
                    for src_code, dst_code in pairs:
                        for v in src_i.all(src_code):
                            if (v or "").strip():
                                f.add(dst_code, v)
                for code, v in csubs:
                    f.add(code, v)
                if f.subfields:
                    insert_field_ordered(rec, f)
                    made += 1
        elif csubs:
            # 该目标 tag 没有任何源（如 982）→ 建一条只装常量的
            f = Field(dst_tag, " ", " ")
            for code, v in csubs:
                f.add(code, v)
            insert_field_ordered(rec, f)
            made = 1

        if made:
            applied.append(f"{dst_tag}（{made} 条）")
    return applied


def write_iso(path: str | Path, records: list[Record], encoding: str = "gbk") -> int:
    """批量写出。默认 GBK（国内下游系统兼容性最好），可指定 utf-8。返回条数。

    单条超限（字段>9999B / 记录>99999B）时跳过该条并抛出收集到的错误清单。
    """
    out, skipped = bytearray(), []
    for i, rec in enumerate(records):
        try:
            out += record_to_iso(rec, encoding=encoding)
        except MarcWriteError as e:
            skipped.append({"index": i, "error": str(e)})
    Path(path).write_bytes(bytes(out))
    if skipped:
        raise MarcWriteError(f"{len(skipped)} 条记录写出失败：{skipped[:3]}")
    return len(records)
