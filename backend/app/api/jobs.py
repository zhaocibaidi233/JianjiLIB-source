# -*- coding: utf-8 -*-
"""后台任务：批量导入提交与进度查询。"""
from __future__ import annotations

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     Request, UploadFile)
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.operator import operator_of
from ..db import get_db
from ..models import Job
from ..services.batch import run_iso_import_job
from ..services.batch_cataloging import run_batch_cataloging_job
from ..core.route_meta import api
from .import_export import read_upload

router = APIRouter(tags=["jobs"])


@router.post("/jobs/import-iso")
@api(module="io")
def submit_iso_import(background: BackgroundTasks, request: Request,
                      file: UploadFile = File(...),
                      source: str = "iso-upload", target: str = "records",
                      library_id: int | None = None,
                      barcode_start: str = "",
                      force: bool = False, mapping: str = "",
                      db: Session = Depends(get_db)):
    """ISO 文件导入：立即返回任务 ID，进度经 GET /jobs/{id} 轮询。

    **`target` 决定落点**（与发货清单路径统一）：
      - `records`（默认）：**图书库**。`library_id` = 图书库 id（不传 → 默认图书库）。
        每条记录都要有条码：记录自带（`905` / `995`）或由 `barcode_start`
        （前缀 + 起始号，如 `A00000001`）生成；都没有的记录**不入库**
        （旧实现不建馆藏，会留下"有条目无条码"的脏记录）。
        `force=true` 可放行质检 ERROR 的记录。
      - `resource`：**资源库**。`library_id` = 资源库 id（不传 → 「手工导入」库），按 ISBN 去重。

    `mapping`：字段映射（JSON 字符串 `{"源 tag$code": "目标 tag$code"}`），
    如 `{"909$b": "905$f"}` —— 把书商数据里我们不认的字段搬到我们的字段上。

    传了不存在的 id → **当场 404**，不会白跑一个必然失败的任务。
    """
    if target not in ("resource", "records"):
        raise HTTPException(400, "target 必须是 resource/records")
    if target == "records":
        # 先校验再入队：坏 id 立刻 404，别让用户等一个注定失败的后台任务。
        from .book_libraries import resolve_library
        resolve_library(db, library_id)
    elif library_id:
        from ..models import ResourceLibrary
        if db.get(ResourceLibrary, library_id) is None:
            raise HTTPException(404, f"资源库不存在：{library_id}")

    start = (barcode_start or "").strip()
    if start:
        from ..services.batch import make_barcode_allocator
        try:
            make_barcode_allocator(start)             # 起始号非法 → 当场 400
        except ValueError as e:
            raise HTTPException(400, str(e))

    fmap = None
    if (mapping or "").strip():
        import json
        try:
            fmap = json.loads(mapping)
            if not isinstance(fmap, dict):
                raise ValueError
        except Exception:
            raise HTTPException(400, "mapping 必须是 JSON 对象（源 tag$code -> 目标 tag$code）")

    data = read_upload(file)
    # 署名在**提交时**取一次就存进任务参数：后台任务跑起来时请求已经结束，
    # `request.state` 早就没了（的"这批是谁导的"就靠这一句）。
    who = operator_of(request)
    job = Job(type="import-iso", params={"filename": file.filename, "source": source,
                                        "target": target, "library_id": library_id,
                                        "barcode_start": start or None,
                                        "operator": who,
                                        "mapping": fmap or None})
    db.add(job)
    db.commit()
    db.refresh(job)
    background.add_task(run_iso_import_job, job.id, data, source, library_id,
                        target, start or None, force, fmap, who)
    return {"job_id": job.id, "status": job.status}


@router.post("/jobs/batch-cataloging")
@api(module="io")
def submit_batch_cataloging(background: BackgroundTasks, file: UploadFile = File(...),
                            use_remote: bool = True, db: Session = Depends(get_db)):
    """批量套录：发货清单逐行 查重→本地资源库→Z39.50→自动入库（status=review 待校对）。"""
    data = read_upload(file)
    job = Job(type="batch-cataloging",
              params={"filename": file.filename, "use_remote": use_remote})
    db.add(job)
    db.commit()
    db.refresh(job)
    background.add_task(run_batch_cataloging_job, job.id, data, use_remote)
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs")
@api(module="io")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(Job.id.desc()).limit(20).all()
    return [{"id": j.id, "type": j.type, "status": j.status, "progress": j.progress,
             "message": j.message, "result": j.result, "error": j.error,
             "created_at": str(j.created_at)} for j in jobs]


@router.post("/jobs/{job_id}/missed-to-draft")
@api(module="io")
def missed_to_draft(job_id: int, library_id: int | None = None,
                    db: Session = Depends(get_db)):
    """把某次「批量套录」的**未命中行**转成资源库待编目草稿。

    未命中 = 全部套录源都没查到 MARC，这些书要人工/AI 原始编目。任务结果里已经带着
    这些行的**完整字段**（见 `batch_cataloging._missed`），所以这里不必再让用户上传
    一次清单。落到资源库后，编目员在「资源库」页逐条「载入编目」即可。

    - 未传 `library_id` → 落到「套录未命中草稿」库（缺则自动创建）；
    - 传了不存在的 id → **404**；
    - 按 ISBN upsert：**重复点击不会翻倍**（同 ISBN 走更新）。
    """
    from ..io.excel import row_from_json, row_to_draft_record
    from ..models import ResourceLibrary, ResourceRecordORM
    from ..services.batch_cataloging import MISSED_DRAFT_LIBRARY_NAME
    from ..services.resource_cache import upsert_resource_record

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.type != "batch-cataloging":
        raise HTTPException(400, "该任务不是「批量套录」，没有未命中清单")
    if job.status != "done":
        raise HTTPException(400, f"任务尚未完成（当前 {job.status}），完成后才能转草稿")
    rows = ((job.result or {}).get("missed") or [])
    if not rows:
        raise HTTPException(400, "本次套录没有未命中行，无需转草稿")

    if library_id:
        lib = db.get(ResourceLibrary, library_id)
        if lib is None:
            raise HTTPException(404, f"资源库不存在：{library_id}")
    else:
        lib = (db.query(ResourceLibrary)
               .filter(ResourceLibrary.name == MISSED_DRAFT_LIBRARY_NAME).first())
        if lib is None:
            lib = ResourceLibrary(name=MISSED_DRAFT_LIBRARY_NAME,
                                  origin="批量套录未命中行生成的待编目草稿", enabled=True)
            db.add(lib)
            db.flush()

    added = updated = skipped = 0
    errors = []
    for index, d in enumerate(rows, 1):
        try:
            # 包括 JSON 还原与 MARC 构造；异常只回滚此行，不撤销新库与前面的成功行。
            with db.begin_nested():
                row = row_from_json(d)
                if not (row.title or "").strip():
                    skipped += 1
                    continue
                rec = row_to_draft_record(row)
                action = upsert_resource_record(db, rec, lib.id, source_tag="套录未命中")
                db.flush()
        except Exception as exc:
            # 连接中断、死锁等操作故障不应伪装成坏行。
            if isinstance(exc, SQLAlchemyError) and not isinstance(exc, (DataError, IntegrityError)):
                raise
            skipped += 1
            errors.append({"row": index, "reason": str(exc)})
            continue
        if action == "updated":
            updated += 1
        else:
            added += 1
    db.flush()
    # record_count 以表内实况为准（与其它导入路径同口径）
    lib.record_count = (db.query(ResourceRecordORM)
                        .filter(ResourceRecordORM.library_id == lib.id).count())
    db.commit()
    return {"library": lib.name, "library_id": lib.id,
            "imported": added, "updated": updated, "skipped": skipped,
            "errors": errors[:50], "error_count": len(errors),
            "record_count": lib.record_count}


@router.get("/jobs/{job_id}")
@api(module="io")
def get_job(job_id: int, db: Session = Depends(get_db)):
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "任务不存在")
    return {"id": j.id, "type": j.type, "status": j.status, "progress": j.progress,
            "message": j.message, "result": j.result, "error": j.error}
