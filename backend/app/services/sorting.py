# -*- coding: utf-8 -*-
"""列表排序的**唯一实现**（查询串 `?sort=&order=`）。

**为什么放在 services 层**：`api/*` 与 `services/*` 都要用它
（`services/record_logs.py::list_logs` 就是服务层直接排的）。
放 `api/` 会让 services **反向依赖 api**（层次倒置 + 循环 import 风险）；
放 `core/` 又会把 SQLAlchemy 引进本该与存储无关的领域层。故落在 services。

背景
----
列表页过去全是写死的 `order_by`（`id.desc()` / `barcode`）—— 编目员没法按题名、
出版年、编目员翻找，只能一页页翻。本模块给「需要按列排序」的列表一个统一入口，
前端用 Element Plus 的 `sortable="custom"` + `@sort-change` 联动。

两个必须守住的点
----------------
1. **白名单，不是拼接**：`sort` 是**用户输入**，只能在调用方给的 `columns` 字典里查。
   查不到就**回退默认** —— 绝不把字符串拼进 SQL（注入），也绝不报错
   （用户手改 URL 不该 500）。
2. **排序挂在筛选之后**：调用方先拿到**共用的筛选 query**
   （`records._record_query()` / `sources._resource_query()` …），再交给本模块排序。
   **别在这里另写 where** —— 否则「列表里看到的」与「按筛选整批删/转库的」不是同一批
   （实现说明：筛选口径唯一真源）。

分页稳定性（★ 容易被忽略）
--------------------------
只按"值会重复"的列排（题名、出版年、来源…）时，SQL **不保证同值行之间的相对顺序稳定**，
翻页就会出现**同一条重复出现、另一条永远看不到**。所以本模块**总是再追加一个唯一列
（默认主键）当 tie-breaker**（当主排序键本身就是它时不追加）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc


def apply_sort(query, columns: dict[str, Any], sort: str | None, order: str | None,
               *, default: str = "id", default_order: str = "desc",
               tiebreak: Any = None, then: tuple[str, ...] = ()):
    """给 `query` 追加 ORDER BY，返回新的 query。

    参数
    ----
    - `columns`：**可排序列的白名单**，形如 `{"title": RecordORM.title_norm}`。
      键就是前端 `sort` 参数的取值（与表格列的 `prop` 对齐）。
    - `sort` / `order`：来自查询串。`sort` 不在白名单 → 整对回退
      `default` + `default_order`。
    - `order`：`"asc"` / `"desc"`（大小写不敏感）；其它值按 `"asc"` 处理。
    - `tiebreak`：唯一列（通常主键），保证分页稳定；`None` = 不追加。
      ⚠️ 也可以传**表达式**（如 `HoldingORM.id.desc()`）—— 注销清单原本的
      tie-breaker 就是 `id DESC`（同秒注销的按新→旧），要原样保留就传表达式。
    - `then`：**默认排序时的次级键**（如条码总览原本是 `library_id, barcode`）。
      ⚠️ **只在"没给有效 sort"时生效** —— 用户点了某一列，就该只按那一列排，
      再掺次级键反而看不懂。这是为了**原样保留**加排序之前的默认顺序。
    - 空值**排在最后**（`nullslast`）：编目数据里出版年/来源常缺，让它们沉底
      比"降序时一堆空值顶在最上面"好用。

    返回的 query **仍可继续 `.offset()/.limit()`**。
    """
    key = (sort or "").strip()
    order_by = []
    if key in columns:
        col = columns[key]
        descending = (order or "").strip().lower().startswith("desc")
        order_by.append((desc(col) if descending else asc(col)).nullslast())
    else:
        col = columns[default]
        descending = default_order.strip().lower().startswith("desc")
        order_by.append((desc(col) if descending else asc(col)).nullslast())
        for k in then:
            order_by.append(asc(columns[k]).nullslast())

    if tiebreak is not None and col is not tiebreak:
        order_by.append(tiebreak)
    return query.order_by(*order_by)


def sortable_keys(columns: dict[str, Any]) -> list[str]:
    """白名单的键列表（供接口自检/测试断言用）。"""
    return list(columns)
