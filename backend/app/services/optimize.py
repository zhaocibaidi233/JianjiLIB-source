# -*- coding: utf-8 -*-
"""规范中心：**可配置**的规则层规范化（纯规则，不依赖大模型）。

背景
----
原「自动规范」= `ai/fields.py::autofill_fields`（固定 6 步）+ `/ai/optimize-local`
里先跑的 `core/clean.py::clean_record`，全局只有一个开关 `optimize_mode`（auto/manual）。
用户 2026-10-07 反馈：希望能**逐条选择规范哪些条目**，并把写死的值（尺寸 20cm、
装订平装）变成可配；同时补上「ISBN 10 位转 13 位」「装订为空默认平装」。

设计（与 `services/dictionaries.py` / `services/settings.py` 同一套哲学）
--------------------------------------------------------------------
- `OPTIMIZE_RULES` 是**唯一注册表**（id / 通俗名 / 说明 / 参数形态 / 执行函数）。
  前端从 `GET /settings/optimize` 拿清单，**界面不硬编码任何一条** —— 加一条规则只改这里。
- 存储：settings 键 `optimize_rules`，值 `{id: {"enabled": bool, "value": <param>}}`，
  **只存用户改过的**；没改 → 用内置默认（`default_rules()`）。不建表、不需要迁移。
- 读侧容错：未知 id 丢弃、类型不符回退默认、绝不抛 —— 设置读不出来不该让编目失败。
- 每条规则是 `fn(rec, value) -> list[str]`，返回「做了什么」的可读明细，
  与 `/ai/optimize-local` 的 `applied` 契约一致（前端直接把整列表显示出来）。
- **规则顺序即执行顺序**：清洗类（控制字符/去重/定价/出版年/拼音）在前，
  补全类（题名/语种/出版地/尺寸/装订/ISBN）在后，ISBD 收尾。

⚠️ 规范只改**当前编辑内容**、不落库（保存时才写）：见实现说明。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from ..ai import fields as af
from ..core import clean as cl
from ..core.isbd import unify_isbd
from ..core.marc import Record

# settings 表里的键（模式仍走老的 `optimize_mode`，本键只存**逐条规则**的改动）
OPTIMIZE_RULES_KEY = "optimize_rules"

# 参数长度上限（防脏值灌库）
_PARAM_MAXLEN = 24


# ---------- 各条规则的执行函数（每条返回"做了什么"的可读明细） ----------

def _run_ctrl_chars(rec: Record, value) -> list[str]:
    """剥离控制字符（NUL 等看不见的字符，多见于复制粘贴）。"""
    n = 0
    for f in rec.fields:
        for sf in f.subfields:
            cleaned = cl.strip_ctrl(sf.value)
            if cleaned != sf.value:
                sf.value = cleaned
                n += 1
        if f.data and cl.CTRL_RE.search(f.data):
            f.data = cl.strip_ctrl(f.data)
            n += 1
    return [f"已剥离控制字符（{n} 处）"] if n else []


def _run_dup_subfields(rec: Record, value) -> list[str]:
    """合并连续重复子字段（606 $a$a恐龙 → $a恐龙）。"""
    n = sum(1 for f in rec.fields if cl.merge_duplicate_subfields(f))
    return [f"已合并重复子字段（{n} 处）"] if n else []


def _run_price(rec: Record, value) -> list[str]:
    """规整 010$d 定价：去重重复货币码、补两位小数。"""
    n = 0
    for f in rec.get_fields("010"):
        for sf in f.subfields:
            if sf.code == "d" and sf.value:
                fixed = cl.normalize_price(sf.value)
                if fixed != sf.value:
                    sf.value = fixed
                    n += 1
    return [f"定价货币码已规整（{n} 处）"] if n else []


def _run_pub_year(rec: Record, value) -> list[str]:
    """出版年规整为 4 位（2007.9 → 2007）。"""
    return af.normalize_pub_year(rec)


def _run_pinyin(rec: Record, value) -> list[str]:
    """把非标准的 `$A` 拼音归位到**标准**子字段 `$9`（GB/T 33286—2016）。"""
    n = cl.migrate_pinyin_to_9(rec)
    return [f"拼音已由 $A 归位到 $9（{n} 处）"] if n else []


def _run_isbn13(rec: Record, value) -> list[str]:
    """ISBN 10 位转 13 位。"""
    return af.normalize_isbn_13(rec)


def _run_split_title(rec: Record, value) -> list[str]:
    """正/副题名拆分。"""
    return af.fill_split_title(rec)


def _run_lang_default(rec: Record, value) -> list[str]:
    """语种缺省。"""
    return af.fill_language(rec, value or "chi")


def _run_pub_place(rec: Record, value) -> list[str]:
    """出版地对照补全。"""
    return af.fill_pub_place(rec)


def _run_size_clean(rec: Record, value) -> list[str]:
    """清除尺寸位的错误值（页数写进了尺寸位）。"""
    return af.clean_size_value(rec)


def _run_size_default(rec: Record, value) -> list[str]:
    """尺寸缺省。"""
    return af.fill_default_size(rec, value or "20cm")


def _run_binding_default(rec: Record, value) -> list[str]:
    """装订缺省。"""
    return af.fill_default_binding(rec, value or "平装")


def _run_f100(rec: Record, value) -> list[str]:
    """100 通用处理数据（**必备**，36 位定长）—— 缺则生成。"""
    return af.fill_100(rec)


def _run_f105(rec: Record, value) -> list[str]:
    """105 编码数据:图书（13 位定长）—— 缺则生成。"""
    return af.fill_105(rec)


def _run_f106(rec: Record, value) -> list[str]:
    """106 载体形态（`$a r`）—— 缺则补。"""
    return af.fill_106(rec)


def _run_f102(rec: Record, value) -> list[str]:
    """102 出版国别（`$a CN`）—— 缺则补。"""
    return af.fill_102(rec)


def _run_isbd(rec: Record, value) -> list[str]:
    """200 字段 ISBD 标点规范（题名/责任者之间的「 : 」「 / 」）。

    照旧修数据，但**不回报**这一项：简编表单把题名/责任者拆成多栏，展示时
    会剥掉 ISBD 尾标点（见 `前端实现::populateSimple`），
    于是送进来的载荷天然不带尾标点 —— 这一步对任何简编载荷都必然"有改动"，
    回报出去就是每次打开记录都弹一条「已按规范补齐」的假修复提示。

    真正的落库由 `core/clean.py::clean_record()` 在保存/导入时保证，不依赖本条。
    """
    unify_isbd(rec)
    return []


# ---------- 规则注册表（**唯一真源**，顺序即执行顺序） ----------

@dataclass(frozen=True)
class OptimizeRule:
    id: str
    name: str                       # 通俗名（界面直接展示）
    desc: str                       # 一句话说明（界面副标题）
    fn: Callable[[Record, object], list[str]]
    default_enabled: bool = True
    param_label: str | None = None  # 非空 → 该条带一个可配参数
    param_default: str | None = None
    param_options: tuple[str, ...] = ()   # 非空 → 前端给下拉（仍允许手填）


OPTIMIZE_RULES: tuple[OptimizeRule, ...] = (
    OptimizeRule("ctrl_chars", "剥离控制字符",
                 "去掉数据里看不见的控制字符（复制粘贴常见）。", _run_ctrl_chars),
    OptimizeRule("dup_subfields", "合并重复子字段",
                 "同一字段里连着出现两个相同子字段时合并（如 606 的 $a$a）。", _run_dup_subfields),
    OptimizeRule("price", "规整定价",
                 "把 010$d 定价统一成「货币码 + 两位小数」，去重重复货币码。", _run_price),
    OptimizeRule("pub_year", "出版年规整为 4 位",
                 "把「2020.5」「2020年」这类写法规整成 2020。", _run_pub_year),
    OptimizeRule("pinyin", "拼音归位到 $9",
                 "题名/责任者拼音放在 $A 的，归位到标准子字段 $9（GB/T 33286—2016）。", _run_pinyin),
    OptimizeRule("isbn13", "ISBN 10 位转 13 位",
                 "把 10 位 ISBN 统一转换成 13 位，便于查重与套录命中。", _run_isbn13),
    OptimizeRule("split_title", "正副题名拆分",
                 "题名里带「：」时，把冒号后的部分拆到副题名 $e。", _run_split_title),
    OptimizeRule("lang_default", "语种缺省",
                 "没有语种时自动补上（中文书按此默认；纯英文题名仍按 eng）。", _run_lang_default,
                 param_label="默认语种", param_default="chi",
                 param_options=("chi", "eng", "jpn", "kor", "fre", "ger", "rus")),
    OptimizeRule("pub_place", "出版地对照补全",
                 "有出版者没出版地时，按本馆已有书目归纳出的出版者—出版地对应关系补出。",
                 _run_pub_place),
    OptimizeRule("size_clean", "清除尺寸位的错误值",
                 "尺寸位里填了页数（如「154页」）时清除，避免脏数据。", _run_size_clean),
    OptimizeRule("size_default", "尺寸缺省",
                 "尺寸为空时补一个默认值。", _run_size_default,
                 param_label="默认尺寸", param_default="20cm"),
    OptimizeRule("binding_default", "装订缺省",
                 "有 ISBN 但没写装订时，补一个默认装订方式。", _run_binding_default,
                 param_label="默认装订", param_default="平装",
                 param_options=("平装", "精装", "线装", "活页")),
    OptimizeRule("f102", "补出版国别 102",
                 "缺 102 时补 `$a CN`（出版国代码）。", _run_f102),
    OptimizeRule("f106", "补载体形态 106",
                 "缺 106 时补 `$a r`（印刷型文字资料）。", _run_f106),
    OptimizeRule("f105", "补编码数据 105",
                 "缺 105 时生成 13 位默认值（图表/内容类型/文学体裁等代码位）。", _run_f105),
    OptimizeRule("f100", "补通用处理数据 100",
                 "缺 100 时生成 36 位定长值（入档日期 / 出版年 / 编目语种 / 字符集…）。"
                 "**100 是 CNMARC 必备字段**，导出联编前应确保有。", _run_f100),
    OptimizeRule("isbd", "ISBD 标点规范",
                 "题名与责任者之间补上规范的「 : 」「 / 」标点。", _run_isbd),
)

RULE_BY_ID: dict[str, OptimizeRule] = {r.id: r for r in OPTIMIZE_RULES}


# ---------- 配置的默认值 / 归一 / 读写 ----------

def default_rules() -> dict[str, dict]:
    """内置默认：每条规则按其 `default_enabled` 与 `param_default` 组装（每次返回新对象）。"""
    return {
        r.id: {"enabled": r.default_enabled,
               "value": r.param_default if r.param_label else None}
        for r in OPTIMIZE_RULES
    }


def _coerce_rule_value(rule: OptimizeRule, raw) -> str | None:
    """参数归一：无参数规则恒为 None；有参数规则取字符串并限长，空/非法 → 用默认。"""
    if rule.param_label is None:
        return None
    if not isinstance(raw, str):
        return rule.param_default
    s = raw.strip()[:_PARAM_MAXLEN]
    return s or rule.param_default


def normalize_rules(raw) -> dict[str, dict]:
    """把**存库的原始值**归一成完整配置：未知 id 丢弃、脏值回退默认。绝不抛。

    返回形如 `{id: {"enabled": bool, "value": str|None}}`，覆盖**全部**注册规则
    （未出现在 raw 里的用默认），这样调用方永远拿到完整可渲染的结构。
    """
    out = default_rules()
    if isinstance(raw, dict):
        for rid, cfg in raw.items():
            rule = RULE_BY_ID.get(rid)
            if rule is None or not isinstance(cfg, dict):
                continue
            enabled = cfg.get("enabled", rule.default_enabled)
            out[rid] = {
                "enabled": enabled if isinstance(enabled, bool) else rule.default_enabled,
                "value": _coerce_rule_value(rule, cfg.get("value")),
            }
    return out


def optimize_rules_value(db: Session) -> dict[str, dict]:
    """读库里的规则配置（行不存在 / 脏值 → 回退默认）。"""
    try:
        from ..models import Setting
        row = db.get(Setting, OPTIMIZE_RULES_KEY)
        return normalize_rules(row.value if row is not None else None)
    except Exception:
        return default_rules()


def save_rules(db: Session, patch: dict) -> dict[str, dict]:
    """局部更新规则配置：把 `patch`（`{id: {"enabled"?, "value"?}}`）并进现值后落库。

    未知 id → `ValueError`（交给路由转 400）。写入前经 `normalize_rules` 归一。
    """
    for rid in patch:
        if rid not in RULE_BY_ID:
            raise ValueError(f"未知的规范条目：{rid}")
    from ..models import Setting
    current = optimize_rules_value(db)
    for rid, cfg in patch.items():
        if isinstance(cfg, dict):
            rule = RULE_BY_ID[rid]
            if "enabled" in cfg and isinstance(cfg["enabled"], bool):
                current[rid]["enabled"] = cfg["enabled"]
            if rule.param_label is not None and "value" in cfg:
                current[rid]["value"] = _coerce_rule_value(rule, cfg["value"])
    row = db.get(Setting, OPTIMIZE_RULES_KEY)
    if row is None:
        db.add(Setting(key=OPTIMIZE_RULES_KEY, value=current))
    else:
        row.value = current
    db.commit()
    return current


def catalog(rules: dict[str, dict]) -> list[dict]:
    """把配置转成前端要的**有序清单**（含每条的名称/说明/参数定义）。"""
    return [
        {
            "id": r.id, "name": r.name, "desc": r.desc,
            "enabled": rules[r.id]["enabled"], "value": rules[r.id]["value"],
            "param_label": r.param_label, "param_options": list(r.param_options),
        }
        for r in OPTIMIZE_RULES
    ]


# ---------- 执行 ----------

def apply_optimize(rec: Record, rules: dict[str, dict]) -> list[str]:
    """按 `rules` 顺序执行**已启用**的条目，返回"做了什么"的可读明细。

    `rules` 若为 None 或空 → 用内置默认（全开）。调用方（`/ai/optimize-local`）
    传的是 `optimize_rules_value(db)` 的结果。
    """
    if not rules:
        rules = default_rules()
    applied: list[str] = []
    for rule in OPTIMIZE_RULES:
        cfg = rules.get(rule.id)
        if not cfg or not cfg.get("enabled", rule.default_enabled):
            continue
        applied.extend(rule.fn(rec, cfg.get("value")))
    return applied
