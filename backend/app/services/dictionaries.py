# -*- coding: utf-8 -*-
"""统一字典中心。

系统内置的 7 类基础数据 —— **馆藏地点 / 货币类型 /
文献类型 / 获得方式 / 出版物语种 / 付款方式 / 责任方式**（说明书第二章第三节原文）。

这些字典的**内容**我们早就随包带了（`app/data/vendor_dicts.json`，见
`scripts/import_vendor_dump.py`），但此前是**只读参考数据**，界面只在「全局设置」
里能改馆藏地点，其余 6 类无处可看、无处可改。本模块把它升级为
「**内置默认 + 用户增删**」的可维护字典：

- 内置默认来自随包 JSON（`_builtin()`），**只读、不进库**；
- 用户改过的类型整体存进 `settings` 表 key = ``dictionaries``（**只存改过的**）；
- 生效列表 = 存过就用存的、没存过就用内置默认（`effective()`）。

⚠️ **刻意不新建数据库表**：这些是"作业参数"量级的小数据，与 `barcode` / `class_no` /
`label_format` 同款放 `settings` 的 key-value 里 —— 因此**不需要迁移脚本**，
`create_all()` 也不用动。

⚠️ **旧的 `settings['locations']`（`{"list": [...]}`）仍被读取**（`_legacy_locations`），
免得老库升级后地点列表凭空回到内置默认；一旦通过本模块保存过 locations，
旧键会被删掉，避免两份数据分叉。
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

# 本文件在 backend/app/services/ 下，parents[1] 就是 backend/app/
DICTS_FILE = Path(__file__).resolve().parents[1] / "data/vendor_dicts.json"

# settings 表里存"用户改过的字典"的那个 key。
SETTING_KEY = "dictionaries"
# 旧存储：settings['locations'] = {"list": [...]}（2026-10-06 前只有馆藏地点可改）。
LEGACY_LOCATIONS_KEY = "locations"
# 「本馆默认币种」单独存一个 key（值是 `{"code": "CNY"}`）。
# **刻意不与字典本体混存**：`dictionaries` 那个 JSONB 里全是"列表"，
# 塞一个标量进去会让 `effective()` 的读取路径分叉；而且默认币种是"本馆用哪个"，
# 与"有哪些可选"是两件事，混在一起恢复默认时容易互相牵连。
DEFAULT_CURRENCY_KEY = "default_currency"
# 兜底币种：生效货币列表为空时用（正常情况下不会走到，列表至少有一条）。
FALLBACK_CURRENCY = "CNY"

# 条目形态：
#   simple —— 条目就是**字符串**（值即所见，如馆藏地点「流通库」、责任方式「著」）；
#   coded  —— 条目是 `{code, name}`（**值是码**，界面显示名，如语种 `chi/汉语`）。
KIND_SIMPLE = "simple"
KIND_CODED = "coded"

# 上限（防手滑灌爆 settings 行）：条数 / 单条长度。
MAX_ITEMS = 500
MAX_VALUE_LEN = 64
MAX_CODE_LEN = 32
MAX_NAME_LEN = 64

# 馆藏地点内置默认（与旧 api/ai.py::DEFAULT_LOCATIONS 一致，原样搬过来）。
DEFAULT_LOCATIONS = ["流通库", "阅览室", "参考阅览区"]

# 货币类型：内置字典**没有随包导出**（`sys_Dictionary` 里只有语种/付款方式/
# 获得方式/文献类型/阅览室这 5 类），所以这里给一份**内置默认**，用户可自行增删。
# 说明书写明它用于图书编目（`010$d` 定价的货币码）。
DEFAULT_CURRENCIES = [
    {"code": "CNY", "name": "人民币"},
    {"code": "USD", "name": "美元"},
    {"code": "HKD", "name": "港币"},
    {"code": "TWD", "name": "新台币"},
    {"code": "EUR", "name": "欧元"},
    {"code": "JPY", "name": "日元"},
    {"code": "GBP", "name": "英镑"},
    {"code": "KRW", "name": "韩元"},
    {"code": "SGD", "name": "新加坡元"},
]

# 7 类字典的**唯一注册表**（key / 中文名 / 条目形态）。顺序 = 界面上的展示顺序。
# 改这里就等于改接口与页面能管理的字典集合（前端不硬编码任何一份）。
DICT_TYPES: tuple[dict, ...] = (
    {"key": "locations", "label": "馆藏地点", "kind": KIND_SIMPLE},
    {"key": "currencies", "label": "货币类型", "kind": KIND_CODED},
    {"key": "item_types", "label": "文献类型", "kind": KIND_CODED},
    {"key": "acquisition", "label": "获得方式", "kind": KIND_CODED},
    {"key": "languages", "label": "出版物语种", "kind": KIND_CODED},
    {"key": "payment", "label": "付款方式", "kind": KIND_CODED},
    {"key": "responsibility", "label": "责任方式", "kind": KIND_SIMPLE},
)

_BY_KEY = {t["key"]: t for t in DICT_TYPES}


# --------------------------------------------------------------------------
#  随包字典文件（只读）
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _vendor_dicts() -> dict:
    """读随包的内置字典 JSON；文件缺失/损坏 → 空壳（**绝不抛**）。"""
    try:
        return json.loads(DICTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"responsibility": [], "sys_dictionary": {}}


def sys_dict(kind: str) -> list[dict]:
    """`sys_Dictionary` 里某一类（出版物语种 / 文献类型 / 获得方式 / 付款方式 / 阅览室）。"""
    return _vendor_dicts().get("sys_dictionary", {}).get(kind, [])


def _builtin(key: str) -> list:
    """某一类字典的**内置默认**（深拷贝，避免调用方改到缓存里的对象）。"""
    if key == "locations":
        return list(DEFAULT_LOCATIONS)
    if key == "currencies":
        return copy.deepcopy(DEFAULT_CURRENCIES)
    if key == "item_types":
        return copy.deepcopy(sys_dict("文献类型"))
    if key == "acquisition":
        return copy.deepcopy(sys_dict("获得方式"))
    if key == "languages":
        return copy.deepcopy(sys_dict("出版物语种"))
    if key == "payment":
        return copy.deepcopy(sys_dict("付款方式"))
    if key == "responsibility":
        return list(_vendor_dicts().get("responsibility", []))
    return []


# --------------------------------------------------------------------------
#  注册表查询
# --------------------------------------------------------------------------

def type_meta(key: str) -> dict:
    """取某一类的元信息；未知 key 抛 `KeyError`（调用方转 404）。"""
    return _BY_KEY[key]


def known_keys() -> list[str]:
    return [t["key"] for t in DICT_TYPES]


def kind_of(key: str) -> str:
    return type_meta(key)["kind"]


# --------------------------------------------------------------------------
#  归一 / 校验（**唯一真源**：写入校验与读取容错共用）
# --------------------------------------------------------------------------

def normalize_items(kind: str, raw) -> list:
    """把外部传入的条目列表归一成可存形态；非法 → `ValueError`（消息可直接给用户看）。

    容错与 `services/settings.py` 同一哲学：
      - 结构非法（不是列表 / 空列表 / 超上限）→ 报错（**不做静默丢弃**，否则用户以为存上了）；
      - 单条非法（空串 / 超长 / 类型不对）→ 报错，且消息里带上第几条，便于定位；
      - 重复项 → **静默去重**（保留首次出现），重复不是错误、只是噪音。
    """
    if not isinstance(raw, list):
        raise ValueError("字典内容须为数组")
    if not raw:
        raise ValueError("字典至少要保留一条")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"字典最多 {MAX_ITEMS} 条")

    out: list = []
    if kind == KIND_SIMPLE:
        seen: set[str] = set()
        for i, item in enumerate(raw, 1):
            if not isinstance(item, str):
                raise ValueError(f"第 {i} 条须为文本")
            v = item.strip()
            if not v:
                raise ValueError(f"第 {i} 条不能为空")
            if len(v) > MAX_VALUE_LEN:
                raise ValueError(f"第 {i} 条超过 {MAX_VALUE_LEN} 个字符")
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out

    # coded：{code, name}
    seen_codes: set[str] = set()
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 条须为 {{code, name}} 对象")
        code = item.get("code")
        name = item.get("name")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"第 {i} 条的代码不能为空")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"第 {i} 条的名称不能为空")
        code, name = code.strip(), name.strip()
        if len(code) > MAX_CODE_LEN:
            raise ValueError(f"第 {i} 条的代码超过 {MAX_CODE_LEN} 个字符")
        if len(name) > MAX_NAME_LEN:
            raise ValueError(f"第 {i} 条的名称超过 {MAX_NAME_LEN} 个字符")
        if code in seen_codes:
            continue
        seen_codes.add(code)
        out.append({"code": code, "name": name})
    return out


# --------------------------------------------------------------------------
#  存储读写
# --------------------------------------------------------------------------

def _stored(db: Session) -> dict:
    """`settings['dictionaries']` 的原始内容；行不存在 / 不是对象 → `{}`。"""
    try:
        from ..models import Setting
        row = db.get(Setting, SETTING_KEY)
        value = row.value if row is not None else None
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _legacy_locations(db: Session) -> list[str] | None:
    """旧的 `settings['locations'] = {"list": [...]}`；没有/空 → `None`。"""
    try:
        from ..models import Setting
        row = db.get(Setting, LEGACY_LOCATIONS_KEY)
        value = row.value if row is not None else None
        if isinstance(value, dict):
            items = value.get("list")
            if isinstance(items, list):
                try:
                    return normalize_items(KIND_SIMPLE, items)
                except ValueError:
                    return None
    except Exception:
        pass
    return None


def _drop_legacy_locations(db: Session) -> None:
    """删掉旧键（一旦新存储接管了 locations，旧键就是分叉源）。"""
    try:
        from ..models import Setting
        row = db.get(Setting, LEGACY_LOCATIONS_KEY)
        if row is not None:
            db.delete(row)
    except Exception:
        pass


def effective(db: Session, key: str) -> list:
    """某一类字典的**生效列表**（用户改过用用户的，否则内置默认）。**绝不抛**。

    读侧同样过一遍 `normalize_items`：手工改库塞进来的脏值（空串/超长/重复）会被
    归一或（整体非法时）退回内置默认，绝不把脏数据喂给界面。
    """
    kind = kind_of(key)                      # 未知 key 会 KeyError —— 调用方先判
    items = _stored(db).get(key)
    if items is not None:
        try:
            return normalize_items(kind, items)
        except ValueError:
            pass                             # 存进去的整份都不合法 → 退回内置默认
    if key == "locations":
        legacy = _legacy_locations(db)
        if legacy:
            return legacy
    return _builtin(key)


def is_customized(db: Session, key: str) -> bool:
    """该类是否被用户改过（存过一份合法内容）。"""
    try:
        items = _stored(db).get(key)
        normalize_items(kind_of(key), items)
        return True
    except (ValueError, KeyError):
        return False


def save_type(db: Session, key: str, raw) -> list:
    """保存某一类字典；`KeyError` = 未知类型，`ValueError` = 内容非法。"""
    kind = kind_of(key)
    items = normalize_items(kind, raw)
    stored = dict(_stored(db))
    stored[key] = items
    from ..models import Setting
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        db.add(Setting(key=SETTING_KEY, value=stored))
    else:
        row.value = stored                         # 赋新对象：原地改 JSONB 不会被侦测
    if key == "locations":
        _drop_legacy_locations(db)                 # 新存储接管后旧键即分叉源
    if key == "currencies":
        # 币种被删掉了，默认币种不能再指向它（见 _prune_default_currency）
        _prune_default_currency(db, [c["code"] for c in items])
    db.commit()
    return items


def reset_type(db: Session, key: str) -> list:
    """恢复某一类的内置默认（删掉用户那份）。`KeyError` = 未知类型。"""
    kind_of(key)                                   # 校验存在性
    stored = dict(_stored(db))
    stored.pop(key, None)
    from ..models import Setting
    row = db.get(Setting, SETTING_KEY)
    if not stored:
        if row is not None:
            db.delete(row)                         # 全空就删行，别留 {"": ...} 空壳
    elif row is None:
        db.add(Setting(key=SETTING_KEY, value=stored))
    else:
        row.value = stored
    if key == "locations":
        _drop_legacy_locations(db)
    if key == "currencies":
        _prune_default_currency(db, [c["code"] for c in _builtin("currencies")])
    db.commit()
    return _builtin(key)


# --------------------------------------------------------------------------
#  本馆默认币种（定价 `010$d` 的货币码）
# --------------------------------------------------------------------------

def currency_codes(db: Session) -> list[str]:
    """生效的货币码列表（顺序同字典，第一条是内置默认的兜底币种）。"""
    return [c["code"] for c in effective(db, "currencies")
            if isinstance(c, dict) and c.get("code")]


def default_currency(db: Session) -> str:
    """本馆默认币种码。**绝不抛**。

    读侧容错与 `effective()` 同一哲学：没设过 / 存了脏值 / 存了一个**已经从字典里
    删掉**的码 → 一律回退到生效货币列表的第一条（内置默认是 CNY）。
    这样"改字典把币种删了"不会让定价框凭空报错或留空。
    """
    codes = currency_codes(db)
    code = ""
    try:
        from ..models import Setting
        row = db.get(Setting, DEFAULT_CURRENCY_KEY)
        value = row.value if row is not None else None
        if isinstance(value, dict):
            code = str(value.get("code") or "").strip().upper()
        elif isinstance(value, str):
            code = value.strip().upper()
    except Exception:
        code = ""
    if code and code in codes:
        return code
    return codes[0] if codes else FALLBACK_CURRENCY


def set_default_currency(db: Session, code) -> str:
    """设置本馆默认币种；码不在生效货币列表里 → `ValueError`（消息可直接给用户看）。"""
    code = str(code or "").strip().upper()
    codes = currency_codes(db)
    if code not in codes:
        raise ValueError("默认币种必须是「货币类型」里的一个（当前可选："
                         + "、".join(codes) + "）")
    from ..models import Setting
    row = db.get(Setting, DEFAULT_CURRENCY_KEY)
    if row is None:
        db.add(Setting(key=DEFAULT_CURRENCY_KEY, value={"code": code}))
    else:
        row.value = {"code": code}                 # 赋新对象：原地改 JSONB 不会被侦测
    db.commit()
    return code


def _prune_default_currency(db: Session, codes: list[str]) -> None:
    """货币列表变了之后，若默认币种已不在列表里就删掉那一行（回退到第一条）。

    不删的话会留一个"幽灵默认"：用户把 CNY 删掉、默认自动变成 USD，之后又把
    CNY 加回来，默认会**无声地**跳回 CNY —— 看起来像设置自己变了。
    删掉更符合直觉：不在列表里的选择就等于没选过。
    """
    try:
        from ..models import Setting
        row = db.get(Setting, DEFAULT_CURRENCY_KEY)
        if row is None:
            return
        value = row.value
        code = ""
        if isinstance(value, dict):
            code = str(value.get("code") or "").strip().upper()
        elif isinstance(value, str):
            code = value.strip().upper()
        if code and code not in codes:
            db.delete(row)
    except Exception:
        pass

