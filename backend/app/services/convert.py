# -*- coding: utf-8 -*-
"""ORM ↔ core.Record 转换与检索列同步。"""
from __future__ import annotations

import json

from ..core import clean as cl
from ..core import isbn as isbnlib
from ..core import pinyin as pyl
from ..core.marc import Field, Record, Subfield, record_to_iso
from ..core.volume import normalize_volume_key


def compute_search_columns(rec: Record) -> dict:
    """检索冗余列（写入时生成，规则集中在 core，见）。"""
    # 完整题名 = 正题名 + 全部层级的分辑名（200$i，按字段内顺序）。
    # 复用 Record.title()（口径唯一真源，含多级 $i 拼接规则与注释），
    # 不在这里重写一遍——两处规则迟早漂移（2026-10-30 教训：first_value 漏了
    # 第二层 $i，《学汉语分级读物》3 条单册名全部撞车）。
    title_norm = cl.normalize_title(rec.title() or "")
    year_raw = rec.first_value("210", "d") or ""
    year = cl.normalize_year(year_raw)
    author_raw = rec.first_value("200", "f") or ""
    names, _role = cl.split_author(author_raw) if author_raw else ([], None)
    return {
        # 统一为 ISBN-13 形态（可转换时），避免同一本书跨形态重复入库
        "isbn_norm": isbnlib.canonical(rec.first_value("010", "a") or ""),
        "title_norm": title_norm or None,
        "title_pinyin": pyl.to_pinyin(title_norm) or None,
        "title_pinyin_initials": pyl.to_initials(title_norm) or None,
        "author_norm": " ".join(names) or None,
        "year": int(year) if year else None,
        "class_no": (rec.first_value("690", "a") or "").strip() or None,
    }


def compute_volume_key(rec: Record) -> str | None:
    """200$h/$i（全部层级）→ records.volume_key；不混入 200$v。"""
    numbers = [v.strip() for v in rec.all_values("200", "h") if (v or "").strip()]
    titles = [v.strip() for v in rec.all_values("200", "i") if (v or "").strip()]
    if not numbers and not titles:
        return None
    return normalize_volume_key(
        "/".join(numbers) if numbers else None,
        "/".join(titles) if titles else None,
    )


def record_to_orm_values(rec: Record, source: str = "local") -> dict:
    """core.Record -> ORM 字段值（含 marc_raw 与检索列）。字段超长抛 MarcWriteError。"""
    values = {
        "marc_raw": record_to_iso(rec, encoding="gbk"),
        "leader": rec.leader,
        "fields": json.loads(rec.to_json()),
        "source": source,
    }
    values.update(compute_search_columns(rec))
    values["volume_key"] = compute_volume_key(rec)
    return values


def orm_to_record(orm) -> Record:
    rec = Record(leader=orm.leader or " " * 24)
    for item in (orm.fields or []):
        if "sf" in item or "i1" in item:
            rec.fields.append(Field(item["tag"], item.get("i1", " "), item.get("i2", " "),
                                    item.get("data", ""),
                                    [Subfield(c, v) for c, v in item.get("sf", [])]))
        else:
            rec.fields.append(Field(item["tag"], data=item.get("data", "")))
    return rec


def record_from_payload(payload: dict) -> Record:
    """API 请求体 -> core.Record。载荷形态与 Record.to_json() 一致：
    {"leader": "...?", "fields": [{"tag","data"} | {"tag","i1","i2","sf":[[code,value],...]}]}
    """
    rec = Record(leader=payload.get("leader") or " " * 24)
    for item in payload.get("fields", []):
        tag = item.get("tag", "")
        if "sf" in item or "i1" in item:
            rec.fields.append(Field(tag, item.get("i1", " "), item.get("i2", " "),
                                    item.get("data", ""),
                                    [Subfield(str(c), str(v)) for c, v in item.get("sf", [])]))
        else:
            rec.fields.append(Field(tag, data=str(item.get("data", ""))))
    return rec
