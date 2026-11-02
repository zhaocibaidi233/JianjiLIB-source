# -*- coding: utf-8 -*-
"""类目表种子脚本：`app/data/clc_min.json` -> `clc_classes` 表。

本产品固定使用 **22 个中图法一级大类**，不提供完整类表导入或细分树。
分类号本身仍按字符串著录，不要求存在于这张参考表。

用法：
    cd backend && python scripts/seed_clc.py
    cd backend && python scripts/seed_clc.py --replace
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import CLCClass  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "app/data/clc_min.json"
_CHUNK = 2000


def _load() -> list[dict]:
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        code = (r.get("class_no") or "").strip()
        if not code:
            continue
        out.append({
            "class_no": code,
            "title": (r.get("title") or code).strip() or code,
            "letter": (r.get("letter") or code[:1].upper()),
            "level": int(r.get("level") or 1),
            "parent_code": (r.get("parent_code") or None),
            "is_alt": bool(r.get("is_alt")),
        })
    return out


def seed(replace: bool = False) -> None:
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.commit()
    import app.models  # noqa: F401
    Base.metadata.create_all(engine)

    classes = _load()
    print(f"类目种子：{DATA}（{len(classes)} 条）")
    db = SessionLocal()
    started = time.time()
    try:
        inserted = 0
        if replace:
            deleted = db.query(CLCClass).delete()
            db.commit()
            print(f"已清空 {deleted} 条旧类目")
            # 分块 bulk_insert_mappings，避免逐条构造 ORM 对象。
            for i in range(0, len(classes), _CHUNK):
                db.bulk_insert_mappings(CLCClass, classes[i:i + _CHUNK],
                                        return_defaults=False)
                db.commit()
            inserted = len(classes)
            print(f"类目表已全量重灌 {inserted} 条，耗时 {time.time() - started:.1f}s")
        else:
            # 增量模式 = **只补缺**（INSERT ... ON CONFLICT DO NOTHING）：表里已有的
            # 类号一律不碰 —— 编目员手工补的类名、套录登记的占位节点都不能被种子覆盖。
            # 旧实现是无条件 bulk_insert_mappings，docstring 却写"增量 upsert（安全）"：
            # 非空表一跑就主键冲突，且分块 commit 崩溃时会留下灌了一半的表。
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            for i in range(0, len(classes), _CHUNK):
                stmt = pg_insert(CLCClass).values(classes[i:i + _CHUNK])
                res = db.execute(stmt.on_conflict_do_nothing(
                    index_elements=[CLCClass.class_no]))
                db.commit()
                inserted += res.rowcount or 0
            print(f"增量补缺 {inserted} 条（已有类号不覆盖），耗时 {time.time() - started:.1f}s")

        total = db.query(CLCClass).count()
        roots = db.query(CLCClass).filter(CLCClass.parent_code.is_(None)).count()
        print(f"表内合计 {total} 条；一级类目 {roots} 个")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="中图法一级大类种子")
    ap.add_argument("--replace", action="store_true",
                    help="先清空再重灌固定 22 个一级大类")
    args = ap.parse_args()
    seed(replace=args.replace)
