# -*- coding: utf-8 -*-
"""路由声明：把「属于哪个模块 / 需要什么档位 / 是否公开」写在路由上。

## 为什么要有这一层

改造前，同一条路由的权限归属记在**三处**：

| 位置 | 内容 |
| --- | --- |
| `core/perm.py::MODULES[].prefixes` | 前缀 → 模块 |
| `core/perm.py::_READ_ONLY_POST` | 哪些 POST 其实只读 |
| `main.py::PUBLIC_PATHS` / `PUBLIC_AUTH_PATHS` | 哪些路径公开 |

新增一条接口要改 3–4 处，而且**漏一处的后果不对称**：漏登记 → 登录后一律 403
（好查）；前缀写宽了 → 静默放权（难发现）。

前端 `nav.ts` 的 `module` 是**页面**归属（一个页面可能跨多个接口），不是路由概念，
不在这里收敛——硬合并只会得到一张既不像路由、也不像菜单的表。

## 用法

```python

@router.get("/records", **api(module="records"))
def list_records(...): ...

@router.post("/cataloging/search", **api(module="records", level="read"))
def search(...): ...              # POST 但只读

@router.get("/health", **api(public=True))
def health(...): ...              # 不需要登录
```

`level` 缺省按方法推导（GET/HEAD → read，其余 → write），只有「POST 但只读」需要
显式写 —— 这正是原来 `_READ_ONLY_POST` 那张表的替代物。

## 迁移策略（两步，不一步到位）

- **第一步（本文件当前状态）**：只加声明、不改判定。`perm.py` 仍按前缀表判，
  新声明由 `check_declarations()` 与旧表**逐条比对**，不一致就抛错（启动失败）。
  这样"漏登记"从「上线后 403」提前成「启动时炸」。
- **第二步**：声明覆盖全部路由后，让判定改读声明，删掉 `perm.py` 的前缀表。

为什么不一次做完：34 个模块、226 个操作，一次改判定的话，出问题时分不清是
"声明写错了"还是"判定逻辑改了"。分两步，每一步都能用现有测试证明行为没变。
"""
from __future__ import annotations

import re

from .capabilities import (CAPABILITIES, MODULE_CAPABILITIES, READ_CAPABILITIES,
                           level_for_capability)
from .perm import MODULE_AUTH, MODULE_KEYS

#: 允许的档位。
LEVELS = ("read", "write")

#: 档位高低（能力声明检查用）：写路由不能只要求只读能力。
_LEVEL_RANK = {"read": 0, "write": 1}

#: 挂到 endpoint 函数上的属性名。
ATTR = "_dsh_api"


def api(*, module: str | None = None, level: str | None = None,
        capability: str | None = None, public: bool = False):
    """声明一条路由的权限归属，**套在路由装饰器外面**：

        @router.get("/records")
        @api(module="records")
        def list_records(...): ...

        @router.post("/holdings/suspend")
        @api(module="barcodes", capability="catalog.item.suspend")
        def suspend(...): ...

    为什么不做成 `**api(...)` 塞进路由装饰器：FastAPI 的路由装饰器只接受它自己
    认识的参数，多一个就 TypeError；而"先声明、后注册"的顺序在这里无所谓——
    元数据挂在函数上，注册时函数对象是同一个。

    `capability` 是可选的具体能力声明。给了它，运行时判定按能力算所需档位
    （只读能力要 read，其余要 write）；没给就沿用 module+level。能力必须属于声明的
    模块，否则启动前就抛错（避免“声明了 suspend 却挂在 readers 模块下”这种错配）。
    """
    if public and (module or capability):
        raise ValueError("public 路由不该再声明 module/capability：公开意味着不判权限")
    if module is not None and module not in MODULE_KEYS and module != MODULE_AUTH:
        raise ValueError(f"未知模块 {module!r}")
    if level is not None and level not in LEVELS:
        raise ValueError(f"level 只能是 {LEVELS}，收到 {level!r}")
    if capability is not None:
        if capability not in CAPABILITIES:
            raise ValueError(f"未知能力 {capability!r}")
        if module is None or module == MODULE_AUTH:
            raise ValueError("声明 capability 必须同时声明可授权的 module")
        if capability not in MODULE_CAPABILITIES[module]:
            raise ValueError(f"能力 {capability!r} 不属于模块 {module!r}")
        # 只读路由不能声明写能力：那会让“能看”变成“能改”。反向（写路由声明只读
        # 能力）在运行时会降级成只读，同样错误，由启动检查按实际档位拦。
        if level == "read" and capability not in READ_CAPABILITIES:
            raise ValueError(f"只读路由不能声明写能力 {capability!r}")

    meta = {"module": module, "level": level, "capability": capability,
            "public": public}

    def _apply(fn):
        setattr(fn, ATTR, meta)
        return fn
    return _apply


def mark(fn, *, module: str | None = None, level: str | None = None,
         capability: str | None = None, public: bool = False):
    """把声明挂到 endpoint 函数上（供 `check_declarations` 读取）。"""
    if capability is not None and capability not in CAPABILITIES:
        raise ValueError(f"未知能力 {capability!r}")
    setattr(fn, ATTR, {"module": module, "level": level,
                       "capability": capability, "public": public})
    return fn


def iter_routes(router, prefix: str = ""):
    """展开 `app` 上全部已注册路由，产出 `(完整路径, 方法集合, endpoint)`。

    ⚠️ 与 `tests/test_auth.py::iter_api_paths` 同一个坑：FastAPI 0.14x 起
    `include_router` 不再平铺，而是包一个 `_IncludedRouter`（`path` 为 None，
    真路由在 `original_router`、前缀在 `include_context.prefix`）。
    """
    for route in router.routes:
        path = getattr(route, "path", None)
        if path is None:
            inner = getattr(route, "original_router", None)
            ctx = getattr(route, "include_context", None)
            if inner is not None:
                yield from iter_routes(inner, prefix + (getattr(ctx, "prefix", "") or ""))
            continue
        endpoint = getattr(route, "endpoint", None)
        methods = getattr(route, "methods", None) or set()
        yield prefix + path, set(methods), endpoint


def declaration_of(endpoint) -> dict | None:
    return getattr(endpoint, ATTR, None) if endpoint is not None else None


# ---------------------------------------------------------------------------
#  运行时判定（第二步：判定改读声明）
# ---------------------------------------------------------------------------
#
# 判定拿到的是**具体路径**（`/api/records/42`），而声明登记在**路由模板**上
# （`/api/records/{id}`）。所以需要一层「具体路径 → 模板」的匹配。
#
# 用编译好的正则而不是逐个 `str.replace`：模板里的 `{param}` 与路径参数段一一对应，
# 正则能顺带保证段数相同（`/api/records/42/logs` 不会误配到 `/api/records/{id}`）。
# 只编译一次、进程内复用；227 条路由的正则匹配在微秒级，比每次请求都重建快得多。

_ROUTES: list[tuple[re.Pattern, dict, set[str]]] = []
_ROUTES_READY = False


def _template_to_regex(path: str) -> re.Pattern:
    """`/api/records/{id}/logs` → `^/api/records/[^/]+/logs$`"""
    parts = re.split(r"(\{[^}]+\})", path)
    out = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            out.append(r"[^/]+")
        else:
            out.append(re.escape(part))
    return re.compile("^" + "".join(out) + "$")


def build_index(app) -> None:
    """从 app 收集全部声明，编译成匹配表（幂等，重复调用会重建）。"""
    global _ROUTES_READY
    _ROUTES.clear()
    for path, methods, endpoint in iter_routes(app):
        if not path.startswith("/api"):
            continue
        meta = declaration_of(endpoint)
        if meta is None:
            continue
        _ROUTES.append((_template_to_regex(path), meta, methods))
    # 长模板优先：`/api/records/{id}/validate` 必须排在 `/api/records/{id}` 前面，
    # 否则更宽的模板会先命中（两者都是单段通配时长度一样，按段数降序更稳）。
    _ROUTES.sort(key=lambda item: -item[0].pattern.count("/"))
    _ROUTES_READY = True


def resolve(method: str, path: str) -> dict | None:
    """具体请求 → 它的声明（找不到返回 None）。"""
    if not _ROUTES_READY:
        # 没建索引（脚本/测试直接调用）→ 返回 None，让调用方回落到旧表，
        # 不要在这里抛异常：启动顺序不该成为权限判定的隐式依赖。
        return None
    upper = method.upper()
    for pattern, meta, methods in _ROUTES:
        if upper not in methods and not (upper == "HEAD" and "GET" in methods):
            continue
        if pattern.match(path):
            return meta
    return None


def default_level(method: str) -> str:
    """没有显式 `level` 时的缺省档位：GET/HEAD 读，其余写。"""
    return "read" if method.upper() in ("GET", "HEAD", "OPTIONS") else "write"


# ---------------------------------------------------------------------------
#  与旧判定表的一致性校验（迁移期用，第二步做完可以删）
# ---------------------------------------------------------------------------

def check_declarations(app, *, public_paths: set[str],
                       public_auth_paths: set[str]) -> list[str]:
    """比对「路由声明」与「旧判定表」，返回不一致项（空列表 = 一致）。

    只校验**已声明**的路由：迁移期允许一批还没写声明，此时它们仍由旧表判定，
    不算错。这样可以把 34 个模块分批改，每批合入时都能验证「声明与旧表同口径」。

    不一致的三种情况：

    - 声明 `module` 与 `module_for_path()` 给出的模块不同 → 有人写错了模块；
    - 声明 `level` 与 `required_level()` 不同 → 有人把写接口标成了只读（危险方向）；
    - 声明 `public` 与两张公开表不一致 → 要么漏登记公开路由，要么把私有路由标成公开。
    """
    from .capabilities import MODULE_CAPABILITIES, capabilities_for
    from .perm import module_for_path, required_level

    problems: list[str] = []
    for path, methods, endpoint in iter_routes(app):
        if not path.startswith("/api"):
            continue
        meta = declaration_of(endpoint)
        if meta is None:
            continue                        # 尚未声明：迁移期允许
        # ⚠️ 比对必须用**具体路径**：旧表的只读 POST 是正则（`/api/records/\d+/validate`），
        # 拿模板路径 `/api/records/{record_id}/validate` 去查匹配不到，会误判成 write
        # （实测踩过：回填脚本与这里先后都踩了同一个坑）。
        concrete = re.sub(r"\{[^}]+\}", "42", path)
        is_public = concrete in public_paths or concrete in public_auth_paths
        if meta["public"] != is_public:
            problems.append(
                f"{concrete}: 声明 public={meta['public']}，但公开表里{'有' if is_public else '没有'}它")
            continue
        if meta["public"]:
            continue
        expected_module = module_for_path(concrete)
        if expected_module != meta["module"]:
            problems.append(
                f"{concrete}: 声明 module={meta['module']!r}，旧表判为 {expected_module!r}")
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            expected_level = required_level(method, concrete)
            if meta["level"] is not None and meta["level"] != expected_level:
                problems.append(
                    f"{concrete} [{method}]: 声明 level={meta['level']!r}，旧表判为 {expected_level!r}")
                continue
            # 能力映射必须能算出非空集合；空集意味着切到能力判定后这条路由无人可授权。
            # `settings` 的读档曾为空，就是靠这条提前暴露；auth 不参与能力映射。
            module = meta["module"]
            if module in MODULE_CAPABILITIES:
                level = meta["level"] or expected_level
                if not capabilities_for(module, level):
                    problems.append(
                        f"{concrete} [{method}]: module={module!r} level={level!r} 没有能力可授")
                # 声明了具体能力：能力必须属于该模块，且**能力档位不得低于路由档位**
                # （写路由只声明只读能力 → 只读角色就能执行写操作，是危险方向）。
                capability = meta.get("capability")
                if capability is not None:
                    if capability not in MODULE_CAPABILITIES[module]:
                        problems.append(
                            f"{concrete} [{method}]: 能力 {capability!r} 不属于模块 {module!r}")
                    elif capability not in capabilities_for(module, level):
                        problems.append(
                            f"{concrete} [{method}]: module={module!r} level={level!r} 不包含能力 {capability!r}")
                    elif _LEVEL_RANK[level_for_capability(capability)] > _LEVEL_RANK[level]:
                        problems.append(
                            f"{concrete} [{method}]: 路由档位 {level!r} 低于能力 {capability!r} 所需档位")
    return problems
