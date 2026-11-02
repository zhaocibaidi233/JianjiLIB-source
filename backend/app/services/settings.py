# -*- coding: utf-8 -*-
"""全局设置读取（`settings` 表的 key-value，见 `models.Setting`）。

与「条码位数」同款的读设置写法（对照 `services.batch._barcode_digits`）：
行不存在 / 值形态异常 / 数值非法一律回退内置默认，**绝不抛异常**——设置读不出来
不该让编目流程失败。
"""
from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from ..core.clc import DEFAULT_CLASS_NO_RULE, ClassNoRule, rule_from_raw

# 分类号截取的全局设置键（不分图书库：编目规则是全馆统一的作业参数）。
CLASS_NO_KEY = "class_no"


def class_no_rule(db: Session) -> ClassNoRule:
    """分类号截取规则（全局设置 `class_no`，默认 `DEFAULT_CLASS_NO_RULE`）。

    2026-10-08 起这条设置从一个 `digits` 变成三键，再变成四键
    （加 `dash_digits`，见），再加一键 `base_digits`（字母后数字位数阈值，
    见）——**为避免两套真源**，读取口只有一个：
    本函数返回完整的 `ClassNoRule`，调用方（套录 / 导入）拿它直接截短。

    容错口径：行不存在 / `value` 不是 dict → 出厂规则；单项脏值 → **该项**回退默认
    （`digits` / `dash_digits` / `base_digits` 可解析但越界则 clamp）。归一逻辑在
    `core.clc.rule_from_raw()`（纯函数、两侧共用一个实现，便于单测）。
    """
    try:
        from ..models import Setting
        row = db.get(Setting, CLASS_NO_KEY)
        return rule_from_raw(row.value if row is not None else None)
    except Exception:
        return DEFAULT_CLASS_NO_RULE


# ---------------- 书标格式 ----------------
#
# 键 `label_format` 是**全局设置**（不分图书库）：书标打印格式是全馆统一的作业参数
# —— 同一批书标不论哪个库都该是一样的大小与版式，按库区分只会带来无意义的分叉。
#
# 默认值依据历史数据的书标格式：默认 = A4 纵向、5 列 × 10 行
# 打满一页。**单位与历史数据一致**：边长 / 间距 / 边距用 **0.01mm 整数**，纸张用**厘米**。
#
# 前端有一份**逐项对称**的实现：
#   `DEFAULT_LABEL_FORMAT` 与 `LABEL_FORMAT_LIMITS` 必须与下面两份常量保持一致
#   （前端单测 `tests/labelFormat.test.ts` 与后端 `tests/test_class_no_truncate.py`
#    各自钉住一侧，改一边忘另一边会被测试挡住）。

LABEL_FORMAT_KEY = "label_format"

# 枚举白名单（进 CSS / 参与判定的字符串一律**白名单**，防注入；不在名单内 → 回退默认）。
LABEL_PAPER_OPTIONS = ("A4", "custom")
LABEL_DIRECTION_OPTIONS = ("portrait", "landscape")
LABEL_ALIGN_OPTIONS = ("left", "center", "right")
LABEL_FONT_OPTIONS = ("宋体", "黑体", "微软雅黑", "等宽")

# ---------------- 书标样式（栏模型；） ----------------
#
# 「书标样式」= 若干**栏（行）**，每栏内摆若干**项（item）**。这就是书标样式设置的主体。
# 多套命名样式存在 `styles` 里，`style_id` 指向当前使用的那套。
#
# item kind：field（字段）/ barcode（条码图形）/ text（固定文字，如分隔符）。
LABEL_ITEM_KINDS = ("field", "barcode", "text")
# kind == "field" 时 key 的白名单（与前端 FIELD_KEYS 逐项一致）。
LABEL_FIELD_KEYS = (
    "callNo", "classNo", "bookNo", "volume", "shelfNo", "copyNo", "title", "barcodeText",
)
# 上限（与前端 MAX_STYLES / MAX_STYLE_ROWS / MAX_ROW_ITEMS 一致）。
LABEL_MAX_STYLES = 20
LABEL_MAX_STYLE_ROWS = 12
LABEL_MAX_ROW_ITEMS = 12
LABEL_STYLE_ID_MAXLEN = 32
LABEL_STYLE_NAME_MAXLEN = 32
LABEL_TEXT_MAXLEN = 40
# item 字号范围（pt）。
LABEL_ITEM_PT_MIN = 6
LABEL_ITEM_PT_MAX = 24
# 栏间距范围（0.01mm）。
LABEL_ROW_GAP_MIN = 0
LABEL_ROW_GAP_MAX = 2000

# 内置默认样式：**三栏式**（索书号 / 排架号 / 条码数字），与现网已保存的版式一致。
# 换默认样式时前后端必须同步修改 DEFAULT_FORMAT。
DEFAULT_LABEL_STYLE = {
    "id": "s-default",
    "name": "三栏式",
    "row_gap": 0,
    "rows": [
        {"items": [{"kind": "field", "key": "callNo", "pt": 14, "bold": False}]},
        {"items": [{"kind": "field", "key": "shelfNo", "pt": 14, "bold": False}]},
        {"items": [{"kind": "field", "key": "barcodeText", "pt": 14, "bold": False}]},
    ],
}

# 与前端 DEFAULT_LABEL_FORMAT 必须逐项一致。
DEFAULT_LABEL_FORMAT = {
    "paper": "A4",
    "paper_w": 21.0, "paper_h": 29.7,        # 厘米（A4 = 21.0 × 29.7）
    "direction": "portrait",
    "margin_top": 900, "margin_left": 600,   # 0.01mm
    "label_w": 3800, "label_h": 2600,        # 0.01mm（= 38 × 26 mm，A4 5×10 可排）
    "gap_h": 200, "gap_v": 200,              # 0.01mm（= 2 mm）
    "rows": 10, "cols": 5,
    "align": "center",
    "pad_book_no": True, "pad_width": 4,
    "font_family": "宋体",
    "style_id": "s-default",
    "styles": [DEFAULT_LABEL_STYLE],
}

# 各数值字段的合法范围（PUT 校验 + GET 越界 clamp 共用）。与前端 LABEL_FORMAT_LIMITS 一致。
LABEL_FORMAT_LIMITS = {
    "margin_top": (0, 5000),
    "margin_left": (0, 5000),
    "label_w": (1000, 20000),
    "label_h": (1000, 20000),
    "gap_h": (0, 2000),
    "gap_v": (0, 2000),
    "rows": (1, 50),
    "cols": (1, 50),
    "pad_width": (1, 10),
    "paper_w": (5, 100),
    "paper_h": (5, 100),
}

# PUT 校验时的字段分类（**唯一真源**）：整数 / 浮点 / 布尔 / 枚举。
LABEL_INT_FIELDS = (
    "margin_top", "margin_left", "label_w", "label_h", "gap_h", "gap_v",
    "rows", "cols", "pad_width",
)
LABEL_FLOAT_FIELDS = ("paper_w", "paper_h")
LABEL_BOOL_FIELDS = ("pad_book_no",)
LABEL_ENUM_FIELDS = {
    "paper": LABEL_PAPER_OPTIONS,
    "direction": LABEL_DIRECTION_OPTIONS,
    "align": LABEL_ALIGN_OPTIONS,
    "font_family": LABEL_FONT_OPTIONS,
}


# ---------------- 导出格式预设（历史设计记录） ----------------
#
# 用途：把「我方字段 → 下游厂家字段」的常用映射**存成命名预设**，导出 ISO 时一键套用。
# 对标参考系统的「导出 MARC（默认 909 格式为常用格式，985 为特殊格式，905 也为常用格式，
# 兼容大部份厂家软件）」—— 它靠内置几套写死的格式，我们把它做成**可命名、可改**的预设。
#
# ⚠️ 与导入方向的映射**语义相反**，别混用两份：
#   导入是「搬运」（值移到目标、源字段移空就删，见 `io/iso.py::apply_field_mapping`）；
#   导出是「补写」（源字段一律保留，只多写一份兼容副本，见 `apply_export_mapping`）。
EXPORT_PRESETS_KEY = "export_presets"
EXPORT_MAX_PRESETS = 20
EXPORT_MAX_ENTRIES = 60
EXPORT_PRESET_ID_MAXLEN = 32
EXPORT_PRESET_NAME_MAXLEN = 32

# 出厂三套。`p-905` 是**空映射 = 原样导出标准 CNMARC**，也是默认。
# `p-909` / `p-985` 的映射方向取自 `io/iso.py::ISO_COMPAT_FIELDS`（那是从真实书商数据
# 反推出来的：`909$a` 是条码号、`909$x` 是索书号、`909$J` 是排架号）。
# ⚠️ 它们是**起点不是标准** —— 下游厂家各家不同，界面上可改名、改映射、加新套。
# ⚠️ **2026-10-09**：左侧源随我方 905 布局改为标准子字段 —— 条码 `905$h`→`905$b`、
#    索书号 `905$b`→`905$s`（`905$c` 排架号不变）。改这里等于改**出厂预设**，
#    已存进 `settings['export_presets']` 的旧预设**不会自动更新**（见 `_load_presets`），
#    迁移时一并处理。
DEFAULT_EXPORT_PRESETS: dict = {
    "presets": [
        {"id": "p-905", "name": "905（标准，原样）", "mapping": {}, "const": {}},
        {"id": "p-909", "name": "909（常用）",
         "mapping": {"905$b": ["909$a"], "905$s": ["909$x"], "905$c": ["909$J"]},
         "const": {}},
        {"id": "p-985", "name": "985（特殊）",
         "mapping": {"905$b": ["985$a"]}, "const": {}},
        # 常见书商导出数据中，每册会带一条 909/985；以下映射覆盖常用字段。
        # 导出路径只能看到 MARC 字段，数据库列（入库时间、经手人、馆名）无法映射，
        # 因此 909$t、905$a、985$g 使用常量占位。
        #    要填真值得先把这些**注入记录**，属独立改动。
        {"id": "p-xhs", "name": "书商·某馆配商（每册一条 909/985）",
         "mapping": {
             # 条码：书商把它同时放进 909 与 985 的四个位置（一源多目标）
             "905$b": ["909$a", "909$b", "985$a", "985$h"],
             "905$a": ["909$d", "985$b"],       # 馆藏地点（书商 985$b 是书库/藏书类别）
             "905$c": ["909$J"],                # 排架号
             "905$s": ["909$x"],                # 索书号
             "010$d": ["909$c", "985$d"],       # 定价（单值源 → 广播进每册）
         },
         "const": {
             "909$e": "中文图书",               # 书商实测：该字段恒为此值
             "982$a": "学生用书",               # 书商实测：整库恒为此值（我们没有这个概念，填死）
             "985$g": "图书馆",
             "985$x": "学生用书",
         }},
    ],
    "default_id": "p-905",
}


def _normalize_export_mapping(raw) -> dict:
    """归一一份映射：键必须是合法 `tag$code`，值可以是 `tag$code` **或它的列表**。

    **值允许列表**（2026-10-09 加）：一个源要补写到多个目标（如条码同时进
    `909$a`/`909$b`/`985$a`/`985$h`）。老格式（字符串）照旧兼容，内部统一成列表。

    非法项**只丢这一项**（不整份回退）——与 `_normalize_item()` 同一哲学：
    用户手改的映射里错一条，不该把另外九条一起清掉。
    """
    from ..io.iso import parse_tag_code
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    n = 0
    for k, v in raw.items():
        if n >= EXPORT_MAX_ENTRIES:
            break
        if not isinstance(k, str) or not parse_tag_code(k):
            continue
        dsts = [d for d in (v if isinstance(v, (list, tuple)) else [v])
                if isinstance(d, str) and d != k and parse_tag_code(d)]
        if not dsts:
            continue
        # 同一个目标在一项里写两遍没意义，去重保序
        seen: list[str] = []
        for d in dsts:
            if d not in seen:
                seen.append(d)
        out[k] = seen
        n += len(seen)
    return out


def _normalize_export_const(raw) -> dict:
    """归一 `const`（固定值）段：`{目标tag$code: 文本}`。

    给"目标系统有、我们没有"的子字段填死值（如 `982$a` = 学生用书）。
    值一律转字符串、限长；键非法或值为空 → 丢该项。
    """
    from ..io.iso import parse_tag_code
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in list(raw.items())[:EXPORT_MAX_ENTRIES]:
        if not isinstance(k, str) or not parse_tag_code(k):
            continue
        s = v if isinstance(v, str) else ("" if v is None else str(v))
        s = s.strip()[:EXPORT_PRESET_NAME_MAXLEN]
        if s:
            out[k] = s
    return out


def _normalize_export_preset(raw):
    """归一**一套**预设；结构坏（id 非法）→ None（丢弃该套）。"""
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id")
    if not isinstance(pid, str) or not (1 <= len(pid) <= EXPORT_PRESET_ID_MAXLEN):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= EXPORT_PRESET_NAME_MAXLEN):
        name = pid                                  # id 已合法 → 拿它兜底，保证 name 一定有
    return {"id": pid, "name": name,
            "mapping": _normalize_export_mapping(raw.get("mapping")),
            "const": _normalize_export_const(raw.get("const"))}


def export_presets_value(db: Session) -> dict:
    """读导出格式预设；行不存在 / 脏值 / 一套都不剩 → 出厂三套。**绝不抛**。

    与 `label_format_value()` 同一套容错哲学（设置读不出来不该让导出失败）。
    """
    try:
        from ..models import Setting
        row = db.get(Setting, EXPORT_PRESETS_KEY)
        value = row.value if row is not None else None
        if not isinstance(value, dict):
            return copy.deepcopy(DEFAULT_EXPORT_PRESETS)
        presets: list[dict] = []
        seen: set[str] = set()
        raw_list = value.get("presets")
        if isinstance(raw_list, list):
            for p in raw_list[:EXPORT_MAX_PRESETS]:
                norm = _normalize_export_preset(p)
                if norm is None or norm["id"] in seen:
                    continue
                seen.add(norm["id"])
                presets.append(norm)
        if not presets:
            return copy.deepcopy(DEFAULT_EXPORT_PRESETS)
        did = value.get("default_id")
        return {"presets": presets,
                "default_id": did if (isinstance(did, str) and did in seen)
                else presets[0]["id"]}
    except Exception:
        return copy.deepcopy(DEFAULT_EXPORT_PRESETS)


def export_preset_parts(db: Session, preset_id: str | None) -> tuple[dict, dict]:
    """取某个预设的 `(mapping, const)`。

    ⚠️ **未给 `preset_id` → 返回两个空字典（不做任何变换）**，不是"套用默认预设" ——
    否则"设了个默认预设"会**悄悄改掉所有导出方**（含 MCP 的 `export_iso` 工具），
    那是静默的行为变更。要套预设就显式指名。
    """
    if not preset_id:
        return {}, {}
    for p in export_presets_value(db)["presets"]:
        if p["id"] == preset_id:
            return dict(p.get("mapping") or {}), dict(p.get("const") or {})
    return {}, {}


def _coerce_bool(raw, default: bool) -> bool:
    """只认真正的布尔值：`bool` 是 `int` 子类，`isinstance(x, int)` 会把 `True` 当数字放过。"""
    return raw if isinstance(raw, bool) else default


def _coerce_int(raw, default: int, lo: int, hi: int) -> int:
    """转 int 并 clamp；`bool` / 非数字 / 无法解析 → 默认值。"""
    if isinstance(raw, bool):
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def _coerce_float(raw, default: float, lo: float, hi: float, decimals: int = 1) -> float:
    """转 float 并 clamp；`bool` / 非数字 / 无法解析 → 默认值。小数位按 decimals 归一。"""
    if isinstance(raw, bool):
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    v = max(lo, min(v, hi))
    return round(v, decimals)


def _coerce_enum(raw, default: str, options: tuple) -> str:
    """枚举项：**必须在白名单内**，否则回退默认（字符串不能 clamp）。"""
    return raw if raw in options else default


def _coerce_pt(raw):
    """item 字号归一：只认数字（bool 排除），clamp 到 6~24；不是数字 → None（省略该项）。

    整数值保持 int（JSON 更干净），小数值保留 2 位。
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    v = max(float(LABEL_ITEM_PT_MIN), min(float(raw), float(LABEL_ITEM_PT_MAX)))
    return int(v) if float(v).is_integer() else round(v, 2)


def _normalize_item(raw):
    """归一一个 item（脏值宽容）：kind / key 非法 → None（跳过该项）；pt / bold 非法 → 省略。

    不做「丢弃整套」——item 级脏值只丢这一项，栏与其余项保留。
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind == "field":
        key = raw.get("key")
        if key not in LABEL_FIELD_KEYS:
            return None
        item = {"kind": "field", "key": key}
    elif kind == "barcode":
        item = {"kind": "barcode", "key": "barcode"}
    elif kind == "text":
        text = raw.get("text")
        if not isinstance(text, str):
            return None
        item = {"kind": "text", "text": text[:LABEL_TEXT_MAXLEN]}
    else:
        return None
    pt = _coerce_pt(raw.get("pt"))
    if pt is not None:
        item["pt"] = pt
    if isinstance(raw.get("bold"), bool):
        item["bold"] = raw["bold"]
    return item


def _normalize_style(raw):
    """归一**一套**样式；结构坏（id 非法 / rows 非列表或空 / 某栏不是对象）→ None（丢弃该套）。

    与 `label_format_value` 的哲学一致：脏值**丢该套**而不是整份回退，尽量保住用户其它样式。
    """
    if not isinstance(raw, dict):
        return None
    sid = raw.get("id")
    if not isinstance(sid, str) or not (1 <= len(sid) <= LABEL_STYLE_ID_MAXLEN):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= LABEL_STYLE_NAME_MAXLEN):
        name = sid                                   # id 已合法 → 拿它兜底，保证 name 一定有
    row_gap = _coerce_int(raw.get("row_gap"), 0, LABEL_ROW_GAP_MIN, LABEL_ROW_GAP_MAX)
    rows_raw = raw.get("rows")
    if not isinstance(rows_raw, list) or not rows_raw:
        return None
    rows = []
    for row in rows_raw[:LABEL_MAX_STYLE_ROWS]:
        if not isinstance(row, dict):
            return None                              # 栏结构坏 → 丢弃该套
        items_raw = row.get("items")
        if not isinstance(items_raw, list):
            return None
        items = []
        for it in items_raw[:LABEL_MAX_ROW_ITEMS]:
            norm = _normalize_item(it)
            if norm is not None:
                items.append(norm)
        rows.append({"items": items})
    return {"id": sid, "name": name, "row_gap": row_gap, "rows": rows}


def _default_styles() -> list:
    """内置默认样式的**深拷贝**（避免调用方改到模块级常量）。"""
    return copy.deepcopy([DEFAULT_LABEL_STYLE])


def _normalize_styles(raw) -> list:
    """归一整套 `styles`：非列表 / 空 → 用默认样式；逐套归一、坏套丢弃、套内 id 重复丢弃后者。"""
    if not isinstance(raw, list) or not raw:
        return _default_styles()
    styles = []
    seen = set()
    for s in raw[:LABEL_MAX_STYLES]:
        norm = _normalize_style(s)
        if norm is None:
            continue
        if norm["id"] in seen:
            continue
        seen.add(norm["id"])
        styles.append(norm)
    return styles or _default_styles()


def label_format_value(db: Session) -> dict:
    """读书标格式；行不存在 / 非 dict / 单项非法 → 该项回退默认；数值越界 → clamp。**绝不抛**。

    与 `class_no_rule()` 同一套容错哲学：设置读不出来不该让打印失败。

    容错口径分两类：
      - 数值（int/float）：`bool` / 非数字 / 无法解析 → 默认；可解析但越界 → clamp；
      - 枚举（paper/direction/align/font_family）：不在白名单 → 默认；
      - 样式（styles / style_id）：整体非列表 / 空 → 默认样式；**某套结构坏 → 丢弃该套**（不整份回退）；
        `style_id` 不指向任何一套 → 落到 `styles[0].id`。
    """
    try:
        from ..models import Setting
        row = db.get(Setting, LABEL_FORMAT_KEY)
        value = row.value if row is not None else None
        if not isinstance(value, dict):
            return copy.deepcopy(DEFAULT_LABEL_FORMAT)
        D = DEFAULT_LABEL_FORMAT
        L = LABEL_FORMAT_LIMITS
        out = {
            "paper": _coerce_enum(value.get("paper"), D["paper"], LABEL_PAPER_OPTIONS),
            "paper_w": _coerce_float(value.get("paper_w"), D["paper_w"], *L["paper_w"]),
            "paper_h": _coerce_float(value.get("paper_h"), D["paper_h"], *L["paper_h"]),
            "direction": _coerce_enum(value.get("direction"), D["direction"],
                                      LABEL_DIRECTION_OPTIONS),
            "align": _coerce_enum(value.get("align"), D["align"], LABEL_ALIGN_OPTIONS),
            "font_family": _coerce_enum(value.get("font_family"), D["font_family"],
                                        LABEL_FONT_OPTIONS),
        }
        for field in LABEL_INT_FIELDS:
            out[field] = _coerce_int(value.get(field), D[field], *L[field])
        for field in LABEL_FLOAT_FIELDS:
            out[field] = _coerce_float(value.get(field), D[field], *L[field])
        for field in LABEL_BOOL_FIELDS:
            out[field] = _coerce_bool(value.get(field), D[field])
        # 样式：逐套归一（坏套丢弃），style_id 落不到任何一套时落到第一套。
        out["styles"] = _normalize_styles(value.get("styles"))
        ids = {s["id"] for s in out["styles"]}
        sid = value.get("style_id")
        out["style_id"] = sid if (isinstance(sid, str) and sid in ids) else out["styles"][0]["id"]
        # 按 DEFAULT 的键序返回，确保字段顺序稳定（与前端逐项对照更直观）
        return {k: out[k] for k in D}
    except Exception:
        return copy.deepcopy(DEFAULT_LABEL_FORMAT)


# ---------------------------------------------------------------------------
#  套录命中条数
# ---------------------------------------------------------------------------
#
# 「一次检索最多列多少条候选」——多了列表刷屏、Z39.50 也慢；少了容易漏掉想要的那条。
# 默认 8：一屏能看完，又不至于只给两三条。

CATALOG_LIMIT_DEFAULT = 8
CATALOG_LIMIT_MIN, CATALOG_LIMIT_MAX = 1, 50


def catalog_limit(db: Session) -> int:
    """套录命中条数上限。行不存在 / 脏值 / 越界 → 一律回落到默认或夹到边界。

    容错口径与条码位数、分类号截取一致：**永远返回一个能直接用的整数**，
    前端不必再判空、后端各处调用也不会因为脏配置抛异常。
    """
    from ..models import Setting

    row = db.get(Setting, "catalog")
    raw = (row.value or {}).get("limit") if row else None
    if isinstance(raw, bool) or raw is None:
        return CATALOG_LIMIT_DEFAULT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return CATALOG_LIMIT_DEFAULT
    return max(CATALOG_LIMIT_MIN, min(CATALOG_LIMIT_MAX, n))


# ---------------- 本馆机构代码（801$b） ----------------

ORG_CODE_KEY = "org"
ORG_CODE_MAX_LEN = 32


def org_code(db: Session) -> str:
    """本馆机构代码，用于自动补 `801 $b`。空串 = 不自动生成（默认）。

    GB/T 33286—2016 7.10：801 在**交换书目数据时**必备；$b 允许用机构英文缩写，
    也允许中文全称。各家做法不一致（不同图书馆使用不同机构代码），
    所以做成设置项而不是写死。

    读不出来 / 值异常一律回退空串——设置坏了不该让编目失败。
    """
    try:
        from ..models import Setting
        row = db.get(Setting, ORG_CODE_KEY)
        value = row.value if row is not None else None
        if not isinstance(value, dict):
            return ""
        return str(value.get("code") or "").strip()[:ORG_CODE_MAX_LEN]
    except Exception:
        return ""
