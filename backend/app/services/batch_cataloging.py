# -*- coding: utf-8 -*-
"""批量套录：发货清单 -> 逐行 查重 -> 本地资源库 -> Z39.50 -> 自动入库。

命中记录以 status=review 入库（批量来源数据走人工校对，
本地化改写管线），未命中行留给 AI/人工原始编目。
"""
from __future__ import annotations

import tempfile
import os

from ..core import isbn as isbnlib
from ..db import SessionLocal
from ..io.excel import read_delivery_list, row_to_json
from ..models import Job, RecordORM, ResourceLibrary, SourceORM
from ..sources import LocalLibrarySource, SourceConfig, Z3950Source
from ..sources.pg_store import PgResourceStore

# 未命中行保留在 job.result 里的上限。超出的只计数、不带字段（任务结果是一列 JSON，
# 几万行会把它撑爆），并在结果里用 `missed_truncated` 标明，不静默截断。
MISSED_CAP = 2000

# 「一键转待编目草稿」的默认落点资源库名（用户没指定库时自动创建/复用）。
MISSED_DRAFT_LIBRARY_NAME = "套录未命中草稿"


def _missed(row, reason: str) -> dict:
    """一条未命中记录：**整行字段 + 原因**。

    带全量字段是为了「一键转待编目草稿」——未命中的书全部套录源都没查到 MARC，
    需要人工/原始编目，直接拿这一行就能建草稿，不必让用户重传清单。
    """
    entry = row_to_json(row)
    entry["reason"] = reason
    return entry


def _build_sources(db, use_remote: bool) -> tuple[list, list]:
    """返回 (全部源, z3950源)。本地资源库优先级最高。"""
    sources: list = []
    lib_ids = [l.id for l in db.query(ResourceLibrary)
               .filter(ResourceLibrary.enabled.is_(True)).all()]
    if lib_ids:
        sources.append(LocalLibrarySource(
            SourceConfig(type="local", name="本地资源库", priority=0),
            PgResourceStore(SessionLocal, library_ids=lib_ids)))
    remote = []
    if use_remote:
        for orm in db.query(SourceORM).filter(SourceORM.enabled.is_(True),
                                              SourceORM.type == "z3950").all():
            remote.append(Z3950Source(SourceConfig.from_dict(orm.config)))
    sources.extend(remote)
    return sources, remote


def first_hit(sources, isbn: str):
    """按顺序试各源，返回 `(record, 命中的联机源名, 报错的源)`。

    **单源报错不中断**：继续试下一个源。排在前面的源一抖，后面本来能命中的源
    就没机会被查——一行书白跑，还记成"错误"而不是"未命中"。这与 `search_tiered`
    的容错口径一致（那边是"某一层全错才往下一层"，这里是"某一个源错就试下一个"）。
    """
    src_errors: list[str] = []
    for src in sources:
        try:
            cands = src.search(isbn=isbn, limit=3)
        except Exception as e:
            src_errors.append(f"{src.name}: {type(e).__name__}")
            continue
        if cands and cands[0].record is not None:
            hit_remote = None if isinstance(src, LocalLibrarySource) else src.name
            return cands[0].record, hit_remote, src_errors
    return None, None, src_errors


def run_batch_cataloging_job(job_id: int, xlsx_bytes: bytes,
                             use_remote: bool = True) -> None:
    """批量套录（新架构）：命中数据写入套录资源库（联机命中自动回存缓存库），
    图书库（records）只存编目校验好的记录——由编目员从资源库逐条载入校对入库。"""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        job.status, job.progress, job.message = "running", 2, "读取清单"
        db.commit()

        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        with os.fdopen(fd, "wb") as f:
            f.write(xlsx_bytes)
        try:
            rows = read_delivery_list(tmp)
        finally:
            os.unlink(tmp)

        data_rows = [r for r in rows if not r.is_summary_row]
        total = len(data_rows)
        job.message = f"共 {total} 行，构建套录源"
        db.commit()

        sources, remote = _build_sources(db, use_remote)
        from ..services.resource_cache import (get_or_create_online_library,
                                               upsert_resource_record)
        from ..models import ResourceRecordORM
        online_lib = get_or_create_online_library(db) if remote else None

        to_resource, in_cataloging, already, missed, errors = 0, 0, 0, [], []
        seen_forms: set[str] = set()

        for i, row in enumerate(data_rows):
            # 每行一个 SAVEPOINT：下面 `except` 里的 rollback 原本会把**最多 24 条
            # 尚未提交的 upsert 一起回滚**（进度每 25 行才 commit 一次），但
            # to_resource / already / seen_forms 这些计数不回滚 —— 结果里的数字大于
            # 库里真实条数，且被回滚掉的书会以"已入资源库"的身份挡住后续同书行
            # 。用 SAVEPOINT 后失败只废这一行。
            try:
                with db.begin_nested():
                    if not row.isbn:
                        missed.append(_missed(row, "无有效 ISBN"))
                    else:
                        forms = isbnlib.all_forms(row.isbn)
                        if forms & seen_forms:
                            missed.append(_missed(row, "清单内重复"))
                        elif forms and db.query(RecordORM.id).filter(
                                RecordORM.isbn_norm.in_(forms)).first() is not None:
                            in_cataloging += 1            # 图书库已有（编目过）
                            seen_forms |= forms
                        elif forms and db.query(ResourceRecordORM.id).filter(
                                ResourceRecordORM.isbn_norm.in_(forms)).first() is not None:
                            already += 1                  # 资源库已有（之前套录过）
                            seen_forms |= forms
                        else:
                            rec, hit_remote, src_errors = first_hit(sources, row.isbn)
                            if rec is not None:
                                if hit_remote:            # 联机命中 → 回存联机缓存库
                                    upsert_resource_record(db, rec, online_lib.id,
                                                           source_tag=hit_remote)
                                    # 不在这里 commit：SAVEPOINT 已隔离本行，提前
                                    # commit 会把整批未提交的进度一起落库。
                                # 本地资源库命中：记录本来就在资源库表，无需回存
                                to_resource += 1
                                seen_forms |= forms
                            elif src_errors and len(src_errors) == len(sources):
                                # 全部源都报错 ≠ 这本书没有：要分开记，否则编目员会
                                # 以为清单里没有这本书，而实际是网络/目标不可用。
                                missed.append(_missed(
                                    row, "全部源检索失败：" + "；".join(src_errors[:3])))
                            else:
                                missed.append(_missed(row, "全部源未命中"))
            except Exception as e:                     # 单行故障不拖垮整批
                # SAVEPOINT 已随 with 退出回滚，这里**不能再 rollback**：那会把整批
                # 未提交的进度一起丢掉（正是这次要修的问题）。
                errors.append({"row": i, "error": f"{type(e).__name__}: {str(e)[:100]}"})
            # 进度每 25 行落一次（它也负责把本地命中行的 upsert 落库）：独立小事务
            # 过频（旧值 2）只会空转，过疏则进度条长时间不动。
            if (i + 1) % 25 == 0 or i + 1 == total:
                job.progress = 2 + int(96 * (i + 1) / max(total, 1))
                job.message = f"{i + 1}/{total}（入资源库 {to_resource}）"
                db.commit()

        job.status, job.progress = "done", 100
        job.message = "完成"
        job.result = {
            "total": total, "to_resource": to_resource,
            "in_cataloging": in_cataloging, "already_in_resource": already,
            # 未命中行**带全量字段**保留（见 _missed），供「一键转待编目草稿」；
            # 超过 MISSED_CAP 只计数不带字段，并用 missed_truncated 明示，不静默截断。
            "missed": missed[:MISSED_CAP], "missed_count": len(missed),
            "missed_truncated": len(missed) > MISSED_CAP,
            "missed_draft_library": MISSED_DRAFT_LIBRARY_NAME,
            "errors": errors[:50],
            "remote_sources": [s.name for s in remote],
            "note": "命中数据在套录资源库，请到「资源库」页逐条载入编目",
        }
        db.commit()
    except Exception as e:
        db.rollback()
        job = db.get(Job, job_id)
        if job:
            job.status, job.error = "error", str(e)[:800]
            db.commit()
    finally:
        db.close()
