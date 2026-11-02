# -*- coding: utf-8 -*-
"""Excel 导入：发货清单模板（列映射与清洗规则见）。

发货清单是馆配商随书交付数据的真实形态，也是 MVP 主线的输入端：
  清单行 -> DeliveryRow（清洗+告警）-> 套录/规范补全 -> 规范 MARC 记录。

2026-10-07 用户反馈后扩展：
- 列映射支持**手动指定**（`mapping`，列下标 -> 字段 key），书商列名五花八门也能导；
- 字段注册表扩成**手动录入的整套条目**（简编表单 23 栏 + 书次号/卷册号/排架号 +
  索书号 + 条码号/馆藏地点），支持**直接导入图书库**（`target=records`）；
- 哪些**强制**由 `ImportField.required` 与 `missing_required` 定义（见）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core import clean as cl
from ..core import isbn as isbnlib
from ..core.callno import parse_call_no
from ..core.marc import Record

# 发货清单「列名 -> 字段」的**自动识别**表（列名对不上时用手动映射兜底）。
# 一张表两个用途：① `_find_header_row()` 数匹配列名最多的一行当表头；
# ② 未给 mapping 时按列名猜字段。键是**清洗后（strip）的表头文字**。
# 单表读取行数上限（防 xlsx 解压炸弹，见 _load_rows 注释）。
MAX_SHEET_ROWS = 100_000
DELIVERY_COLUMNS = {
    "序号": "seq", "书号": "isbn", "ISBN": "isbn", "书   名": "title", "书名": "title",
    "出版社": "publisher", "作者": "author", "责任者": "author", "本版年月": "year_month",
    "出版年": "year_month", "中图分类": "cls_coarse", "定价": "price", "数量": "qty",
    "码洋": "amount", "打包包号": "package",
    # 扩展条目（手动录入的整套字段）
    "条码号": "barcode", "条码": "barcode", "索书号": "call_no",
    "分类号": "class_no", "书次号": "book_no", "卷册号": "volume", "排架号": "shelf_no",
    "馆藏地点": "location", "馆藏地": "location",
    "副题名": "subtitle", "并列题名": "parallel_title",
    "丛书名": "series", "丛编分册": "series_part", "丛编卷次": "series_vol",
    "装订": "binding", "语种": "language", "版次": "edition", "出版地": "place",
    "页数": "pages", "图表": "chart", "尺寸": "size", "附件": "attach",
    "内容提要": "abstract", "附注": "note", "其他责任者": "other_author",
}


@dataclass(frozen=True)
class ImportField:
    """一个可映射的目标字段。

    - `kind`：落到哪里 —— `marc`（tag/code 子字段）/ `record`（记录级列）/
      `holding`（馆藏级）/ `callno`（索书号整串，解析成分类号+书次号+卷册号）/
      `meta`（仅供资源库草稿，不入 MARC）。
    - `required`：**强制** —— 缺了该行不入库（图书库路径），界面标红星。
    """
    key: str
    label: str
    kind: str = "marc"
    tag: str = ""
    code: str = ""
    required: bool = False


# **可映射字段注册表**（顺序即界面顺序）。改这里就够了，DELIVERY_COLUMNS 与界面都从它派生。
DELIVERY_FIELDS: list[ImportField] = [
    # 标准号与获得方式
    ImportField("isbn", "ISBN", tag="010", code="a"),
    ImportField("binding", "装订", tag="010", code="b"),
    ImportField("price", "定价", tag="010", code="d"),
    # 题名与责任者
    ImportField("title", "正题名", tag="200", code="a", required=True),
    ImportField("parallel_title", "并列题名", tag="200", code="d"),
    ImportField("subtitle", "副题名", tag="200", code="e"),
    ImportField("author", "第一责任者", tag="200", code="f"),
    ImportField("other_author", "其他责任者", tag="200", code="g"),
    ImportField("language", "语种", tag="101", code="a"),
    # 版本与出版发行
    ImportField("edition", "版次", tag="205", code="a"),
    ImportField("place", "出版地", tag="210", code="a"),
    ImportField("publisher", "出版社", tag="210", code="c", required=True),
    ImportField("year_month", "出版年", tag="210", code="d", required=True),
    # 载体形态
    ImportField("pages", "页数", tag="215", code="a"),
    ImportField("chart", "图表", tag="215", code="c"),
    ImportField("size", "尺寸", tag="215", code="d"),
    ImportField("attach", "附件", tag="215", code="e"),
    # 丛编
    ImportField("series", "丛书名", tag="225", code="a"),
    ImportField("series_part", "丛编分册", tag="225", code="h"),
    ImportField("series_vol", "丛编卷次", tag="225", code="v"),
    # 分类与索书号（图书库必填：条码 + 索书号）
    ImportField("class_no", "分类号", tag="690", code="a", required=True),
    ImportField("book_no", "书次号", kind="record", required=True),
    ImportField("volume", "卷册号", kind="record"),
    ImportField("shelf_no", "排架号", kind="record"),
    ImportField("call_no", "索书号（整串，可代替分类号+书次号）", kind="callno"),
    # 馆藏
    ImportField("barcode", "条码号", kind="holding", required=True),
    ImportField("location", "馆藏地", kind="holding"),
    # 提要与附注
    ImportField("abstract", "内容提要", tag="330", code="a"),
    ImportField("note", "附注", tag="300", code="a"),
    # 仅供资源库草稿（不入 MARC）
    ImportField("seq", "序号", kind="meta"),
    ImportField("cls_coarse", "中图分类（仅线索）", kind="meta"),
    ImportField("qty", "数量", kind="meta"),
    ImportField("amount", "码洋", kind="meta"),
    ImportField("package", "打包包号", kind="meta"),
]

FIELD_BY_KEY: dict[str, ImportField] = {f.key: f for f in DELIVERY_FIELDS}
# 可被映射到的字段 key（人工映射时校验；非法值一律当"不导入"）
_MAPPABLE: frozenset[str] = frozenset(FIELD_BY_KEY)
# 落到 MARC 子字段的字段（(tag, code) -> key），供 row_to_draft_record 组装记录
_MARC_BY_TAGCODE: dict[tuple[str, str], str] = {
    (f.tag, f.code): f.key for f in DELIVERY_FIELDS if f.kind == "marc"
}


@dataclass
class DeliveryRow:
    seq: str | None = None
    isbn: str | None = None
    title: str | None = None
    subtitle: str | None = None
    series: str | None = None
    publisher: str | None = None
    author: str | None = None          # 原始责任者列（清洗前）
    authors: list[str] = field(default_factory=list)
    role: str | None = None
    year_month: str | None = None
    year: str | None = None
    cls_coarse: str | None = None      # 大类（如 I_文学），仅作套录/规范参考线索，不入 690
    price: str | None = None
    qty: int = 1
    amount: str | None = None
    package: str | None = None
    # ---- 2026-10-07 扩展：手动录入的整套条目 ----
    binding: str | None = None         # 010$b
    parallel_title: str | None = None  # 200$d
    other_author: str | None = None    # 200$g
    language: str | None = None        # 101$a
    edition: str | None = None         # 205$a
    place: str | None = None           # 210$a
    pages: str | None = None           # 215$a
    chart: str | None = None           # 215$c
    size: str | None = None            # 215$d
    attach: str | None = None          # 215$e
    series_part: str | None = None     # 225$h
    series_vol: str | None = None      # 225$v
    class_no: str | None = None        # 690$a
    book_no: str | None = None         # 记录级书次号
    volume: str | None = None          # 记录级卷册号
    shelf_no: str | None = None        # 记录级排架号
    call_no: str | None = None         # 索书号整串（可解析出上面三项）
    barcode: str | None = None         # 馆藏条码
    location: str | None = None        # 馆藏地点
    abstract: str | None = None        # 330$a
    note: str | None = None            # 300$a
    warnings: list[str] = field(default_factory=list)

    @property
    def is_summary_row(self) -> bool:
        """汇总行（如末尾"合计"行）：ISBN 非法且无题名。"""
        return self.isbn is None and not (self.title or "").strip()


def _find_header_row(rows: list[tuple]) -> int:
    """自动定位表头行：真实清单首行多为大标题横幅，取匹配列名最多的行。"""
    best_idx, best_hits = 0, -1
    for i, raw in enumerate(rows[:20]):
        cells = [str(h).strip() if h is not None else "" for h in raw]
        hits = sum(1 for c in cells if c in DELIVERY_COLUMNS)
        if hits > best_hits:
            best_idx, best_hits = i, hits
    return best_idx


def _load_rows(path: str | Path, sheet: str | None = None):
    """打开工作簿，返回 `(工作表名列表, 实际读取的表名, 该表全部行)`。

    返回「实际读取的表名」很关键：未指定 `sheet` 时读的是**活动表**，而多表清单
    常常只导了其中一张 —— 结果里报出台名，用户才知道少读的是哪张（见 `sheet_summary`）。

    行数有硬上限（MAX_SHEET_ROWS）：openpyxl 的 `max_row` 来自工作表 XML 里**可伪造**
    的声明，一个 10MB 的压缩文件可以声明出数千万行空行——`iter_rows` 全量 `list()`
    会把内存吃光。逐行读取到上限即抛错，
    不物化超限数据。
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = list(wb.sheetnames)
    ws = wb[sheet] if sheet else wb.active
    name = ws.title
    rows: list[tuple] = []
    for row in ws.iter_rows(values_only=True):
        rows.append(row)
        if len(rows) >= MAX_SHEET_ROWS:
            wb.close()
            raise ValueError(
                f"工作表「{name}」超过 {MAX_SHEET_ROWS} 行的读取上限，"
                "请拆分文件或核对是否选错了表")
    wb.close()
    return sheets, name, rows


def sheet_summary(path: str | Path) -> list[dict]:
    """每张工作表有多少行（供界面判断"是不是只读了其中一张表"）。

    背景：用户报"明明有几千行，资源库里只有 1000 条"。`read_delivery_list` 只读
    **一张表**（默认活动表），多表清单（有些系统按 1000 条拆表导出）就会少读 ——
    把每张表的行数摆出来，是不是这个原因一眼可见，不用猜。
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: list[dict] = []
    for name in wb.sheetnames:
        try:
            n = wb[name].max_row or 0
        except Exception:
            n = 0
        out.append({"name": name, "max_row": int(n)})
    wb.close()
    return out


def _cell_text(v) -> str:
    return "" if v is None else str(v).strip()


def _keys_from_mapping(header: list[str], mapping: dict | None) -> list[str | None]:
    """列 -> 字段 key。`mapping` 形如 `{"0": "isbn", "2": "title"}`（键是**列下标**，
    字符串或数字都认；值为空串/不在注册表 → 该列不导入）。

    没给 mapping → 退回按列名自动识别（`DELIVERY_COLUMNS`）。
    """
    if not mapping:
        return [DELIVERY_COLUMNS.get(h) for h in header]
    keys: list[str | None] = []
    for i in range(len(header)):
        raw = mapping.get(str(i), mapping.get(i))
        keys.append(raw if raw in _MAPPABLE else None)
    return keys


def read_delivery_list(path: str | Path, sheet: str | None = None,
                       header_row: int | None = None,
                       mapping: dict | None = None) -> list[DeliveryRow]:
    """读取发货清单。自动定位表头行、识别列映射，逐行清洗；汇总行保留并标记。

    - `header_row`：指定表头行下标（不给 → 自动挑匹配列名最多的一行）；
    - `mapping`：**手动列映射**（列下标 -> 字段 key，见 `inspect_delivery()`），
      给了就以它为准，不再按列名猜 —— 这是"书商清单列名五花八门"时的正解。
    """
    _, _, rows = _load_rows(path, sheet)
    return _build_rows(rows, header_row, mapping)


def read_delivery_with_meta(path: str | Path, sheet: str | None = None,
                            header_row: int | None = None,
                            mapping: dict | None = None) -> tuple[list[DeliveryRow], dict]:
    """`read_delivery_list` + **本次读取的元信息**（用哪张表 / 每张表各多少行 / 读了多少行）。

    导入结果要能回答"是不是只读了一张表"（用户 2026-10-07 报"几千行的清单只进了
    1000 条"）。一次开文件把行与元信息一起带出，不再二次读取。
    """
    sheets, cur_sheet, rows = _load_rows(path, sheet)
    out = _build_rows(rows, header_row, mapping)
    return out, {"sheet": cur_sheet, "sheets": sheets,
                 "sheets_info": sheet_summary(path), "data_rows": len(out)}


def _build_rows(rows: list[tuple], header_row: int | None,
                mapping: dict | None) -> list[DeliveryRow]:
    """把原始行按表头行/列映射转成 `DeliveryRow`（`read_delivery_list` 的实现体）。"""
    if not rows:
        return []
    header_idx = header_row if header_row is not None else _find_header_row(rows)
    if not (0 <= header_idx < len(rows)):
        header_idx = 0
    header = [_cell_text(h) for h in rows[header_idx]]
    keys = _keys_from_mapping(header, mapping)
    out: list[DeliveryRow] = []
    for raw in rows[header_idx + 1:]:
        if all(v is None or str(v).strip() == "" for v in raw):
            continue
        row = DeliveryRow()
        for key, val in zip(keys, raw):
            if key is None or val is None:
                continue
            setattr(row, key, str(val).strip())
        _clean_row(row)
        out.append(row)
    return out


def inspect_delivery(path: str | Path, sheet: str | None = None,
                     header_row: int | None = None) -> dict:
    """读取**表结构**供前端做「手动选择映射」：工作表清单、表头行、各列与自动识别结果。

    不写库、不构造记录 —— 只回结构。返回：
      - `sheets`：工作表名列表；`sheet`：当前表名；
      - `header_row`：自动/指定的表头行下标；
      - `columns`：`[{index, name, sample}]`（name = 表头文字，sample = 该列首个非空值）；
      - `mapping`：自动识别的 `{列下标(str): 字段key 或 ""}`；
      - `preview`：表头行之后的前 5 行（原样字符串），界面可对照；
      - `fields`：可映射的目标字段清单 `[{key, label, required, kind}]`。
    """
    sheets, cur_sheet, rows = _load_rows(path, sheet)
    fields = [{"key": f.key, "label": f.label, "required": f.required, "kind": f.kind}
              for f in DELIVERY_FIELDS]
    sheets_info = sheet_summary(path)
    if not rows:
        return {"sheets": sheets, "sheet": cur_sheet, "header_row": 0, "columns": [],
                "mapping": {}, "preview": [], "fields": fields,
                "sheets_info": sheets_info, "data_rows": 0}

    header_idx = header_row if header_row is not None else _find_header_row(rows)
    if not (0 <= header_idx < len(rows)):
        header_idx = 0
    header = [_cell_text(h) for h in rows[header_idx]]
    columns = []
    for i, name in enumerate(header):
        sample = ""
        for raw in rows[header_idx + 1: header_idx + 9]:
            if i < len(raw) and _cell_text(raw[i]):
                sample = _cell_text(raw[i])
                break
        columns.append({"index": i, "name": name, "sample": sample})
    mapping = {str(i): (DELIVERY_COLUMNS.get(name) or "") for i, name in enumerate(header)}
    preview = [[_cell_text(v) for v in raw] for raw in rows[header_idx + 1: header_idx + 6]]
    non_empty = sum(1 for raw in rows[header_idx + 1:]
                    if any(_cell_text(v) for v in raw))
    return {"sheets": sheets, "sheet": cur_sheet, "header_row": header_idx,
            "columns": columns, "mapping": mapping, "preview": preview, "fields": fields,
            # 当前表读到多少数据行 + 每张表各有多少行（"只读了活动表"一眼可见）
            "sheets_info": sheets_info, "data_rows": non_empty}


# 数值型字段：界面单位在 MARC 里要补齐（与简编表单 joinUnit 同口径）
_UNIT_FIELDS: dict[str, tuple[str, str]] = {
    "pages": ("页", "append"),
    "size": ("cm", "append"),
}


def _clean_row(row: DeliveryRow) -> None:
    """行级清洗（规则初拆），告警留在 row.warnings 供 UI 标黄。"""
    # ISBN
    d = isbnlib.normalize(row.isbn or "")
    if row.isbn and d is None:
        row.warnings.append(f"ISBN 无法解析：{row.isbn}")
        row.isbn = None
    elif d is not None:
        if not isbnlib.is_valid(d):
            row.warnings.append(f"ISBN 校验位错误：{d}")
        row.isbn = d
    # 题名拆分（规则初拆）：显式给了副题名/丛书名列就不拆
    if row.title and not row.subtitle and not row.series:
        main, sub, series = cl.split_title(row.title)
        row.title, row.subtitle, row.series = main, sub, series
        if "：" in (row.title or "") or "．" in (row.title or ""):
            row.warnings.append("题名仍含分隔符，建议手动拆分")
    # 责任者
    if row.author:
        names, role = cl.split_author(row.author)
        row.authors, row.role = names, role
    # 出版年月
    if row.year_month:
        row.year = cl.normalize_year(row.year_month)
        if row.year is None:
            row.warnings.append(f"出版年月无法解析：{row.year_month}")
    # 数量
    try:
        row.qty = max(1, int(float(row.qty if isinstance(row.qty, (int, float)) else str(row.qty or 1))))
    except ValueError:
        row.qty = 1
        row.warnings.append(f"数量无法解析，按 1 处理：{row.qty}")
    # 索书号整串 → 拆出 分类号 / 书次号 / 卷册号（**显式列优先**，只补空缺）
    if row.call_no:
        cls, bk, vol = parse_call_no(row.call_no)
        row.class_no = row.class_no or (cls or None)
        row.book_no = row.book_no or (bk or None)
        row.volume = row.volume or vol
    # 数值字段去单位（界面可能带「页」「cm」）
    for key, (unit, pos) in _UNIT_FIELDS.items():
        v = getattr(row, key)
        if v and v.endswith(unit):
            setattr(row, key, v[: -len(unit)].strip())


def missing_required(row: DeliveryRow) -> list[str]:
    """**图书库直落**时缺哪些强制字段（返回中文名列表；空列表 = 可以入库）。

    强制口径（见）：条码号 + 索书号（分类号 + 书次号）+ 正题名 +
    出版社 + 出版年 —— 前两者是图书库的硬规则（条码为核心、索书号上架），
    后三者是质检 ERROR（R0201 / R0202）。**ISBN 不强制**（质检只是 WARNING）。
    """
    missing = []
    if not (row.title or "").strip():
        missing.append("正题名")
    if not (row.publisher or "").strip():
        missing.append("出版社")
    if not (row.year or "").strip():
        missing.append("出版年")
    if not (row.class_no or "").strip():
        missing.append("分类号（或索书号）")
    if not (row.book_no or "").strip():
        missing.append("书次号（或索书号）")
    if not (row.barcode or "").strip():
        missing.append("条码号")
    return missing


def row_extras(row: DeliveryRow) -> dict:
    """记录级 / 馆藏级的散值（不进 MARC，但入库要用）。"""
    return {"class_no": (row.class_no or "").strip(),
            "book_no": (row.book_no or "").strip(),
            "volume": (row.volume or "").strip() or None,
            "shelf_no": (row.shelf_no or "").strip() or None,
            "barcode": (row.barcode or "").strip(),
            "location": (row.location or "").strip() or None}


# 行的标量字段清单（row_to_json / row_from_json 共用）。`authors` / `warnings` 是列表，
# 单独处理；`is_summary_row` 是只读属性，不进来。
_ROW_SCALAR_FIELDS: tuple[str, ...] = (
    "seq", "isbn", "title", "subtitle", "series", "publisher", "author", "role",
    "year_month", "year", "cls_coarse", "price", "qty", "amount", "package",
    "binding", "parallel_title", "other_author", "language", "edition", "place",
    "pages", "chart", "size", "attach", "series_part", "series_vol",
    "class_no", "book_no", "volume", "shelf_no", "call_no", "barcode", "location",
    "abstract", "note",
)


def row_to_json(row: DeliveryRow) -> dict:
    """清洗后的行 → JSON 可存的 dict（**全量字段**）。

    两个用途共用同一形状：
      ① 导入接口的预览返回（前端表格）；
      ② 批量套录任务把**未命中行**留在 `job.result` 里 —— 之后可一键转成资源库
         待编目草稿，不必让用户为了那几行再上传一次清单。
    额外带 `missing_required`（图书库强制项缺项）与 `is_summary`，前端直接用。
    """
    d: dict = {k: getattr(row, k) for k in _ROW_SCALAR_FIELDS}
    d["authors"] = list(row.authors)
    d["warnings"] = list(row.warnings)
    d["missing_required"] = missing_required(row)
    d["is_summary"] = row.is_summary_row
    return d


def row_from_json(d: dict) -> DeliveryRow:
    """`row_to_json()` 的逆：dict → DeliveryRow。

    ⚠️ 数据**已经清洗过**（ISBN 归一、题名拆分、索书号解析…），这里**不重跑
    `_clean_row()`** —— 重跑会把拆分后的题名再拆一次、把已归一的 ISBN 再归一，
    反而可能引入告警。只认注册表里的标量字段，多余键忽略（前向兼容）。
    """
    row = DeliveryRow()
    for k in _ROW_SCALAR_FIELDS:
        v = d.get(k)
        if v is not None:
            setattr(row, k, v)
    row.authors = list(d.get("authors") or [])
    row.warnings = list(d.get("warnings") or [])
    return row


def _field(tag: str, **subs) -> "object":
    from ..core.marc import Field as F
    f = F(tag)
    for code, value in subs.items():
        code = code.lstrip("_")          # _4 -> $4（子字段代码）
        if value is not None and str(value).strip():
            f.add(code, str(value))
    return f


def _marc_subfields(row: DeliveryRow) -> dict[tuple[str, str], str]:
    """把行里**所有已填**的可映射字段收集成 `{(tag, code): 值}`。

    责任者（第一/其他）特殊：`author` 列先经 `split_author` 拆成姓名+责任方式，
    这里用拆出的姓名（与旧行为一致），责任方式走 701/702$4。
    """
    vals: dict[tuple[str, str], str] = {}

    def put(key: str, value):
        f = FIELD_BY_KEY.get(key)
        if f is None or f.kind != "marc" or not (value or "").strip():
            return
        vals[(f.tag, f.code)] = str(value).strip()

    put("isbn", row.isbn)
    put("binding", row.binding)
    put("title", row.title)
    put("parallel_title", row.parallel_title)
    put("subtitle", row.subtitle)
    put("other_author", row.other_author)
    put("language", row.language)
    put("edition", row.edition)
    put("place", row.place)
    put("publisher", row.publisher)
    # 出版年：写**规整后的 4 位年**（`row.year`），不是原始的「2017-09」
    if (row.year or "").strip():
        vals[("210", "d")] = row.year.strip()
    put("pages", row.pages)
    put("chart", row.chart)
    put("size", row.size)
    put("attach", row.attach)
    put("series", row.series)
    put("series_part", row.series_part)
    put("series_vol", row.series_vol)
    put("class_no", row.class_no)
    put("abstract", row.abstract)
    put("note", row.note)
    # 责任者：`author` 列的拆解结果（第一责任者）优先于 200$f 直填
    first = (row.authors[0] if row.authors else (row.author or "")).strip()
    if first:
        vals[("200", "f")] = first
    if row.price:
        vals[("010", "d")] = row.price if _looks_priced(row.price) else f"CNY{row.price}"
    return vals


def _looks_priced(v: str) -> bool:
    """定价已带货币码则原样（`CNY29.80` / `USD12`），裸数字才补 CNY。"""
    import re
    return bool(re.match(r"^[A-Za-z]{2,4}\d", v.strip()))


def row_to_draft_record(row: DeliveryRow) -> Record:
    """把清洗后的清单行转成 CNMARC 记录（**所有已映射字段都填进去**）。

    分类号 `690$a` 不再一律留空：**行里给了分类号就写**（资源库草稿也保留原始
    分类号，便于后续核对）；大类线索仍在 `row.cls_coarse`，不入记录。
    责任者按 CNMARC 惯例另起 701/702（$4 责任方式）。
    """
    rec = Record()
    vals = _marc_subfields(row)
    if not vals:
        return rec
    # 按 tag 聚合子字段；同一 tag 的子字段按注册表声明序（= CNMARC 规范序）排
    order = {(f.tag, f.code): i for i, f in enumerate(DELIVERY_FIELDS)}
    by_tag: dict[str, list[tuple[str, str]]] = {}
    for (tag, code), value in vals.items():
        by_tag.setdefault(tag, []).append((code, value))
    for tag in sorted(by_tag):
        sfs = sorted(by_tag[tag], key=lambda c_v: order.get((tag, c_v[0]), 99))
        # 指示符（ind1/ind2）**不在这里推导**：`_field` 只填子字段，指示符保持默认空格。
        # 交付清单的来源表本来就没有指示符信息，按 tag 猜一个（例如 200→'1'）
        # 只会把"看起来像、其实没依据"的数据写进馆藏，比留空更难查。
        rec.fields.append(_field(tag, **{code: value for code, value in sfs}))
    # 责任者责任方式（701/702$4）
    if row.role and first_author(row):
        rec.fields.append(_field("701", a=first_author(row), _4=row.role))
        for name in row.authors[1:]:
            rec.fields.append(_field("702", a=name, _4=row.role))
    elif len(row.authors) > 1:
        rec.fields.append(_field("701", a=row.authors[0]))
        for name in row.authors[1:]:
            rec.fields.append(_field("702", a=name))
    return rec


def first_author(row: DeliveryRow) -> str | None:
    """该行的第一责任者姓名（拆解后的第一个）。"""
    if row.authors:
        return row.authors[0]
    return (row.author or "").strip() or None
