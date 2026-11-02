# -*- coding: utf-8 -*-
"""套录源管理（可编辑配置）+ 资源库管理。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_db, like_escape
from ..models import ResourceLibrary, ResourceRecordORM, SourceORM
from ..io.iso import read_iso
from ..sources import PgExternalSource, SourceConfig, Z3950Source
from ..sources.z3950 import validate_pre_commands
from .import_export import read_upload
from ..services.sorting import apply_sort


def _validate_source_extra(cfg: SourceConfig) -> None:
    """保存源配置前的合法性校验：z39.5 的 pre_commands 不允许 shell 逃逸与
    open/auth（会覆盖会话目标与凭据，见 sources/z3950.validate_pre_commands）。
    保存时就拒绝，别等第一次检索才在会话日志里失败。"""
    if cfg.type == "z3950":
        try:
            validate_pre_commands(cfg.extra)
        except ValueError as e:
            raise HTTPException(400, str(e))
from ..services.paging import paginate
from ..core.route_meta import api
from ..schemas import ResourceRecordPage

router = APIRouter(tags=["sources"])

# 资源库浏览**可排序的列**（白名单，见 `services/sorting.py`）。键与前端列的 `prop` 对齐。
RESOURCE_SORTS = {
    "id": ResourceRecordORM.id,
    "title": ResourceRecordORM.title_norm,
    "isbn": ResourceRecordORM.isbn_norm,
    "author": ResourceRecordORM.author_norm,
    "year": ResourceRecordORM.year,
    "class_no": ResourceRecordORM.class_no,
    "source_tag": ResourceRecordORM.source_tag,
    "updated_at": ResourceRecordORM.updated_at,
}


class ResourceLibraryEnabledIn(BaseModel):
    enabled: bool


class SourceIn(BaseModel):
    type: str                      # local / z3950
    name: str
    host: str = ""
    port: int = 2100
    database: str = ""
    user: str = ""
    password: str = ""
    charset: str = "gbk"
    record_syntax: str = "unimarc"
    priority: int = 100
    enabled: bool = True
    extra: dict = {}


def _cfg_to_dict(cfg: SourceConfig) -> dict:
    return cfg.to_dict()


# 口令脱敏占位符：列表接口不返回明文；编辑表单回传该值表示"不修改口令"
PASSWORD_MASK = "***"


def _mask_config(cfg: dict | None) -> dict:
    """列表输出中对套录源口令做脱敏处理。"""
    if not isinstance(cfg, dict):
        return cfg or {}
    out = dict(cfg)
    if out.get("password"):
        out["password"] = PASSWORD_MASK
    return out


@router.get("/sources")
@api(module="settings")
def list_sources(db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "type": s.type, "enabled": s.enabled,
             "config": _mask_config(s.config),
             "last_ok_at": str(s.last_ok_at) if s.last_ok_at else None,
             "last_fail_at": str(s.last_fail_at) if s.last_fail_at else None}
            for s in db.query(SourceORM).order_by(SourceORM.id).all()]


@router.get("/sources/libraries")
@api(module="settings")
def list_source_libraries(db: Session = Depends(get_db)):
    """设置页的资源库清单：本地资源库的启停按 settings 模块授权。"""
    return _resource_library_rows(db)


@router.put("/sources/libraries/{library_id}")
@api(module="settings")
def set_source_library_enabled(library_id: int, payload: ResourceLibraryEnabledIn,
                               db: Session = Depends(get_db)):
    """停用后不再参与编目套录与批量套录；记录本身保留，可随时恢复。"""
    lib = db.get(ResourceLibrary, library_id)
    if lib is None:
        raise HTTPException(404, "资源库不存在")
    lib.enabled = payload.enabled
    db.commit()
    db.refresh(lib)
    return {"id": lib.id, "enabled": lib.enabled}


@router.post("/sources")
@api(module="settings")
def create_source(payload: SourceIn, db: Session = Depends(get_db)):
    if db.query(SourceORM).filter(SourceORM.name == payload.name).first():
        raise HTTPException(409, "同名源已存在")
    cfg = SourceConfig(**payload.model_dump())
    _validate_source_extra(cfg)
    orm = SourceORM(name=cfg.name, type=cfg.type, config=_cfg_to_dict(cfg), enabled=cfg.enabled)
    db.add(orm)
    db.commit()
    db.refresh(orm)
    return {"id": orm.id, "name": orm.name}


@router.put("/sources/{source_id}")
@api(module="settings")
def update_source(source_id: int, payload: SourceIn, db: Session = Depends(get_db)):
    orm = db.get(SourceORM, source_id)
    if orm is None:
        raise HTTPException(404, "源不存在")
    data = payload.model_dump()
    # 前端未重填口令时会回传脱敏占位符，保留库中原值，避免被 "***" 覆盖
    if data.get("password") == PASSWORD_MASK:
        data["password"] = (orm.config or {}).get("password", "")
    cfg = SourceConfig(**data)
    _validate_source_extra(cfg)
    orm.name, orm.type, orm.config, orm.enabled = cfg.name, cfg.type, _cfg_to_dict(cfg), cfg.enabled
    db.commit()
    return {"id": orm.id, "name": orm.name}


@router.delete("/sources/{source_id}")
@api(module="settings")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    orm = db.get(SourceORM, source_id)
    if orm is None:
        raise HTTPException(404, "源不存在")
    db.delete(orm)
    db.commit()
    return {"deleted": source_id}


@router.post("/sources/{source_id}/test")
@api(module="settings")
def test_source(source_id: int, db: Session = Depends(get_db)):
    """源探活：z3950 发起无害检索；pg 连库探活；local 返回资源量。"""
    orm = db.get(SourceORM, source_id)
    if orm is None:
        raise HTTPException(404, "源不存在")
    cfg = SourceConfig.from_dict(orm.config)
    if cfg.type == "z3950":
        result = Z3950Source(cfg).health()
        _stamp(db, orm, result["status"] == "ok")
        return result
    if cfg.type == "pg":
        result = PgExternalSource(cfg).health()
        _stamp(db, orm, result["status"] == "ok")
        return result
    count = db.query(ResourceRecordORM).count()
    return {"name": cfg.name, "type": "local", "status": "ok", "records": count}


class PgProbeIn(BaseModel):
    """探测外部 PostgreSQL 库的连接参数（表/列映射的填写依据）。

    `source_id`：编辑已有源时，前端密码框里是脱敏占位符 `***`，此时回落到库里
    已存的口令 —— 否则用户只是"点一下探测"就会因为占位符而当口令错误。

    字段名用 `schema_name` 而非 `schema`：后者与 Pydantic 的 `BaseModel.schema()`
    撞名，会触发 UserWarning（v1 遗留方法）。
    """
    host: str = "localhost"
    port: int = 5432
    database: str = ""
    user: str = ""
    password: str = ""
    sslmode: str = ""
    schema_name: str | None = None
    table: str | None = None
    source_id: int | None = None


def _scrub_password(msg: str, password: str) -> str:
    """把消息里可能回显的口令抹掉（某些驱动会把连接串带进异常文本）。

    ⚠️ 只对**足够长**的口令整串替换。短口令会命中正常单词里的字母 ——
    实测口令 `p` 把 `psycopg2.OperationalError` 糊成 `***syco***g2`、
    `port` 糊成 `***ort`，报错直接读不懂（脱敏脱过头比不脱更糟）。
    阈值与 `sources.pg_external._MIN_SCRUB_LEN` 同口径。
    """
    from ..sources.pg_external import _MIN_SCRUB_LEN
    if not password or len(password) < _MIN_SCRUB_LEN:
        return msg
    return msg.replace(password, "***")


@router.post("/sources/pg/inspect")
@api(module="settings")
def inspect_pg(payload: PgProbeIn, db: Session = Depends(get_db)):
    """探测外部 PostgreSQL 库：连通性 + 表清单（+ 指定表的列清单）。

    给「手动指定数据库」这件事一个**看得见的目标** —— 不探测的话，用户只能盲填
    模式名/表名/列名，填错了要到套录时才报错（还容易被误当成"这本书没有"）。
    只读：只查 `information_schema`。

    本端点要求「全局设置」写权限。请只把该权限授予可信管理员；
    如需限制服务器可访问的地址范围，请在网络层只放行实际使用的目标地址与端口。
    """
    from sqlalchemy import create_engine, text as sqltext

    from ..sources.pg_external import build_url

    password = payload.password
    if password == PASSWORD_MASK and payload.source_id is not None:
        orm = db.get(SourceORM, payload.source_id)
        if orm is not None:
            password = (orm.config or {}).get("password", "")
    url = build_url(payload.host, payload.port, payload.database,
                    payload.user, password, payload.sslmode)
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            server = conn.execute(sqltext("SELECT version()")).scalar()
            tables = [{"schema": s, "table": t} for s, t in conn.execute(sqltext(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name")).all()]
            columns = []
            if payload.schema_name and payload.table:
                columns = [{"name": n, "type": d, "nullable": (nb == "YES")}
                           for n, d, nb in conn.execute(sqltext(
                               "SELECT column_name, data_type, is_nullable "
                               "FROM information_schema.columns "
                               "WHERE table_schema = :s AND table_name = :t "
                               "ORDER BY ordinal_position"),
                               {"s": payload.schema_name, "t": payload.table}).all()]
    except Exception as e:
        raise HTTPException(400, f"连接失败：{_scrub_password(str(e).strip(), password)}")
    finally:
        engine.dispose()
    return {"ok": True, "server": (server or "")[:120], "tables": tables,
            "columns": columns}


def _stamp(db: Session, orm: SourceORM, ok: bool):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if ok:
        orm.last_ok_at = now
    else:
        orm.last_fail_at = now
    db.commit()


# ---------- 资源库 ----------

def _resource_library_rows(db: Session) -> list[dict]:
    """资源库清单。`record_count` 以表内实况重算，避免冗余列漂移。"""
    from sqlalchemy import func
    counts = dict(db.query(ResourceRecordORM.library_id,
                           func.count(ResourceRecordORM.id))
                  .group_by(ResourceRecordORM.library_id).all())
    return [{"id": l.id, "name": l.name, "origin": l.origin,
             "record_count": counts.get(l.id, 0), "enabled": l.enabled}
            for l in db.query(ResourceLibrary).order_by(ResourceLibrary.id).all()]


@router.get("/resource-libraries")
@api(module="resources")
def list_libraries(db: Session = Depends(get_db)):
    """资源库清单（资源库页使用）。"""
    return _resource_library_rows(db)


@router.post("/resource-libraries")
@api(module="resources")
def create_library(name: str, origin: str = "", db: Session = Depends(get_db)):
    if db.query(ResourceLibrary).filter(ResourceLibrary.name == name).first():
        raise HTTPException(409, "同名资源库已存在")
    lib = ResourceLibrary(name=name, origin=origin)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return {"id": lib.id, "name": lib.name}


@router.post("/resource-libraries/{library_id}/import-iso")
@api(module="resources")
def import_library_iso(library_id: int, file: UploadFile = File(...),
                       db: Session = Depends(get_db)):
    """ISO 批次导入资源库（按库内归一 ISBN upsert 去重；原样保留 9xx 私有字段，
    本地化改写发生在套录使用时）。

    实现走 `services.resource_cache.import_iso_records_to_library()` —— 与 `/import/iso?target=resource`
    和后台任务**同一份代码**（旧实现各自写了一遍，且逐条 db.add 不查重，同一批次
    重复导入整套翻倍，与 /import/iso 修复前的事故同源）。
    """
    lib = db.get(ResourceLibrary, library_id)
    if lib is None:
        raise HTTPException(404, "资源库不存在")
    from ..services.resource_cache import import_iso_records_to_library
    records, parse_errors = read_iso(read_upload(file))
    result = import_iso_records_to_library(db, records, library_id,
                                           source_tag="ISO 批次导入")
    db.commit()
    return {"library": result["library"], "added": result["added"],
            "updated": result["updated"], "parse_errors": parse_errors[:10]}


@router.get("/resource-libraries/{library_id}/records", response_model=ResourceRecordPage)
@api(module="resources")
def browse_library_records(library_id: int, q: str | None = None,
                           source_tag: str | None = None, has_isbn: bool | None = None,
                           class_no: str | None = None, year: int | None = None,
                           page: int = 1, page_size: int = 20,
                           sort: str | None = None, order: str | None = None,
                           db: Session = Depends(get_db)):
    """浏览资源库记录（分页 + 常用筛选：关键词 / 来源 / 有无 ISBN / 分类号前缀 / 出版年）。

    筛选口径与 `POST /resource-records/bulk-delete {filter}` **共用 `_resource_query`**
    ——「看到的」与「按筛选删掉的」必须一致。
    `sort` / `order`：点击表头排序（白名单见 `RESOURCE_SORTS`，白名单外回退 `id desc`）。
    """
    query = _resource_query(db, library_id, q=q, source_tag=source_tag,
                            has_isbn=has_isbn, class_no=class_no, year=year)
    total, rows = paginate(apply_sort(query, RESOURCE_SORTS, sort, order,
                                      default="id", default_order="desc",
                                      tiebreak=ResourceRecordORM.id),
                           page, page_size)
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{"id": r.id, "title": r.title_norm, "isbn": r.isbn_norm,
                       "author": r.author_norm, "year": r.year, "class_no": r.class_no,
                       "source_tag": r.source_tag,
                       "updated_at": str(r.updated_at) if r.updated_at else None}
                      for r in rows]}


def _resource_query(db: Session, library_id: int, *, q: str | None = None,
                    source_tag: str | None = None, has_isbn: bool | None = None,
                    class_no: str | None = None, year: int | None = None):
    """资源库记录筛选的**唯一真源**（列表与「按筛选批量删除」共用）。"""
    query = db.query(ResourceRecordORM).filter(ResourceRecordORM.library_id == library_id)
    if source_tag:
        query = query.filter(ResourceRecordORM.source_tag == source_tag)
    if has_isbn is True:
        query = query.filter(ResourceRecordORM.isbn_norm.isnot(None),
                             ResourceRecordORM.isbn_norm != "")
    elif has_isbn is False:
        query = query.filter(or_(ResourceRecordORM.isbn_norm.is_(None),
                                 ResourceRecordORM.isbn_norm == ""))
    if class_no:
        # 分类号按前缀匹配（I247 / K825 是实际筛选习惯）
        query = query.filter(ResourceRecordORM.class_no.ilike(
            f"{like_escape(class_no.strip())}%", escape="\\"))
    if year:
        query = query.filter(ResourceRecordORM.year == year)
    if q:
        q = q.strip()
        d = "".join(ch for ch in q if ch.isdigit()) or q
        query = query.filter(ResourceRecordORM.title_norm.ilike(f"%{like_escape(q)}%", escape="\\")
                             | ResourceRecordORM.isbn_norm.ilike(f"%{like_escape(d)}%", escape="\\"))
    return query


@router.get("/resource-libraries/{library_id}/facets")
@api(module="resources")
def library_facets(library_id: int, db: Session = Depends(get_db)):
    """资源库的筛选项候选值：来源标签（带条数）与出版年区间。

    只回候选值，不回记录 —— 列表页每次翻页都要用，但候选值几乎不变。
    """
    from sqlalchemy import func
    lib = db.get(ResourceLibrary, library_id)
    if lib is None:
        raise HTTPException(404, "资源库不存在")
    tags = [{"value": v, "count": c} for v, c in
            db.query(ResourceRecordORM.source_tag, func.count(ResourceRecordORM.id))
            .filter(ResourceRecordORM.library_id == library_id,
                    ResourceRecordORM.source_tag.isnot(None))
            .group_by(ResourceRecordORM.source_tag)
            .order_by(func.count(ResourceRecordORM.id).desc()).all()]
    years = [y for (y,) in
             db.query(ResourceRecordORM.year)
             .filter(ResourceRecordORM.library_id == library_id,
                     ResourceRecordORM.year.isnot(None))
             .distinct().order_by(ResourceRecordORM.year.desc()).limit(60).all()]
    return {"source_tags": tags, "years": years,
            "total": db.query(ResourceRecordORM)
            .filter(ResourceRecordORM.library_id == library_id).count()}


@router.get("/resource-records/{record_id}")
@api(module="resources")
def get_resource_record(record_id: int, db: Session = Depends(get_db)):
    """资源库完整记录（载入编辑器用）。"""
    from ..services import orm_to_record
    orm = db.get(ResourceRecordORM, record_id)
    if orm is None:
        raise HTTPException(404, "资源库记录不存在")
    rec = orm_to_record(orm)
    return {"id": orm.id, "library_id": orm.library_id,
            "record": {"leader": rec.leader, "fields": json.loads(rec.to_json())}}


@router.delete("/resource-records/{record_id}")
@api(module="resources")
def delete_resource_record(record_id: int, db: Session = Depends(get_db)):
    from ..services.resource_cache import bump_library_count
    orm = db.get(ResourceRecordORM, record_id)
    if orm is None:
        raise HTTPException(404, "资源库记录不存在")
    bump_library_count(db, orm.library_id, -1)
    db.delete(orm)
    db.commit()
    return {"deleted": record_id}


class IdsIn(BaseModel):
    ids: list[int] = []


class ResourceFilterIn(BaseModel):
    """资源库记录筛选条件（与浏览页的输入一一对应）。"""
    q: str | None = None
    source_tag: str | None = None
    has_isbn: bool | None = None
    class_no: str | None = None
    year: int | None = None


class ResourceDeleteIn(BaseModel):
    """批量删除载荷：`ids` 勾选删，或 `filter` + `expected_total` 按筛选整批删。

    `library_id`：**按筛选删时必须给**（删哪个库要写在请求里，不从 id 反推 ——
    一条都没有时反推不出库）。
    """
    ids: list[int] = []
    library_id: int | None = None
    filter: ResourceFilterIn | None = None
    expected_total: int | None = None


# 单次按筛选删除上限（与图书库同口径）
MAX_BULK_DELETE = 20000


def _resync_library_count(db: Session, library_id: int) -> None:
    """把某个资源库的 record_count 按表内实况重算。

    与 `list_libraries()` 口径一致（列表本来就是实时聚合的，冗余列只是给别处读）。
    批量删除后重算一次，避免冗余列与实况越差越远。
    """
    from sqlalchemy import func
    lib = db.get(ResourceLibrary, library_id)
    if lib is None:
        return
    lib.record_count = (db.query(func.count(ResourceRecordORM.id))
                        .filter(ResourceRecordORM.library_id == library_id)
                        .scalar() or 0)


def _delete_resource_rows(db: Session, rows: list, lib_ids: set[int]) -> int:
    for r in rows:
        db.delete(r)
    db.commit()
    for lid in lib_ids:
        _resync_library_count(db, lid)
    db.commit()
    return len(rows)


@router.post("/resource-records/bulk-delete")
@api(module="resources")
def bulk_delete_resource_records(payload: ResourceDeleteIn, db: Session = Depends(get_db)):
    """**批量删除**资源库记录。

    两种用法：
      - `ids`：勾选删除（幂等：不存在的 id 忽略、重复提交不报错）；
      - `filter` + `expected_total`：**按当前筛选整批删**（清掉一次错误导入的整批原料），
        条数不一致 → 409 拒绝（防筛选条件拼错删过头）。
    """
    if payload.filter is not None:
        lib_id = payload.library_id
        if lib_id is None:
            raise HTTPException(400, "按筛选删除需要指定 library_id")
        if payload.expected_total is None:
            raise HTTPException(400, "按筛选删除必须回传 expected_total（页面上显示的条数）")
        query = _resource_query(db, lib_id, **payload.filter.model_dump())
        matched = query.count()
        if matched != payload.expected_total:
            raise HTTPException(409, (
                f"筛选结果与预期不一致（页面显示 {payload.expected_total} 条，"
                f"当前实际 {matched} 条）。数据已发生变化，请刷新后重试。"))
        if matched == 0:
            return {"deleted": 0, "total_matched": 0}
        if matched > MAX_BULK_DELETE:
            raise HTTPException(400, f"单次最多删除 {MAX_BULK_DELETE} 条，请缩小筛选范围后分批删除")
        rows = query.all()
        return {"deleted": _delete_resource_rows(db, rows, {lib_id}),
                "total_matched": matched}

    if not payload.ids:
        raise HTTPException(400, "请提供要删除的记录 id")
    rows = (db.query(ResourceRecordORM)
            .filter(ResourceRecordORM.id.in_(payload.ids)).all())
    lib_ids = {r.library_id for r in rows}
    return {"deleted": _delete_resource_rows(db, rows, lib_ids),
            "total_matched": len(payload.ids)}


@router.post("/resource-libraries/{library_id}/clear")
@api(module="resources")
def clear_library(library_id: int, db: Session = Depends(get_db)):
    lib = db.get(ResourceLibrary, library_id)
    if lib is None:
        raise HTTPException(404, "资源库不存在")
    n = db.query(ResourceRecordORM).filter(ResourceRecordORM.library_id == library_id).count()
    db.query(ResourceRecordORM).filter(ResourceRecordORM.library_id == library_id).delete()
    lib.record_count = 0
    db.commit()
    return {"cleared": n}


@router.delete("/resource-libraries/{library_id}")
@api(module="resources")
def delete_library(library_id: int, db: Session = Depends(get_db)):
    lib = db.get(ResourceLibrary, library_id)
    if lib is None:
        raise HTTPException(404, "资源库不存在")
    if lib.name == "联机套录下载":
        raise HTTPException(400, "联机套录下载缓存库不可删除（可清空）")
    n = (db.query(ResourceRecordORM)
         .filter(ResourceRecordORM.library_id == library_id).count())
    db.query(ResourceRecordORM).filter(ResourceRecordORM.library_id == library_id).delete()
    db.delete(lib)
    db.commit()
    return {"deleted": library_id, "records_removed": n}
