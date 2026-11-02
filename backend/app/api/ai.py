# -*- coding: utf-8 -*-
"""规则层优化 + 通用键值设置接口。

  1. 规则层优化（`POST /ai/optimize-local`）：纯规则，不调用外部模型，
     按「规范中心」配置执行，见 `services/optimize.py`；
  2. 全局设置的读写（`/settings/optimize`、`/settings/{key}`）。

⚠️ `/settings/optimize` 必须定义在 `/settings/{key}` **之前** —— FastAPI 按注册顺序
匹配，否则会被 `{key}` 通配吞掉（`key="optimize"` 不在白名单 → 404）。
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..services import record_from_payload
from ..core.clc import (CLASS_NO_DIGITS_MAX, DEFAULT_BASE_DIGITS, DEFAULT_CLASS_NO_DIGITS,
                        DEFAULT_DASH_DIGITS, DEFAULT_MARKS_MODE, MARKS_MODES)
from ..db import get_db
from ..core.route_meta import api

router = APIRouter(tags=["ai"])


@router.get("/settings/optimize")
@api(module="settings", capability="admin.settings.read")
def get_optimize_mode(db: Session = Depends(get_db)):
    """规范配置：模式（auto/manual）+ **逐条规则清单**（可启用/停用与参数）。

    - `mode`：auto（进入编辑页自动执行，**默认**）/ manual（仅手动点按钮）。
    - `rules`：`services/optimize.py::OPTIMIZE_RULES` 注册表的**有序清单**，每条带
      当前启用状态与参数值；前端直接渲染，**界面不硬编码任何一条**（加规则只改后端）。

    注意：这里的"规范"是纯规则修复（ISBN/装订/尺寸/标点/语种/出版年/出版地/题名拆分），
    不调用大模型，所以可以放心默认开启。
    """
    from ..models import Setting
    from ..services.optimize import catalog, optimize_rules_value
    row = db.get(Setting, "optimize_mode")
    mode = (row.value or {}).get("mode", "manual") if row else "manual"
    return {"mode": mode, "rules": catalog(optimize_rules_value(db))}


@router.put("/settings/optimize")
@api(module="settings", capability="admin.settings")
def put_optimize_mode(payload: dict, db: Session = Depends(get_db)):
    """保存规范配置：`mode` 与 `rules` **可部分提交**（至少给一个）。

    - `{"mode": "auto"|"manual"}` —— 与旧口径一致，仍存进 `optimize_mode` 键；
    - `{"rules": {"isbn13": {"enabled": false}, ...}}` —— 逐条局部更新，存进
      `optimize_rules` 键（未列出的条目保持不动）。
    """
    from ..models import Setting
    from ..services.optimize import catalog, optimize_rules_value, save_rules

    if "mode" not in payload and "rules" not in payload:
        raise HTTPException(400, "至少需要提供 mode 或 rules")
    if "mode" in payload:
        mode = payload.get("mode")
        if mode not in ("auto", "manual"):
            raise HTTPException(400, "mode 必须是 auto/manual")
        row = db.get(Setting, "optimize_mode")
        if row is None:
            db.add(Setting(key="optimize_mode", value={"mode": mode}))
        else:
            row.value = {"mode": mode}
        db.commit()
    if "rules" in payload:
        rules_patch = payload.get("rules")
        if not isinstance(rules_patch, dict):
            raise HTTPException(400, "rules 必须是对象")
        try:
            save_rules(db, rules_patch)
        except ValueError as e:
            raise HTTPException(400, str(e))
    row = db.get(Setting, "optimize_mode")
    mode = (row.value or {}).get("mode", "manual") if row else "manual"
    return {"saved": True, "mode": mode, "rules": catalog(optimize_rules_value(db))}


# ---------- 通用键值设置（条码位数 / 分类号截取位数 / 书标格式等） ----------
#
# ⚠️ `locations`（馆藏地点）**已移出本文件**：它现在是「字典中心」的一类，
# 走 `api/dictionaries.py` 的 `/dictionaries/locations`（`services/dictionaries.py`）。
# 旧的 `/settings/locations` 已随之删除（前端是唯一调用方，已一并迁移）。

_ALLOWED_SETTING_KEYS = {"barcode", "class_no", "label_format", "export_presets",
                         "catalog", "org"}

# 条码位数**可以设多个**（旧库常并存 7 位历史号与 8 位新号）；这是个数上限。
_BARCODE_DIGITS_MAX_COUNT = 8


def _validate_label_item(it: dict) -> dict:
    """校验并归一一个书标样式 item；非法 → `HTTPException(400, 说明)`。

    归一后只保留白名单字段（kind / key / text / pt / bold），避免脏键入库。
    """
    from ..services.settings import (
        LABEL_ITEM_KINDS, LABEL_FIELD_KEYS,
        LABEL_ITEM_PT_MIN, LABEL_ITEM_PT_MAX, LABEL_TEXT_MAXLEN,
    )
    if not isinstance(it, dict):
        raise HTTPException(400, "书标样式 item 须为对象")
    kind = it.get("kind")
    if kind not in LABEL_ITEM_KINDS:
        raise HTTPException(400, "书标样式 item.kind 须为 field / barcode / text")
    item = {"kind": kind}
    if kind == "field":
        key = it.get("key")
        if key not in LABEL_FIELD_KEYS:
            raise HTTPException(400, "书标样式 item（field）的 key 非法")
        item["key"] = key
    elif kind == "barcode":
        if it.get("key", "barcode") != "barcode":
            raise HTTPException(400, "书标样式 item（barcode）的 key 须为 barcode")
        item["key"] = "barcode"
    else:  # text：固定文字，缺省用 "/"（旧版系统的分隔符）
        text = it.get("text", "/")
        if not isinstance(text, str) or len(text) > LABEL_TEXT_MAXLEN:
            raise HTTPException(400, f"书标样式 item（text）的 text 须为不超过 {LABEL_TEXT_MAXLEN} 字符的字符串")
        item["text"] = text
    if "pt" in it:
        raw = it["pt"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise HTTPException(400, "书标样式 item.pt 须为数字")
        if not (LABEL_ITEM_PT_MIN <= float(raw) <= LABEL_ITEM_PT_MAX):
            raise HTTPException(400, f"书标样式 item.pt 须在 {LABEL_ITEM_PT_MIN}~{LABEL_ITEM_PT_MAX} 之间")
        item["pt"] = int(raw) if float(raw).is_integer() else round(float(raw), 2)
    if "bold" in it:
        raw = it["bold"]
        if not isinstance(raw, bool):
            raise HTTPException(400, "书标样式 item.bold 须为布尔值")
        item["bold"] = raw
    return item


def _validate_label_styles(value: dict) -> tuple[list, set]:
    """校验 `value['styles']`（长度 1..20、套内 id 唯一、每套结构合法）；非法 → 400。

    返回 `(归一后的 styles, 全部 id 集合)`。**仅在提供了 `styles` 时调用**。
    """
    from ..services.settings import (
        LABEL_MAX_STYLES, LABEL_MAX_STYLE_ROWS, LABEL_MAX_ROW_ITEMS,
        LABEL_STYLE_ID_MAXLEN, LABEL_STYLE_NAME_MAXLEN,
        LABEL_ROW_GAP_MIN, LABEL_ROW_GAP_MAX,
    )
    styles_raw = value.get("styles")
    if not isinstance(styles_raw, list) or not (1 <= len(styles_raw) <= LABEL_MAX_STYLES):
        raise HTTPException(400, f"书标样式 styles 须为长度 1~{LABEL_MAX_STYLES} 的数组")
    styles: list = []
    seen: set = set()
    for st in styles_raw:
        if not isinstance(st, dict):
            raise HTTPException(400, "书标样式的每一套须为对象")
        sid = st.get("id")
        if not isinstance(sid, str) or not (1 <= len(sid) <= LABEL_STYLE_ID_MAXLEN):
            raise HTTPException(400, f"书标样式 id 须为 1~{LABEL_STYLE_ID_MAXLEN} 字符的字符串")
        if sid in seen:
            raise HTTPException(400, f"书标样式 id 重复：{sid}")
        seen.add(sid)
        name = st.get("name")
        if not isinstance(name, str) or not (1 <= len(name) <= LABEL_STYLE_NAME_MAXLEN):
            raise HTTPException(400, f"书标样式 name 须为 1~{LABEL_STYLE_NAME_MAXLEN} 字符的字符串")
        row_gap = st.get("row_gap")
        if isinstance(row_gap, bool) or not isinstance(row_gap, int):
            raise HTTPException(400, "书标样式 row_gap 须为整数")
        if not (LABEL_ROW_GAP_MIN <= row_gap <= LABEL_ROW_GAP_MAX):
            raise HTTPException(400, f"书标样式 row_gap 须在 {LABEL_ROW_GAP_MIN}~{LABEL_ROW_GAP_MAX} 之间")
        rows_raw = st.get("rows")
        if not isinstance(rows_raw, list) or not (1 <= len(rows_raw) <= LABEL_MAX_STYLE_ROWS):
            raise HTTPException(400, f"书标样式 rows 须为长度 1~{LABEL_MAX_STYLE_ROWS} 的数组")
        rows: list = []
        for row in rows_raw:
            if not isinstance(row, dict):
                raise HTTPException(400, "书标样式的栏须为对象")
            items_raw = row.get("items")
            if not isinstance(items_raw, list) or len(items_raw) > LABEL_MAX_ROW_ITEMS:
                raise HTTPException(400, f"书标样式的 items 须为长度 0~{LABEL_MAX_ROW_ITEMS} 的数组")
            rows.append({"items": [_validate_label_item(it) for it in items_raw]})
        styles.append({"id": sid, "name": name, "row_gap": row_gap, "rows": rows})
    return styles, seen


@router.get("/settings/{key}")
@api(module="settings", capability="admin.settings.read")
def get_setting(key: str, db: Session = Depends(get_db)):
    from ..models import Setting
    if key not in _ALLOWED_SETTING_KEYS:
        raise HTTPException(404, "未知设置项")
    if key == "barcode":
        # 与截取位数同一口径：行不存在 / 脏值 / 旧单值写法 → 都归一成「允许的位数列表」，
        # 前端永远拿到可渲染的结构（不会因为返回 {} 而不知道默认是几位）。
        from ..services.batch import barcode_digits_list
        return {"key": key, "value": {"digits": barcode_digits_list(db)}}
    if key == "class_no":
        # 行存在与否都走**与截取同一套容错**（非 dict / 单项脏值 → 该项回退默认；
        # digits 可解析但越界 → 夹到 0..6），见 services/settings.class_no_rule。
        # 否则手工写入 {"digits": 99} 时：设置页显示 99、预览按 6、实际按 6 截，
        # 三处口径不一致。
        from ..services.settings import class_no_rule
        return {"key": key, "value": class_no_rule(db).as_dict()}
    if key == "label_format":
        # 与截取位数同一口径：行不存在 / 脏值 / 越界都走 `label_format_value()` 的容错，
        # 前端永远拿到一份**完整**的格式（绝不返回空 {}，否则界面无从渲染）。
        from ..services.settings import label_format_value
        return {"key": key, "value": label_format_value(db)}
    if key == "export_presets":
        # 同上：行不存在 / 脏值 → 出厂三套，前端永远拿到可渲染的预设清单。
        # 额外带 `source_fields`：可作映射**源**的我方字段清单（唯一真源在后端
        # `io/iso.py::target_field_options()`）—— 界面上就不用再抄一份字段表，
        # 加了新字段也不会两边不同步。PUT 只读 `value`，多这个键无害。
        from ..io.iso import target_field_options
        from ..services.settings import export_presets_value
        return {"key": key, "value": export_presets_value(db),
                "source_fields": target_field_options()}
    if key == "org":
        # 行不存在时也要给出可渲染的结构（前端据此显示"未设置"），不要返回 {}
        from ..services.settings import ORG_CODE_KEY, org_code
        row = db.get(Setting, ORG_CODE_KEY)
        return {"key": key, "value": {"code": org_code(db)},
                "saved": row is not None}
    if key == "catalog":
        # 套录命中条数：行不存在 / 脏值 / 越界 → 由 catalog_limit() 统一兜底，
        # 界面永远拿到一个能直接显示的数字（不会因为返回 {} 而不知道默认是 8）。
        from ..services.settings import CATALOG_LIMIT_DEFAULT, CATALOG_LIMIT_MAX, \
            CATALOG_LIMIT_MIN, catalog_limit
        return {"key": key, "value": {"limit": catalog_limit(db)},
                "min": CATALOG_LIMIT_MIN, "max": CATALOG_LIMIT_MAX,
                "default": CATALOG_LIMIT_DEFAULT}
    row = db.get(Setting, key)
    if row is not None:
        return {"key": key, "value": row.value}
    return {"key": key, "value": {}}


@router.put("/settings/{key}")
@api(module="settings", capability="admin.settings")
def put_setting(key: str, payload: dict, db: Session = Depends(get_db)):
    from ..models import Setting
    if key not in _ALLOWED_SETTING_KEYS:
        raise HTTPException(404, "未知设置项")
    value = payload.get("value")
    if not isinstance(value, dict):
        raise HTTPException(400, "value 必须是对象")
    if key == "org":
        from ..services.settings import ORG_CODE_MAX_LEN
        code = str(value.get("code") or "").strip()
        if len(code) > ORG_CODE_MAX_LEN:
            raise HTTPException(400, f"机构代码最长 {ORG_CODE_MAX_LEN} 个字符")
        if any(ord(ch) < 32 for ch in code):
            raise HTTPException(400, "机构代码不能含控制字符")
        # 空串是合法值：表示"不自动生成 801"
        value = {"code": code}
    elif key == "barcode":
        # 条码位数（**可多个**，如 [7, 8]）。兼容旧的单值写法 {"digits": 8} → [8]，
        # 统一归一成「升序去重的列表」存库，读取侧见 services.batch.barcode_digits_list()。
        from ..services.batch import _DEFAULT_BARCODE_DIGITS
        raw = value.get("digits", _DEFAULT_BARCODE_DIGITS)
        vals = raw if isinstance(raw, list) else [raw]
        out: list[int] = []
        for v in vals:
            # bool 是 int 子类，显式排除（True 会伪装成 1）
            if isinstance(v, bool) or not isinstance(v, int):
                raise HTTPException(400, "条码位数须为 4~16 之间的整数")
            if not (4 <= v <= 16):
                raise HTTPException(400, "条码位数须在 4~16 之间")
            if v not in out:
                out.append(v)
        if not out:
            raise HTTPException(400, "至少需要设置一个条码位数")
        if len(out) > _BARCODE_DIGITS_MAX_COUNT:
            raise HTTPException(400, f"条码位数最多设置 {_BARCODE_DIGITS_MAX_COUNT} 个")
        value = {"digits": sorted(out)}
    elif key == "catalog":
        # 套录命中条数：只收一个整数，越界直接 400（不静默夹取——界面会因此显示
        # 一个和输入不一致的值，用户以为没保存成功）。
        from ..services.settings import (CATALOG_LIMIT_DEFAULT, CATALOG_LIMIT_MAX,
                                        CATALOG_LIMIT_MIN)
        raw = value.get("limit", CATALOG_LIMIT_DEFAULT)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise HTTPException(400, "套录命中条数须为整数")
        if not (CATALOG_LIMIT_MIN <= raw <= CATALOG_LIMIT_MAX):
            raise HTTPException(
                400, f"套录命中条数须在 {CATALOG_LIMIT_MIN}~{CATALOG_LIMIT_MAX} 之间")
        value = {"limit": raw}
    elif key == "class_no":
        # 四键：缺项用默认补齐（与 label_format 同哲学），
        # 提供了但非法才 400。
        # ⚠️ `keep_mark_when_empty`（空小数位保留复分号）已移除。
        #    存量库里多带这个键**不必迁移** —— 读取侧 `rule_from_raw()` 只看白名单键。
        #    提交时带上它也不会被存（下面 `value = {...}` 是白名单重建，不是原样落库）。
        digits = value.get("digits", DEFAULT_CLASS_NO_DIGITS)
        # 必须是整数（bool 是 int 子类，显式排除），且落在 0..CLASS_NO_DIGITS_MAX
        if (isinstance(digits, bool) or not isinstance(digits, int)
                or not (0 <= digits <= CLASS_NO_DIGITS_MAX)):
            raise HTTPException(
                400, f"小数点截取位数须为 0~{CLASS_NO_DIGITS_MAX} 之间的整数")
        # 总论复分号位数（；即 `-` 后保留几位）与小数点位数**同一套校验**：
        # 两项都是"0..6 的整数"，校验口径分叉会让设置页出现"上面那个拦得住、
        # 下面那个拦不住"。两条消息各自点名是哪一项 —— 只写"分类号截取位数"分不出是哪个。
        dash_digits = value.get("dash_digits", DEFAULT_DASH_DIGITS)
        if (isinstance(dash_digits, bool) or not isinstance(dash_digits, int)
                or not (0 <= dash_digits <= CLASS_NO_DIGITS_MAX)):
            raise HTTPException(
                400, f"总论复分号截取位数须为 0~{CLASS_NO_DIGITS_MAX} 之间的整数")
        marks = value.get("marks", DEFAULT_MARKS_MODE)
        # 枚举必须落白名单（字符串不能 clamp），否则读取侧会静默回退默认，
        # 用户看到"保存成功但没生效"。
        if marks not in MARKS_MODES:
            raise HTTPException(400, "复分号口径须为 " + " / ".join(MARKS_MODES) + " 之一")
        # 字母后数字位数阈值。与上面两个位数**同一套校验**（都是 0..6 整数），
        # 消息同样**各自点名** —— 三个位数并存后只写"分类号截取位数"根本分不出是哪个错了。
        # `0` 在这里是**有意义的取值**（= 关闭本规则），不是"未设置"：所以用 `value.get(k, 默认)`
        # 时默认值取 `DEFAULT_BASE_DIGITS`（0），显式传 0 与缺项结果一致。
        base_digits = value.get("base_digits", DEFAULT_BASE_DIGITS)
        if (isinstance(base_digits, bool) or not isinstance(base_digits, int)
                or not (0 <= base_digits <= CLASS_NO_DIGITS_MAX)):
            raise HTTPException(
                400, f"字母后数字截取位数须为 0~{CLASS_NO_DIGITS_MAX} 之间的整数")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise HTTPException(400, "分类号截取开关必须是布尔值")
        value = {"digits": digits, "dash_digits": dash_digits,
                 "marks": marks, "base_digits": base_digits,
                 "enabled": enabled}
    elif key == "label_format":
        from ..services.settings import (
            DEFAULT_LABEL_FORMAT, LABEL_FORMAT_LIMITS,
            LABEL_INT_FIELDS, LABEL_FLOAT_FIELDS, LABEL_BOOL_FIELDS, LABEL_ENUM_FIELDS,
        )
        # 逐项严格校验：**缺项用默认补齐**；提供了但类型/范围/枚举非法 → 400（写清字段与范围）。
        out = copy.deepcopy(DEFAULT_LABEL_FORMAT)
        # ① 枚举：必须落在白名单内（font_family 会进 CSS，白名单同时是防注入手段）。
        for field, options in LABEL_ENUM_FIELDS.items():
            if field not in value:
                continue
            raw = value[field]
            if not isinstance(raw, str) or raw not in options:
                raise HTTPException(400, f"书标格式字段 {field} 取值非法")
            out[field] = raw
        # ② 布尔：`bool` 是 `int` 子类，必须显式判 `isinstance(raw, bool)`。
        for field in LABEL_BOOL_FIELDS:
            if field not in value:
                continue
            raw = value[field]
            if not isinstance(raw, bool):
                raise HTTPException(400, f"书标格式字段 {field} 须为布尔值")
            out[field] = raw
        # ③ 整数（边长 / 间距 / 边距 / 行列 / 前导 0 位数）。
        for field in LABEL_INT_FIELDS:
            if field not in value:
                continue
            raw = value[field]
            lo, hi = LABEL_FORMAT_LIMITS[field]
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise HTTPException(400, f"书标格式字段 {field} 须为整数")
            if not (lo <= raw <= hi):
                raise HTTPException(400, f"书标格式字段 {field} 须在 {lo}~{hi} 之间")
            out[field] = raw
        # ④ 浮点（纸张宽高（厘米））。
        for field in LABEL_FLOAT_FIELDS:
            if field not in value:
                continue
            raw = value[field]
            lo, hi = LABEL_FORMAT_LIMITS[field]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise HTTPException(400, f"书标格式字段 {field} 须为数字")
            if not (lo <= float(raw) <= hi):
                raise HTTPException(400, f"书标格式字段 {field} 须在 {lo}~{hi} 之间")
            out[field] = round(float(raw), 1)
        # ⑤ 书标样式（多套命名样式 + 当前使用的 style_id）。
        if "styles" in value:
            styles, seen_ids = _validate_label_styles(value)
            out["styles"] = styles
        else:
            seen_ids = {s["id"] for s in out["styles"]}
        if "style_id" in value:
            sid = value["style_id"]
            if not isinstance(sid, str) or sid not in seen_ids:
                raise HTTPException(400, "书标格式 style_id 必须指向 styles 中的某一套")
            out["style_id"] = sid
        elif "styles" in value:
            # 只给了 styles、没给 style_id：落到第一套（避免默认 s-default 不在新 styles 里产生歧义）
            out["style_id"] = out["styles"][0]["id"]
        value = out
    elif key == "export_presets":
        # 逐项严格校验（缺项不补齐：预设**整体**提交，与书标格式同一做法）。
        # 与读取侧 `export_presets_value()` 的容错**有意不同**：读要"怎么都能渲染"，
        # 写要"错了就拦住" —— 静默丢掉用户刚编辑的那一套，比 400 难查得多。
        from ..io.iso import parse_tag_code
        from ..services.settings import (
            EXPORT_MAX_ENTRIES, EXPORT_MAX_PRESETS,
            EXPORT_PRESET_ID_MAXLEN, EXPORT_PRESET_NAME_MAXLEN,
            _normalize_export_const, _normalize_export_mapping,
        )
        raw = value.get("presets")
        if not isinstance(raw, list) or not (1 <= len(raw) <= EXPORT_MAX_PRESETS):
            raise HTTPException(
                400, f"导出格式预设 presets 须为长度 1~{EXPORT_MAX_PRESETS} 的数组")
        presets: list[dict] = []
        seen: set[str] = set()
        for p in raw:
            if not isinstance(p, dict):
                raise HTTPException(400, "导出格式预设的每一套须为对象")
            pid = p.get("id")
            if not isinstance(pid, str) or not (1 <= len(pid) <= EXPORT_PRESET_ID_MAXLEN):
                raise HTTPException(
                    400, f"导出格式预设 id 须为 1~{EXPORT_PRESET_ID_MAXLEN} 字符的字符串")
            if pid in seen:
                raise HTTPException(400, f"导出格式预设 id 重复：{pid}")
            seen.add(pid)
            name = p.get("name")
            if not isinstance(name, str) or not (1 <= len(name) <= EXPORT_PRESET_NAME_MAXLEN):
                raise HTTPException(
                    400, f"导出格式预设 name 须为 1~{EXPORT_PRESET_NAME_MAXLEN} 字符的字符串")
            mp = p.get("mapping") or {}
            if not isinstance(mp, dict):
                raise HTTPException(400, "导出格式预设 mapping 须为对象")
            if len(mp) > EXPORT_MAX_ENTRIES:
                raise HTTPException(400, f"单个预设的映射最多 {EXPORT_MAX_ENTRIES} 条")
            for k, v in mp.items():
                if not isinstance(k, str) or not parse_tag_code(k):
                    raise HTTPException(
                        400, f"导出格式预设的映射键非法：{k}（须是「字段$子字段」形态）")
                # 值可以是「字段$子字段」**或它的数组**（一个源补写到多个目标）
                dsts = v if isinstance(v, (list, tuple)) else [v]
                if not dsts:
                    raise HTTPException(400, f"导出格式预设的映射项 {k} 没有目标")
                for d in dsts:
                    if not isinstance(d, str) or d == k or not parse_tag_code(d):
                        raise HTTPException(
                            400, f"导出格式预设的映射项非法：{k} → {d}"
                                 "（两侧都须是「字段$子字段」形态，且不能相同）")
            cst = p.get("const") or {}
            if not isinstance(cst, dict):
                raise HTTPException(400, "导出格式预设 const 须为对象")
            for k, v in cst.items():
                if not isinstance(k, str) or not parse_tag_code(k):
                    raise HTTPException(
                        400, f"导出格式预设的常量键非法：{k}（须是「字段$子字段」形态）")
                if not isinstance(v, str) or not v.strip():
                    raise HTTPException(400, f"导出格式预设的常量 {k} 须为非空字符串")
            # 回显走**与读取侧同一份归一**（字符串值 → 数组、补 const 键），
            # 否则 PUT 的回显与随后 GET 的形状不一致（round-trip 用例会红）。
            presets.append({"id": pid, "name": name,
                            "mapping": _normalize_export_mapping(mp),
                            "const": _normalize_export_const(cst)})
        did = value.get("default_id")
        if not isinstance(did, str) or did not in seen:
            did = presets[0]["id"]           # 缺项 / 指向不存在的一套 → 落到第一套
        value = {"presets": presets, "default_id": did}
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()
    return {"saved": True, "key": key, "value": value}


@router.post("/ai/optimize-local")
@api(module="records", level="read")
def ai_optimize_local(payload: dict, db: Session = Depends(get_db)):
    """对未保存的字段载荷执行规则层优化（不查库、不落库）——临时副本/新建记录用。

    **按「规范中心」的配置执行**（`settings['optimize_rules']`，见 `services/optimize.py`）：
    用户在全局设置里启停哪些条目、把默认尺寸/装订/语种设成什么，这里就照做。
    未配置时 = 内置默认（全开，含 ISBN 10→13、装订缺省平装、尺寸缺省 20cm）。
    """
    from ..services.optimize import apply_optimize, optimize_rules_value
    from ..core.validators import validate

    rec = record_from_payload(payload)
    applied = apply_optimize(rec, optimize_rules_value(db))
    return {"applied": applied, "fields": payload_of_fields(rec),
            "findings": [f.__dict__ for f in validate(rec)]}


def payload_of_fields(rec):
    import json as _json
    return _json.loads(rec.to_json())
