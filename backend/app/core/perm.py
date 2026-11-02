# -*- coding: utf-8 -*-
"""权限模型：模块登记表 + 「请求路径 → 模块」映射 + 角色判定。

## 粒度：模块级

侧栏一项 = 一个模块，每个角色对每个模块取 `none / read / write` 三档。
不做「单条记录级 / 字段级」权限——那是 ILS 的活，本工具是单机编目工具。

## 为什么要一张**显式**的路径映射表，而不是按 HTTP 方法猜

`GET` 放行、其余拦住的写法看起来很省事，但它把「哪些接口算读」这个判断
散落进了每个路由的实现里：`POST /api/cataloging/search` 是**查**（套录检索，
一个字都不写库），`POST /api/import/iso/preview` 也是**查**，而
`POST /api/export/excel` 是**导出数据**（按模块口径算写操作）。
所以本文件用 `_READ_ONLY_POST` 显式列出「方法为 POST 但只读」的接口，
其余 POST/PUT/PATCH/DELETE 一律按 write 判定 —— 默认从严。

## 未登记路径怎么办：**登录开启时拒绝**

`module_for_path` 返回 None 时中间件直接 403（而不是放行）。理由：以后新加
接口忘了登记模块，宁可**响亮地坏掉**（开发/测试立刻发现），也不要静默地
变成「人人可访问」。`backend/tests/test_auth.py::test_every_api_route_maps_to_a_module`
遍历 `app.routes`，保证每个已注册路由都能映射到模块 —— 忘登记会在测试里红。

## 通配键

管理员角色的 permissions 是 `{"*": "write"}`。用通配而不是把所有模块逐个
填满：后者每新增一个模块就漏一个，是这类系统的经典踩坑（新模块一上线，
管理员反而进不去，得手工改库）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import (PERM_LEVELS, PERM_NONE, PERM_READ, PERM_WILDCARD,
                      PERM_WRITE, ROLE_ADMIN, ROLE_EDITOR)

# 权限档位排序：判定「够不够」时比大小
_RANK = {PERM_NONE: 0, PERM_READ: 1, PERM_WRITE: 2}


@dataclass(frozen=True)
class Module:
    """一个可授权的模块。

    `prefixes` 是它拥有的 API 路径前缀（按**路径段边界**匹配，见 `_under`）。
    前端侧栏的 `/xxx` 路径不在这里登记——前端按 `key` 查表
    （`前端实现` 的 `ROUTE_MODULES`），两边的 `key` 必须逐项一致。
    """
    key: str
    title: str
    group: str
    prefixes: tuple[str, ...]


# 顺序 = 侧栏顺序（前端菜单按同一顺序渲染授权勾选框，管理员配权限时不会找不着北）
MODULES: tuple[Module, ...] = (
    # 图书转库 `/api/transfers` 挂在图书库模块下：搬的是记录与馆藏，
    # 不是"图书库的管理设置"（后者在 settings 的 /api/book-libraries）。
    Module("records", "图书库与编目", "编目",
           ("/api/records", "/api/cataloging", "/api/quick-entry",
            # MARC 文件浏览是编目辅助（看看手头这个文件里到底有什么），
            # 能编目的人就该能看；它不读写库里的数据。
            "/api/marc",
            "/api/lookup", "/api/dedup", "/api/ai", "/api/transfers",
            # 操作日志：`/api/record-logs` 与 `/api/records/{id}/logs`。
            # 前缀 `/api/records` **不会**匹配到 `/api/record-logs`（`-` 不是 `/`），
            # 必须显式列出——与 `/api/records` 同模块：能编目的人就该看得见"谁改的"。
            "/api/record-logs")),
    Module("barcodes", "条码管理", "编目", ("/api/holdings",)),
    Module("labels", "书标打印", "编目", ("/api/labels",)),

    # 读者与流通：借还台在前、读者档案在后（侧栏同序）。
    # 两个模块分开授权：只做借还的志愿者不需要改读者档案；只维护读者名单的
    # 老师也不需要借还权限。
    # 盘点（/api/inventory）与借还台同属「流通中的实物」：能借还的人就该能盘点，
    # 不再单开一个权限键（新增键会让已有角色的授权表凭空多一格 none）。
    Module("circulation", "借还台", "借阅",
           ("/api/circulation", "/api/inventory", "/api/recommendations",
            "/api/reservations",
            # 荐购/受赠登记：读者服务的一部分，与推荐位同模块（不是采购单）。
            "/api/acquisitions")),
    Module("readers", "读者管理", "借阅", ("/api/readers",)),

    Module("resources", "资源库", "数据与规范",
           ("/api/resource-libraries", "/api/resource-records")),
    Module("io", "导入导出", "数据与规范",
           ("/api/import", "/api/export", "/api/jobs")),
    Module("inspect", "数据体检", "数据与规范", ("/api/inspect",)),
    Module("dictionaries", "字典中心", "数据与规范",
           ("/api/dictionaries", "/api/marc-fields")),

    # 财产登记表 `/api/property-register` 是报表，归统计模块（不是"设置"）
    Module("stats", "统计分析", "统计",
           ("/api/stats", "/api/property-register", "/api/accession")),
    Module("settings", "全局设置", "系统",
           ("/api/settings", "/api/book-libraries", "/api/sources")),
    # agent 凭据与审计归「用户管理」：管的是**谁能进系统、进来能干什么**，
    # 与账号/角色同源。另开一个模块只会让角色权限表多一列没人懂的开关。
    Module("users", "用户管理", "系统",
           ("/api/users", "/api/roles", "/api/agent-tokens", "/api/agent-audit",
            # 库结构升级是"改整个系统形态"的操作，与账号/角色同档：只给管理员。
            # 不登记的话登录开启后会被中间件按"未知前缀"一律 403。
            "/api/maintenance")),
)

MODULE_KEYS: tuple[str, ...] = tuple(m.key for m in MODULES)
MODULE_BY_KEY: dict[str, Module] = {m.key: m for m in MODULES}

# 登录相关路径自成一档：**登录即可访问**，不走模块授权。
# 否则会出现「用户管理是唯一能改自己密码的入口，而密码接口又要求 users 权限」
# 这种锁死——普通编目员永远改不了自己的密码。
MODULE_AUTH = "auth"

# 精确路径覆盖（在**前缀匹配之前**判定）。少数接口的归属和它的前缀不一致。
# 目前为空（原先唯一的条目 `/api/ai/test` 已随遗留 AI 接口一并清理）——
# **保留机制**：它比前缀匹配更早判定，将来再有「归属与前缀不一致」的接口时直接加。
_EXACT: dict[str, str] = {}

# 方法为 POST 但**只读**的接口（显式列出，见模块 docstring）。
# 只写正则，不写完整路径：`/api/records/42/validate` 里的 42 是记录 id。
_READ_ONLY_POST = (
    re.compile(r"^/api/cataloging/(search|prepare)$"),
    re.compile(r"^/api/records/\d+/validate$"),
    re.compile(r"^/api/ai/optimize-local$"),
    re.compile(r"^/api/import/(iso/preview|delivery-inspect)$"),
    re.compile(r"^/api/transfers/preview$"),
    re.compile(r"^/api/property-register/preview$"),
)


def _under(path: str, prefix: str) -> bool:
    """路径是否落在前缀下。**必须按段边界**判，否则 `/api/recordsfoo` 会误命中。"""
    return path == prefix or path.startswith(prefix + "/")


def module_for_path(path: str) -> str | None:
    """请求路径 → 模块 key；未登记返回 None（中间件据此拒绝，见模块 docstring）。"""
    if _under(path, "/api/auth"):
        return MODULE_AUTH
    # 工作台是登录后的运营摘要（只读、无个人隐私明细），任何馆员都该看得见；
    # 具体页面仍由各自模块权限把关。挂在 MODULE_AUTH 下表示“登录即可”。
    if _under(path, "/api/dashboard"):
        return MODULE_AUTH
    # 读者门户使用独立的 reader_sessions，但路由仍需登记，避免“未登记路径”
    # 在权限测试里误报；真正的鉴权由 portal.current_reader 负责。
    if _under(path, "/api/portal"):
        return MODULE_AUTH
    if path in _EXACT:
        return _EXACT[path]
    for m in MODULES:
        for prefix in m.prefixes:
            if _under(path, prefix):
                return m.key
    return None


def required_level(method: str, path: str) -> str:
    """这个请求需要哪个档位。默认从严：只有 GET/HEAD 与显式列出的只读 POST 算 read。"""
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return PERM_READ
    for pat in _READ_ONLY_POST:
        if pat.match(path):
            return PERM_READ
    return PERM_WRITE


def normalize_permissions(raw) -> dict:
    """归一角色权限：丢掉未知模块键与非法档位，`none` 不落库（省体积、diff 干净）。

    非法输入（None / 字符串 / 列表）→ `{}`，即**零权限**。方向是安全的：
    脏数据只会让人进不去，不会让人多进去。
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    allowed = set(MODULE_KEYS) | {PERM_WILDCARD}
    for key, level in raw.items():
        if not isinstance(key, str) or key not in allowed:
            continue
        if level not in PERM_LEVELS or level == PERM_NONE:
            continue
        out[key] = level
    return out


def role_can(permissions, module: str, level: str) -> bool:
    """角色权限是否够 `level` 档。`permissions` 允许是脏值（走 normalize 的宽松口径）。"""
    need = _RANK.get(level)
    if need is None:
        return False
    if not isinstance(permissions, dict):
        return False
    for key in (module, PERM_WILDCARD):
        have = _RANK.get(permissions.get(key))
        if have is not None and have >= need:
            return True
    return False


def effective_levels(permissions) -> dict[str, str]:
    """把权限摊平成「每个模块的实际档位」——前端侧栏按它决定显示哪些菜单。

    通配角色（管理员）会得到每个模块都是 write 的**完整**字典，前端就不必自己
    再实现一遍通配逻辑（两处实现必然漂移）。
    """
    return {key: (PERM_WRITE if role_can(permissions, key, PERM_WRITE)
                  else PERM_READ if role_can(permissions, key, PERM_READ)
                  else PERM_NONE)
            for key in MODULE_KEYS}


def modules_catalog() -> list[dict]:
    """模块清单（给前端渲染授权勾选框）。"""
    return [{"key": m.key, "title": m.title, "group": m.group} for m in MODULES]


# ---------------------------------------------------------------------------
#  出厂默认角色（默认有管理员即可，其他角色权限由管理员设置）
# ---------------------------------------------------------------------------

# 编辑员（编目员）：日常编目闭环全给写；**设置只给读**。
# 设置必须给读：编目页自己要读 `/api/settings/barcode`（条码位数）与
# `/api/settings/optimize`，图书库页要读 `/api/book-libraries`（图书库下拉框）。
# 只给管理员读的话，普通编目员连编目页都打不开——这是最容易踩的一脚。
_EDITOR_WRITE = ("records", "barcodes", "labels", "circulation", "readers",
                 "resources", "io", "inspect", "dictionaries")
_EDITOR_READ = ("settings", "stats")

DEFAULT_ROLES: tuple[dict, ...] = (
    {
        "name": ROLE_ADMIN,
        "title": "管理员",
        "permissions": {PERM_WILDCARD: PERM_WRITE},
        "builtin": True,
        "note": "全部模块可读写（含用户管理）。此角色不可删除、不可降权。",
    },
    {
        "name": ROLE_EDITOR,
        "title": "编目员",
        "permissions": {**{k: PERM_WRITE for k in _EDITOR_WRITE},
                        **{k: PERM_READ for k in _EDITOR_READ}},
        "builtin": True,
        "note": "编目、条码、书标、借还、读者、资源库、导入导出、体检、字典可读写；设置与统计只读。",
    },
)
