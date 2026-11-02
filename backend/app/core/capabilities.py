# -*- coding: utf-8 -*-
"""能力（capability）登记表：现有模块键 → 能力集合。

## 这一层为什么存在

契约 §12.1 把权限的长期单位定为**能力**，而 v1.0 的授权单位是**模块**
（`core/perm.py::MODULE_KEYS`，侧栏一项一个模块，`none/read/write` 三档）。
两者要能对照，就必须有一张**唯一**的映射表；散在判定代码里写 `if module ==
"barcodes"` 那种分支，迟早出现"同一个模块在三个地方映射成三套能力"。

本文件登记名字，并提供"模块 + 档位 → 能力"的纯函数。`perm.py` 的角色授权仍按
模块档位存储（v1.0 兼容层），路由可用 `@api(capability=...)` 声明某个操作的具体能力；
此时判定先由能力算出所需档位，再查角色档位。未声明能力的路由继续按 module+level 判。
这样敏感操作（停流通、匿名化、登录账）在代码里就写明需要哪个能力，不再只靠模块归属。

## 纯函数，零依赖

本文件**不 import 任何东西**（连 `perm` 都不 import）：`perm` 会牵出 `models` →
`sqlalchemy`，而这张表是纯数据 + 纯函数，没有理由为它拉起 ORM。代价是"是否覆盖全部
模块键"不能在这里 assert，改由 `tests/test_capabilities.py` 逐项比对
`perm.MODULE_KEYS` —— 少一个键测试就红，效果一样。

## 档位（read / write）

模块档位只有两档，能力却有三类命名：

- `xxx.read` / `xxx.write`：天然分档；
- 不带 `.write` 后缀的（`admin.settings` / `admin.user` / `admin.audit` /
  `reader.anonymize` / `reader.account.manage` / `report.accession`）：按语义归到某一档。
  归类见 `READ_CAPABILITIES`，**判据是"这个能力会不会改库"**：

  - 归只读档：`admin.audit`（看审计）、`report.accession`（看登录账，
    `/api/accession` 只有 GET）、`admin.settings.read`（读条码位数等非敏感设置）；
  - 归写档：`admin.settings`（改设置）、`admin.user`、`reader.anonymize`、
    `reader.account.manage`。

  设置拆读/写是为了避免"编目页读条码位数"被迫授予改设置权限。

`level="write"` 返回该模块的**全部**能力（含只读的那些），保证 `write ⊇ read`。
"""
from __future__ import annotations

#: 全部能力名。逐字对齐契约 §12.1 的表：不增、不减、不改名。
CAPABILITIES: frozenset[str] = frozenset((
    # 编目：书目 / 馆藏 / 停流通与恢复流通
    "catalog.record.read", "catalog.record.write",
    "catalog.item.read", "catalog.item.write",
    "catalog.item.suspend", "catalog.item.release",
    # 流通：借还 / 预约 / 盘点 / 转库
    "circ.loan.read", "circ.loan.write",
    "circ.hold.read", "circ.hold.write",
    "circ.inventory.read", "circ.inventory.write",
    "circ.transfer.read", "circ.transfer.write",
    # 读者：档案 / 匿名化 / 账号
    "reader.read", "reader.write",
    "reader.anonymize", "reader.account.manage",
    # 报表：统计与财产登记 / 登录账
    "report.read", "report.accession",
    # 管理：设置 / 用户与凭据 / 审计
    "admin.settings.read", "admin.settings", "admin.user", "admin.audit",
))

#: 只读档能力。`level="read"` 只返回这些；其余一律归写档。
#:
#: 判据：**执行它不会改变库里的数据**。无后缀的只读能力单列在末尾；`admin.settings`
#: 是改设置，不在只读档，读设置另用 `admin.settings.read`。
READ_CAPABILITIES: frozenset[str] = frozenset((
    "catalog.record.read",
    "catalog.item.read",
    "circ.loan.read",
    "circ.hold.read",
    "circ.inventory.read",
    "circ.transfer.read",
    "reader.read",
    "report.read",
    # 无后缀但只读：审计日志只查不改；登录账 `/api/accession` 只有 GET。
    "admin.audit",
    "report.accession",
    # 设置拆读/写：编目页要读条码位数等非敏感项，不应因此获得改设置的权限。
    "admin.settings.read",
))

#: 允许的档位，与 `core/route_meta.py::LEVELS` 同值（那边是路由声明用的）。
LEVELS: tuple[str, ...] = ("read", "write")

#: 模块键 → 能力元组。**必须覆盖 `perm.MODULE_KEYS` 的每一个键**（由测试钉住）。
#:
#: 排列顺序无功能意义，只为阅读：先只读、后写。模块键的顺序与 `perm.MODULES`
#: 的侧栏顺序一致，方便与 `modules_catalog()` 对照。
MODULE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    # 图书库与编目：/api/records、/api/cataloging、/api/quick-entry、/api/marc、
    # /api/lookup、/api/dedup、/api/ai、/api/record-logs，**外加 /api/transfers**。
    # 转库挂在编目模块下（`perm.MODULES` 的注释：搬的是记录与馆藏，不是图书库设置），
    # 故这里要带 `circ.transfer.*`，否则"能转库"在能力模型里无处表达。
    "records": (
        "catalog.record.read", "catalog.record.write",
        "catalog.item.read", "catalog.item.write",
        "circ.transfer.read", "circ.transfer.write",
    ),
    # 条码管理：/api/holdings —— 建册、改册、注销、恢复；Phase 1 起停流通/释放也走这里。
    # `suspend`/`release` 单列而不并进 `item.write`：审查清理是独立授权点（§12.1）。
    "barcodes": (
        "catalog.item.read", "catalog.item.write",
        "catalog.item.suspend", "catalog.item.release",
    ),
    # 书标打印：只读馆藏数据生成标签，不写库 → 单一只读能力。
    "labels": ("catalog.item.read",),
    # 借还台：/api/circulation、/api/inventory、/api/reservations、/api/recommendations。
    # 盘点与借还同模块（v1.0 的裁定），故三个子系统一起登记。
    "circulation": (
        "circ.loan.read", "circ.loan.write",
        "circ.hold.read", "circ.hold.write",
        "circ.inventory.read", "circ.inventory.write",
    ),
    # 读者管理：档案（含敏感字段）+ 匿名化 + 读者账号。
    "readers": (
        "reader.read", "reader.write",
        "reader.anonymize", "reader.account.manage",
    ),
    # 资源库：套录用的外部书目记录，本质仍是书目数据（不是本馆馆藏）。
    "resources": ("catalog.record.read", "catalog.record.write"),
    # 导入导出：导入建书目+册，导出读同一批数据。
    "io": (
        "catalog.record.read", "catalog.record.write",
        "catalog.item.read", "catalog.item.write",
    ),
    # 数据体检：只读巡检（905 漂移、孤儿馆藏等），不改数据。
    "inspect": ("catalog.record.read", "catalog.item.read"),
    # 字典中心：编目用的规范数据。契约 §12.1 **没有** `catalog.refdata.*`，
    # 字典中心暂按"书目数据的词表"归到 `catalog.record.*`。
    # ⚠️ 已知缺口：这会让"改字典"与"改书目"共用同一能力。补了 refdata 后
    # 只改这一行即可（调用方不用动）。
    "dictionaries": ("catalog.record.read", "catalog.record.write"),
    # 统计报表：/api/stats、/api/property-register、/api/accession。
    "stats": ("report.read", "report.accession"),
    # 全局设置：/api/settings、/api/book-libraries、/api/sources。
    # 读/写拆开：编目页要读条码位数等非敏感项，不应因此获得改设置权限。
    "settings": ("admin.settings.read", "admin.settings"),
    # 用户管理：/api/users、/api/roles、/api/agent-tokens、/api/agent-audit、/api/maintenance。
    # 读档只有 `admin.audit`（看审计）；看账号列表本身需要 `admin.user`（敏感）。
    "users": ("admin.user", "admin.audit"),
}


def capabilities_for(module: str, level: str = "read") -> frozenset[str]:
    """某模块在某档位下应有的能力集合。

    - `level="read"`：只返回只读能力（`READ_CAPABILITIES` 里的）；
    - `level="write"`：返回该模块的全部能力（含只读的，保证 `write ⊇ read`）。

    未知模块或未知档位抛 `ValueError` —— 拼错的模块键若静默返回空集，会表现为
    "谁都进不去"或"谁都能进"，两种都比启动时炸掉难查。
    """
    caps = MODULE_CAPABILITIES.get(module)
    if caps is None:
        raise ValueError(f"未知模块 {module!r}")
    if level not in LEVELS:
        raise ValueError(f"level 只能是 {LEVELS}，收到 {level!r}")
    if level == "write":
        return frozenset(caps)
    return frozenset(c for c in caps if c in READ_CAPABILITIES)


def has_capability(module: str, level: str, capability: str) -> bool:
    """在 `module` 的 `level` 档位下，是否具备 `capability`。

    能力名不在 `CAPABILITIES` 里同样抛 `ValueError`（而不是返回 False）：拼错的能力名
    如果静默返回 False，表现是"权限莫名不够"；抛错才能在上线前暴露（与 `perm.py`
    "宁可响亮地坏掉"的口径一致）。
    """
    if capability not in CAPABILITIES:
        raise ValueError(f"未知能力 {capability!r}")
    return capability in capabilities_for(module, level)


def level_for_capability(capability: str) -> str:
    """执行该能力所需的最低档位：只读能力 `read`，其余 `write`。

    路由声明 `capability` 后，判定用它算所需档位；声明与路由的实际档位是否相容
    由启动检查（`route_meta.check_declarations`）保证 —— 写路由不能只要求只读能力。
    """
    if capability not in CAPABILITIES:
        raise ValueError(f"未知能力 {capability!r}")
    return "read" if capability in READ_CAPABILITIES else "write"
