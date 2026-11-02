# -*- coding: utf-8 -*-
"""简籍（Jianji）—— MCP 服务器（stdio 传输）。

把本项目的 REST API 包成一组 MCP 工具，让支持 MCP 的 agent 软件
（Claude Desktop / Cursor / Cherry Studio 等）能直接查书目、导入导出 ISO 与 Excel。

**架构：stdio MCP 服务器 + 薄 HTTP 客户端，调用本项目既有的 REST API。**

选它而不是「直连数据库 / 直接调服务层」的三个理由：

1. **权限校验完全复用既有中间件**：请求照常穿过 ``backend/app/main.py`` 的
   ``AccessTokenMiddleware`` + ``AuthMiddleware``，以及 ``backend/app/core/perm.py``
   的模块权限表。MCP 侧不需要第二套鉴权，也不会绕过任何一道闸门。
2. **不复制业务逻辑**：导入去重、字段映射、质检、后台任务都在服务层实现；
   MCP 只做参数搬运，业务规则只有一份，不会和 Web 界面走岔。
3. **服务器和 agent 可以不在同一台机器**：MCP 进程只要能访问
   ``JIANJI_API_URL`` 即可，不必和后端同机、也不必碰数据库。

配置（全部走环境变量，没有配置文件）：

``JIANJI_API_URL``
    后端地址，默认 ``http://127.0.0.1:8000``。
``JIANJI_API_TOKEN``
    凭据。给了就**同时**发 ``X-Access-Token: <token>`` 与
    ``Authorization: Bearer <token>`` 两个头 —— 前者是部署级共享密钥
    （``JIANJI_ACCESS_TOKEN``）的通道，后者是登录令牌的通道
    （见 ``backend/app/services/auth.py`` 的 ``token_from_request()``）。
    两个都发，用户给哪种凭据都能用。不给则两个头都不发（登录未启用的
    单机部署无需凭据）。
``JIANJI_MCP_TIMEOUT``
    单次 HTTP 请求超时秒数，默认 120。整库导入/导出慢时可调大。

错误处理：后端返回 4xx/5xx 时，把响应体 ``detail`` 原文抛成 ``ToolError``
（例如「未登录或登录已过期，请重新登录」「该角色对『导入导出』模块没有写权限」），
agent 能直接看到后端的中文原因，而不是一句干巴巴的 "HTTP 403"。

启动方式见 ``backend/start-mcp.bat``。
"""
import json
import os
import re
import sys
from pathlib import Path

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 120.0

# 导出类工具把字节写到本地磁盘，返回这个提示语，避免把二进制塞进对话。
_FILENAME_RE = re.compile(r'filename="?([^";]+)"?')


# --------------------------------------------------------------------------
# 配置与 HTTP 层
# --------------------------------------------------------------------------
def _base_url() -> str:
    """后端地址（每次调用都读环境变量，方便运行中改配置与测试注入）。"""
    return (os.environ.get("JIANJI_API_URL") or DEFAULT_API_URL).strip().rstrip("/")


def _headers() -> dict:
    """构造请求头。

    **两个头都发**是刻意的：``X-Access-Token`` 走部署级共享密钥通道，
    ``Authorization: Bearer`` 走登录令牌通道。只发前者时，登录令牌会被拿去
    和共享密钥比对从而误判 401（``前端实现`` 里踩过同一个坑）。
    """
    token = (os.environ.get("JIANJI_API_TOKEN") or "").strip()
    if not token:
        return {}
    return {"X-Access-Token": token, "Authorization": f"Bearer {token}"}


# 测试注入点（生产为 None → httpx 默认传输）。测试用 httpx.MockTransport 顶掉
# 真实网络，因此本模块的测试既不依赖后端进程，也不依赖数据库。
_TRANSPORT = None


def _client() -> httpx.Client:
    """新建 HTTP 客户端。"""
    timeout = float(os.environ.get("JIANJI_MCP_TIMEOUT") or DEFAULT_TIMEOUT)
    return httpx.Client(transport=_TRANSPORT, timeout=timeout)


def _error_text(resp: httpx.Response) -> str:
    """把后端错误响应变成一句能直接给 agent 看的话。

    后端 401/403 的 ``detail`` 是给用户看的中文说明，必须原样带出来。
    """
    detail = ""
    try:
        data = resp.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        raw = data.get("detail")
        if isinstance(raw, str):
            detail = raw
        elif raw is not None:
            detail = json.dumps(raw, ensure_ascii=False)
    elif data is not None:
        detail = json.dumps(data, ensure_ascii=False)
    if not detail:
        detail = (resp.text or "").strip()[:500] or resp.reason_phrase
    return f"后端返回 HTTP {resp.status_code}：{detail}"


def _request(method: str, path: str, *, params=None, json_body=None,
             files=None) -> httpx.Response:
    """发一次请求；网络失败与 4xx/5xx 都抛 ``ToolError``（agent 能看到原文）。"""
    url = _base_url() + path
    try:
        with _client() as client:
            resp = client.request(method, url, params=params, json=json_body,
                                  files=files, headers=_headers())
    except httpx.HTTPError as exc:
        raise ToolError(
            f"连接后端失败（{url}）：{exc}。请确认后端已启动，"
            f"且 JIANJI_API_URL / JIANJI_API_TOKEN 配置正确。"
        ) from exc
    if resp.status_code >= 400:
        raise ToolError(_error_text(resp))
    return resp


def _json(resp: httpx.Response):
    """解析 JSON 响应；后端返回了非 JSON（如代理错误页）也要说清楚。"""
    try:
        return resp.json()
    except Exception as exc:
        raise ToolError(
            f"后端返回的不是 JSON（HTTP {resp.status_code}）："
            f"{(resp.text or '').strip()[:300]}"
        ) from exc


def _clean_params(**kw) -> dict:
    """丢掉 None；布尔只在 True 时下发（False 都是后端默认值，不发更干净）。"""
    out = {}
    for key, value in kw.items():
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                out[key] = "true"
            continue
        out[key] = value
    return out


def _export_body(ids, library_id, encoding=None) -> dict:
    """导出请求体。ids / library_id 都不给时后端会返回 400，照实带出来即可。"""
    body = {}
    if ids:
        body["ids"] = list(ids)
    if library_id is not None:
        body["library_id"] = library_id
    if encoding is not None:
        body["encoding"] = encoding
    return body


def _save_bytes(resp: httpx.Response, out_path: str) -> dict:
    """把导出字节写到 ``out_path``，返回路径与字节数（不把二进制塞进对话）。"""
    target = Path(out_path).expanduser()
    try:
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
    except OSError as exc:
        raise ToolError(f"写入文件失败：{target}（{exc}）") from exc

    info = {"path": str(target.resolve()), "bytes": len(resp.content)}
    count = resp.headers.get("X-Count")
    if count is not None:
        try:
            info["count"] = int(count)
        except ValueError:
            info["count"] = count
    skipped = resp.headers.get("X-Skipped")
    if skipped is not None:
        info["skipped"] = skipped
    disposition = resp.headers.get("Content-Disposition") or ""
    match = _FILENAME_RE.search(disposition)
    if match:
        info["suggested_filename"] = match.group(1)
    return info


def _upload(file_path: str) -> dict:
    """把本地文件读成 multipart 的 ``file`` 字段（字段名必须是 file）。"""
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise ToolError(f"文件不存在或不是普通文件：{path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"读取文件失败：{path}（{exc}）") from exc
    return {"file": (path.name, data, "application/octet-stream")}


def _read_only() -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=True)


def _writes() -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=False)


# --------------------------------------------------------------------------
# MCP 服务器与 18 个工具
# --------------------------------------------------------------------------
server = MCPServer(
    name="jianji",
    title="简籍（Jianji）",
    instructions=(
        "简籍的导入导出接口。查书目用 search_records / get_record；"
        "编目前先 dedup 查重、search_external 套录；导入前建议先 preview_iso 看一眼"
        "字段统计再决定 target 与 mapping；导入后可用 inspect 体检、stats 出统计。"
        "导出前先用 list_libraries / list_resource_libraries 拿 library_id。"
        "导出工具会把文件写到 out_path 并只返回路径，不会把二进制塞进对话。"
        "大文件导入用 import_iso_async（拿 job_id 后用 get_job 轮询），别用同步的"
        "import_iso 硬等超时。所有工具都需要后端进程在 JIANJI_API_URL 上运行，"
        "并按后端既有的模块权限校验（导出/导入属「导入导出」模块，查书目属「图书库与"
        "编目」模块，统计属「统计报表」模块）。"
    ),
)


@server.tool(title="列出图书库", annotations=_read_only())
def list_libraries():
    """列出全部图书库（id / 名称 / 备注 / 记录数）。

    权限：需要「全局设置」模块的查看权限（GET /api/book-libraries）。
    返回：图书库数组，每项含 id、name、note、record_count。
    用途：拿到 library_id 后再做整库导出（export_iso / export_excel）。
    """
    return _json(_request("GET", "/api/book-libraries"))


@server.tool(title="搜索书目", annotations=_read_only())
def search_records(
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
    q: str | None = None,
    barcode: str | None = None,
    library_id: int | None = None,
    created_by: str | None = None,
    source: str | None = None,
    class_no: str | None = None,
    no_barcode: bool = False,
    created_from: str | None = None,
    created_to: str | None = None,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 20,
):
    """搜索图书库书目（GET /api/records）。

    权限：需要「图书库与编目」模块的查看权限。
    参数：q 是跨字段模糊查询（题名/责任者/ISBN/索书号等）；no_barcode=true 只找
    没有条码的记录；active_only=true 只找「至少还有一册在架」的记录（剔旧后整条
    注销的默认不该出现在编目工作台里）；created_from / created_to 是创建日期区间。
    page 从 1 开始，page_size 最大 200。
    返回：{"total": 命中总数, "page":, "page_size":, "items": [...]}；每条含
    id、title、isbn、author、year、class_no、source、created_by、volume、
    shelf_no、call_no、barcodes[]、active_count、total_count。
    提示：整库导出可以只给 library_id 给 export_iso，不必先翻页拿 ids。
    """
    params = _clean_params(isbn=isbn, title=title, author=author, q=q,
                           barcode=barcode, library_id=library_id,
                           created_by=created_by, source=source, class_no=class_no,
                           no_barcode=no_barcode, created_from=created_from,
                           created_to=created_to, active_only=active_only,
                           page=page, page_size=page_size)
    return _json(_request("GET", "/api/records", params=params))


@server.tool(title="取书目详情", annotations=_read_only())
def get_record(record_id: int):
    """取一条书目的完整 MARC 详情（GET /api/records/{record_id}）。

    权限：需要「图书库与编目」模块的查看权限。
    参数：record_id 是 search_records 返回的 id。
    返回：含 id、library_id、leader、fields（完整 MARC 字段）、source、status、
    shelf_no、volume、volume_seq、search（检索点）、findings（质检问题）、
    mrk（MRK 文本）、barcodes[]。
    记录不存在时后端返回 404「记录不存在」。
    """
    return _json(_request("GET", f"/api/records/{int(record_id)}"))


@server.tool(title="导出 ISO 2709（图书库）", annotations=_writes())
def export_iso(ids: list[int] | None = None, library_id: int | None = None,
               encoding: str = "gbk", out_path: str = ""):
    """把图书库记录导出成 ISO 2709 文件，写到本地 out_path（POST /api/export/iso）。

    权限：需要「导入导出」模块的**读写**权限（导出按模块口径算写操作）。
    参数：给 ids 按 id 导出；不给 ids 而给 library_id 则整库导出；两个都不给
    后端返回 400「请提供要导出的记录 id，或指定一个图书库」。
    encoding 默认 gbk（下游 ILS 兼容性最好），可传 utf-8。
    out_path 是本地文件路径，父目录会自动创建。
    返回：{"path": 绝对路径, "bytes": 字节数, "count": 导出条数, "skipped": 跳过条数,
    "suggested_filename": 建议文件名}。**不会**把文件内容塞进对话。
    """
    if not (out_path or "").strip():
        raise ToolError("out_path 不能为空：请给出要写入的本地文件路径，例如 D:/导出/records.iso")
    resp = _request("POST", "/api/export/iso",
                    json_body=_export_body(ids, library_id, encoding))
    return _save_bytes(resp, out_path)


@server.tool(title="导出 Excel（图书库）", annotations=_writes())
def export_excel(ids: list[int] | None = None, library_id: int | None = None,
                 out_path: str = ""):
    """把图书库记录导出成标准 Excel 模板（.xlsx），写到本地 out_path（POST /api/export/excel）。

    权限：需要「导入导出」模块的**读写**权限。
    参数：给 ids 按 id 导出；不给 ids 而给 library_id 则整库导出；两个都不给 → 400。
    out_path 是本地文件路径，父目录会自动创建。
    返回：{"path": 绝对路径, "bytes": 字节数, "count": 导出条数,
    "suggested_filename": "records.xlsx"}。列定义见 实现说明。
    """
    if not (out_path or "").strip():
        raise ToolError("out_path 不能为空：请给出要写入的本地文件路径，例如 D:/导出/records.xlsx")
    resp = _request("POST", "/api/export/excel",
                    json_body=_export_body(ids, library_id))
    return _save_bytes(resp, out_path)


@server.tool(title="预览 ISO 导入", annotations=_read_only())
def preview_iso(file_path: str, limit: int = 10):
    """**只解析、不落库**：先看一眼 ISO 文件里有什么（POST /api/import/iso/preview）。

    权限：需要「导入导出」模块的**查看**权限（该接口在只读 POST 白名单里）。
    参数：file_path 是本地 ISO 2709 文件；limit 是逐条样例的条数（1..50，默认 10）。
    返回：{"total": 解析到的记录数, "parse_errors": [...], "with_barcode":,
    "with_class_no":, "sample": [...], "records": [{"index","rows":[...]}],
    "field_stats": [{"key","count","known","suggested"}], "suggested_mapping": {...},
    "target_fields": [...], "compat_fields": [...]}。
    用途：导入前拿 field_stats 与 suggested_mapping 决定 import_iso 的 mapping 参数。
    """
    files = _upload(file_path)
    params = {"limit": max(1, min(int(limit or 10), 50))}
    return _json(_request("POST", "/api/import/iso/preview", params=params, files=files))


@server.tool(title="导入 ISO（落库）", annotations=_writes())
def import_iso(file_path: str, source: str = "import", target: str = "resource",
               library_id: int | None = None, barcode_start: str = "",
               force: bool = False, mapping: str = ""):
    """导入 ISO 2709 并落库（POST /api/import/iso，**同步**）。

    ⚠️ 大文件（整库导入）会一直等到 HTTP 超时（默认 120s）——那种场景改用
    `import_iso_async`（入队后拿 job_id，再用 `get_job` 轮询进度），别硬等。

    权限：需要「导入导出」模块的**读写**权限。
    参数：
      - file_path：本地 ISO 2709 文件。
      - target：`resource`（默认，写套录资源库，按 ISBN upsert 去重）或
        `records`（写图书库，每条都要有条码）。其他值 → 400。
      - library_id：含义随 target 变 —— resource 时是**资源库** id（缺省「手工导入」），
        records 时是**图书库** id（缺省默认图书库）。传了不存在的 id → 404。
      - barcode_start：target=records 且记录没带条码时，用「前缀+数字」生成条码
        （如 A00000001）。
      - force：target=records 时放行质检 ERROR 的记录。
      - mapping：字段映射的 JSON 字符串，形如 {"909$b": "905$f"}（源 tag$code →
        目标 tag$code），键值来自 preview_iso 的 field_stats。
    返回：落库结果（含写入/更新/跳过条数、parse_errors，用了 mapping 时还有 mapped）。
    """
    if target not in ("resource", "records"):
        raise ToolError("target 必须是 resource/records")
    files = _upload(file_path)
    params = _clean_params(source=source, target=target, library_id=library_id,
                           barcode_start=barcode_start or None, force=force,
                           mapping=mapping or None)
    return _json(_request("POST", "/api/import/iso", params=params, files=files))


@server.tool(title="导入发货清单 Excel", annotations=_writes())
def import_delivery_excel(file_path: str, create: bool = False,
                          target: str = "resource", library_id: int | None = None,
                          force: bool = False, sheet: str | None = None,
                          header_row: int | None = None, mapping: str | None = None):
    """导入书商发货清单 Excel（POST /api/import/delivery-excel）。

    权限：需要「导入导出」模块的**读写**权限。
    参数：
      - file_path：本地 .xlsx 发货清单。
      - create：默认 false —— **只预览清洗结果、不落库**；true 才按 target 写库。
        想先看结果又不想动数据，就保持 false。
      - target：`resource`（默认，草稿写资源库）或 `records`（直接进图书库，
        每行必须有条码号 + 索书号 + 正题名 + 出版者 + 出版年）。
      - library_id：含义随 target 变（resource → 资源库 id，缺省「发货清单草稿」；
        records → 图书库 id，缺省默认图书库）。不存在的 id → 404。
      - force：target=records 时放行质检 ERROR 的行。
      - sheet / header_row：指定工作表与表头行；不给则自动挑匹配列名最多的一行。
      - mapping：手动列映射的 JSON 字符串，形如 {"0": "barcode", "1": "title"}
        （列下标 → 字段 key），给了就以它为准。
    返回：{"rows": [...], "sheet":, "sheets": [...], "sheets_info": [...],
    "read_rows":, "summary": {...}}；create=true 时另有 created。
    """
    if target not in ("resource", "records"):
        raise ToolError("target 必须是 resource/records")
    files = _upload(file_path)
    params = _clean_params(create=create, target=target, library_id=library_id,
                           force=force, sheet=sheet, header_row=header_row,
                           mapping=mapping)
    return _json(_request("POST", "/api/import/delivery-excel", params=params,
                          files=files))


@server.tool(title="列出资源库", annotations=_read_only())
def list_resource_libraries():
    """列出全部资源库（套录/草稿库，GET /api/resource-libraries）。

    权限：需要「资源库」模块的查看权限。
    返回：资源库数组，每项含 id、name、origin、record_count、enabled。
    用途：拿到资源库 id 给 import_iso（target=resource）或 export_resource_iso 整库导出。
    """
    return _json(_request("GET", "/api/resource-libraries"))


@server.tool(title="导出 ISO 2709（资源库）", annotations=_writes())
def export_resource_iso(ids: list[int] | None = None, library_id: int | None = None,
                        encoding: str = "gbk", out_path: str = ""):
    """把**资源库**记录导出成 ISO 2709 文件，写到本地 out_path（POST /api/export/resource-iso）。

    权限：需要「导入导出」模块的**读写**权限。
    参数：给 ids 按 id 导出；不给 ids 而给 library_id 则整库导出；两个都不给
    后端返回 400「请提供要导出的记录 id，或指定一个资源库」。
    encoding 默认 gbk，可传 utf-8。out_path 的父目录会自动创建。
    返回：{"path", "bytes", "count", "skipped", "suggested_filename"}。
    导出的数据是原料原样（含 9xx 私有字段），适合备份 / 迁移 / 换个库再套录。
    """
    if not (out_path or "").strip():
        raise ToolError("out_path 不能为空：请给出要写入的本地文件路径，例如 D:/导出/resource.iso")
    resp = _request("POST", "/api/export/resource-iso",
                    json_body=_export_body(ids, library_id, encoding))
    return _save_bytes(resp, out_path)


@server.tool(title="导出 Excel（资源库）", annotations=_writes())
def export_resource_excel(ids: list[int] | None = None, library_id: int | None = None,
                          out_path: str = ""):
    """把**资源库**记录导出成标准 Excel（.xlsx），写到本地 out_path（POST /api/export/resource-excel）。

    权限：需要「导入导出」模块的**读写**权限。
    参数：给 ids 按 id 导出；不给 ids 而给 library_id 则整库导出；两个都不给 → 400。
    out_path 的父目录会自动创建。
    返回：{"path", "bytes", "count", "suggested_filename": "resource-records.xlsx"}。
    """
    if not (out_path or "").strip():
        raise ToolError("out_path 不能为空：请给出要写入的本地文件路径，例如 D:/导出/resource.xlsx")
    resp = _request("POST", "/api/export/resource-excel",
                    json_body=_export_body(ids, library_id))
    return _save_bytes(resp, out_path)


# --------------------------------------------------------------------------
#  第二批工具：套录 / 查重 / 体检 / 统计 / 长任务
# --------------------------------------------------------------------------
#  曾写「未暴露，用户提出需要时再加，不要预先堆」。这次加的理由是
# 具体的：agent 要独立完成「拿到一批书 → 查重在不在库 → 套录补字段 → 导入 →
# 体检 → 出统计」这条完整链路，缺任何一环都得把人叫回来点界面。
# 仍然**没有**把每个 REST 端点都包成工具：能由一个工具参数覆盖的（体检的 6 个
# 检查项）不拆成 6 个工具 —— 每个工具的 schema 都进模型上下文。

@server.tool(title="套录检索", annotations=_read_only())
def search_external(q: str | None = None, isbn: str | None = None,
                    title: str | None = None, author: str | None = None,
                    library_id: int | None = None, limit: int = 10):
    """在套录源里找书：图书库 → 本地资源库 → Z39.50，**前一层命中即短路**（POST /api/cataloging/search）。

    权限：需要「图书库与编目」模块的查看权限。
    参数：`q` 是通用检索词 —— 长得像 ISBN 就按 ISBN 查，否则先按题名查、没命中
    再按责任者查一次（`matched_by` 回显实际用了哪一种）。也可直接用 isbn /
    title / author 分别指定（给了它们时 q 被忽略）。limit 默认 10。
    返回：{"matched_by": "isbn|title|author", "candidates": [...]}；每个候选含
    source、key、title、author、publisher、year、class_no、isbn、isbn_key、
    volume、**record_id（非空 = 已在图书库里，别再当新书编）** 与 record（完整 MARC）。
    用途：拿到候选后由人（Web 界面）或 agent 决定「加复本」还是「另编一册」。
    """
    body = _clean_params(q=q, isbn=isbn, title=title, author=author,
                         library_id=library_id, limit=limit or 10)
    if not any(k in body for k in ("q", "isbn", "title", "author")):
        raise ToolError("至少给 q / isbn / title / author 之一")
    return _json(_request("POST", "/api/cataloging/search", json_body=body))


@server.tool(title="查重", annotations=_read_only())
def dedup(isbn: str | None = None, title: str | None = None,
          library_id: int | None = None):
    """编目查重：库里是不是已经有这本书（GET /api/dedup）。

    权限：需要「图书库与编目」模块的查看权限。
    参数：isbn 走**全形态精确**匹配（ISBN-10/13 与带不带连字符都能对上）；
    title 走题名模糊（上限 20 条）。两个都给时**只按 isbn 查**（后端就是这个口径）。
    library_id 用来限定图书库 —— 同书异校不算重。
    返回：{"duplicates": [{"id","title","isbn","library_id"}], "count": n}。
    """
    if not (isbn or title):
        raise ToolError("至少给 isbn 或 title 之一")
    params = _clean_params(isbn=isbn, title=title, library_id=library_id)
    return _json(_request("GET", "/api/dedup", params=params))


@server.tool(title="数据体检", annotations=_read_only())
def inspect(kind: str = "summary", library_id: int | None = None,
            page: int = 1, page_size: int = 50):
    """数据体检：库里有多少记录缺这缺那（GET /api/inspect/*）。

    权限：需要「图书库与编目」模块的查看权限。
    参数：`kind` 选一项 ——
      summary（概览卡片：有问题的记录数 + 各项计数）、
      no-barcode（无条码）、no-class（缺分类号，算不出索书号）、
      bad-class（分类号结构异常）、barcode-gaps（条码断号段）。
    page / page_size 只对**非 summary** 的项有效（1..500，默认 50）。
    返回：summary 给 {"record_total","record_with_issue","items":[{"key","label","count"}]}；
    其余给分页记录列表。
    """
    allowed = {"summary", "no-barcode", "no-class", "bad-class", "barcode-gaps"}
    if kind not in allowed:
        raise ToolError(f"kind 必须是 {sorted(allowed)} 之一")
    params = _clean_params(library_id=library_id)
    if kind != "summary":
        params["page"] = max(1, int(page or 1))
        params["page_size"] = max(1, min(int(page_size or 50), 500))
    return _json(_request("GET", f"/api/inspect/{kind}", params=params))


@server.tool(title="馆藏统计", annotations=_read_only())
def stats(library_id: int | None = None):
    """馆藏总览：各图书库的种数 / 册数 / 码洋（在架口径）与注销册数（GET /api/stats/overview）。

    权限：需要「统计报表」模块的查看权限。
    参数：不给 library_id 则全馆合计。
    返回：按图书库分组的种数/册数/金额，附全馆合计与币种。
    注意：**按种汇总**（与财产登记表「按册出账」是两本不同的账，见）。
    """
    return _json(_request("GET", "/api/stats/overview",
                          params=_clean_params(library_id=library_id)))


@server.tool(title="后台导入 ISO（返回任务号）", annotations=_writes())
def import_iso_async(file_path: str, target: str = "records",
                     library_id: int | None = None, barcode_start: str = "",
                     force: bool = False, mapping: str = ""):
    """**长任务专用**：把 ISO 导入丢到后台，立即返回 job_id（POST /api/jobs/import-iso）。

    权限：需要「导入导出」模块的**读写**权限。
    为什么有它：`import_iso` 是同步的，整库导入会一直等到 HTTP 超时（默认 120s）。
    这个工具只负责**入队**，随后用 `get_job(job_id)` 轮询进度（status 为
    pending / running / done / failed，progress 是 0..100）。
    参数与 `import_iso` 完全一致（target / library_id / barcode_start / force / mapping）。
    ⚠️ 参数在**入队时**就校验（坏 id 当场 404），不会让你等一个注定失败的任务。
    """
    if target not in ("resource", "records"):
        raise ToolError("target 必须是 resource/records")
    files = _upload(file_path)
    params = _clean_params(target=target, library_id=library_id,
                           barcode_start=barcode_start or None, force=force,
                           mapping=mapping or None)
    return _json(_request("POST", "/api/jobs/import-iso", params=params, files=files))


@server.tool(title="列出后台任务", annotations=_read_only())
def list_jobs():
    """列出最近的后台任务（最多 20 条，新的在前；GET /api/jobs）。

    权限：需要「导入导出」模块的查看权限。
    用途：找回上次的 job_id 继续轮询，或看一眼有没有卡住的任务。
    返回：[{"id","type","status","progress","message","result","error","created_at"}]。
    """
    return _json(_request("GET", "/api/jobs"))


@server.tool(title="查后台任务进度", annotations=_read_only())
def get_job(job_id: int):
    """查一个后台任务的进度与结果（GET /api/jobs/{job_id}）。

    权限：需要「导入导出」模块的查看权限。
    参数：job_id 由 `import_iso_async` 或 Web 界面的后台任务返回。忘了 id 就用
    `list_jobs` 找回来。长任务的正确用法是**隔几秒轮询一次**，不是一次等到天荒地老。
    返回：{"id", "type", "status", "progress", "message", "result", "error"}；
    status 为 pending / running / done / failed 之一。任务不存在 → 404「任务不存在」。
    """
    return _json(_request("GET", f"/api/jobs/{int(job_id)}"))


def _print_selfcheck() -> None:
    """打印一份自检信息（给 start-mcp.bat 的 check 与排障用）。

    输出刻意保持 ASCII：批处理窗口的代码页会把中文打成乱码。
    """
    import asyncio
    import logging

    # httpx 默认在 INFO 级打一行 "HTTP Request: GET ... 200 OK"，会插进自检
    # 输出的中间。这里临时压掉，让整段自检可以一眼读完。
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)

    print("[mcp] python   :", sys.executable)
    print("[mcp] api url  :", _base_url())
    print("[mcp] api token:", "set" if _headers() else "not set (no auth headers sent)")
    print("[mcp] timeout  :", os.environ.get("JIANJI_MCP_TIMEOUT") or DEFAULT_TIMEOUT, "s")
    tools = asyncio.run(server.list_tools())
    print(f"[mcp] tools    : {len(tools)}")
    for tool in tools:
        print("        -", tool.name)
    try:
        with _client() as client:
            resp = client.get(_base_url() + "/api/health", headers=_headers(), timeout=5.0)
        print(f"[mcp] backend  : HTTP {resp.status_code} {resp.text[:120].strip()}")
    except httpx.HTTPError as exc:
        print(f"[mcp] backend  : UNREACHABLE ({exc})")
        print("[mcp] hint     : start the backend (restart-backend.bat) or fix JIANJI_API_URL")
    finally:
        httpx_logger.setLevel(previous_level)


def main(argv=None) -> None:
    """以 stdio 传输启动 MCP 服务器（供 MCP 客户端拉起的进程入口）。

    ``--check`` 只打印自检信息后退出，不进入 stdio 循环（方便排障）。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0].lstrip("-") == "check":
        _print_selfcheck()
        return
    server.run("stdio")


if __name__ == "__main__":
    main()
