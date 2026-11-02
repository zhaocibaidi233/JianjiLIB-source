# -*- coding: utf-8 -*-
"""响应模型（`response_model`）：让接口的返回结构变成**契约**。

## 为什么需要

改造前 227 个操作 **0 个 `response_model`**：接口返回裸 dict，FastAPI 不知道结构，
OpenAPI 里没有字段定义，前端只能手写 interface。后果是**字段改名要等运行时才炸**——
渲染成空白、不报错，用户先发现。

加了 `response_model` 之后：

1. OpenAPI 里有字段定义 → 可以用 `openapi-typescript` 生成 TS 类型；
2. 后端改字段名 → 前端 `npm run typecheck` **编译失败**；
3. 顺带拦住"不小心把内部字段返回出去"（响应模型会过滤掉未声明的字段）。

## 为什么分批做，不做全量

227 个操作一次加完，等于一次不可回滚的改造。而且**加 `response_model` 会暴露现有
字段的真实形状**（`None` 与缺字段混用、数字与字符串混用），第一次加往往要顺手修几处。
所以按 `docs/11` 的方案：先 Top 20（最高频、最影响前端类型），验证收益后再铺开。

## 约定

- 列表统一用 `Page[T]`：`{total, page, page_size, items[]}`——全站一致，
  前端 `usePagedList` 才能只写一份；
- 时间一律 `str`（ISO 格式）：接口现在就是 `str(...)` 出来的，不要在这里改成
  `datetime`——那会改变 JSON 形状（带不带时区、微秒位数），前端要跟着改；
- 可选字段显式 `| None` 并给默认 `None`：**不要**用"缺字段"表示空，
  那正是现在前端要写一堆 `?.` 的原因。
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """分页响应的统一外壳（全站列表接口都用这个形状）。"""

    total: int = Field(description="满足筛选条件的总条数（不是本页条数）")
    page: int = Field(ge=1, description="页码，从 1 起")
    page_size: int = Field(ge=1, description="每页条数")
    items: list[T] = Field(description="本页数据")


class RecordListItem(BaseModel):
    """图书库列表的一行（`GET /api/records`）。

    字段与 `api/records.py::list_records` 的返回逐字对应；加模型时**不能顺手删字段**
    ——`response_model` 会把未声明的字段过滤掉，删一个前端就少一个。
    """

    id: int
    title: str | None = None
    isbn: str | None = None
    author: str | None = None
    #: `records.year` 是 SmallInteger（不是字符串）。第一版写成 `str` 直接炸了
    #: ResponseValidationError —— 这正是加 response_model 的价值：把"字段到底是什么
    #: 类型"从口头约定变成会报错的事实。
    year: int | None = None
    class_no: str | None = None
    source: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    volume: str | None = None
    shelf_no: str | None = None
    pages: str | None = None
    size: str | None = None
    item_type: str | None = None
    reader_level: str | None = None
    call_no: str | None = None
    barcodes: list[str] = []
    active_count: int = 0
    total_count: int = 0


class RecordPage(Page[RecordListItem]):
    """`GET /api/records` 的完整响应。"""


class ReaderListItem(BaseModel):
    """读者列表的一行（`GET /api/readers`）。

    由 `services/circulation.py::reader_dict()` + 两个统计字段拼成。
    """

    id: int
    library_id: int | None = None
    card_no: str
    name: str
    reader_type: str | None = None
    reader_type_label: str | None = None
    grade: str | None = None
    class_name: str | None = None
    phone: str | None = None
    status: str | None = None
    status_label: str | None = None
    note: str | None = None
    guardian_consent_at: str | None = None
    guardian_consent_by: str | None = None
    anonymized_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    #: 在借册数 / 逾期册数（列表接口额外聚合，不在 reader_dict 里）
    active_count: int = 0
    overdue_count: int = 0


class ReaderPage(Page[ReaderListItem]):
    """`GET /api/readers` 的完整响应。"""


class LoanItem(BaseModel):
    """借阅记录（`GET /api/circulation/loans`，也用于借出/归还的返回）。

    字段与 `services/circulation.py::loan_dict()` 逐字对应。金额用 `float`
    （`loan_dict` 里就是 `float(...)`），不要在模型里改成 `Decimal`——
    Pydantic 会把它序列化成字符串，前端 `toFixed` 直接炸。
    """

    id: int
    library_id: int | None = None
    holding_id: int | None = None
    record_id: int | None = None
    barcode: str | None = None
    title: str | None = None
    reader_id: int | None = None
    reader_card_no: str | None = None
    reader_name: str | None = None
    reader_class: str | None = None
    loaned_at: str | None = None
    due_on: str | None = None
    returned_at: str | None = None
    renew_count: int = 0
    last_renewed_at: str | None = None
    operator_loan: str | None = None
    operator_return: str | None = None
    operator_renew: str | None = None
    status: str | None = None
    status_label: str | None = None
    note: str | None = None
    overdue: bool = False
    days_overdue: int = 0
    days_overdue_at_return: int = 0
    fine_amount: float = 0.0
    fine_status: str | None = None
    fine_status_label: str | None = None
    fine_settled_at: str | None = None
    fine_operator: str | None = None
    lost_at: str | None = None
    lost_operator: str | None = None
    lost_note: str | None = None
    compensation_amount: float = 0.0
    compensation_status: str | None = None
    compensation_status_label: str | None = None
    compensation_settled_at: str | None = None
    compensation_operator: str | None = None


class LoanPage(Page[LoanItem]):
    """`GET /api/circulation/loans` 的完整响应。"""


class HoldingListItem(BaseModel):
    """馆藏列表的一行（`GET /api/holdings`）。

    比 `_json()` 多出 join 来的书目字段（title/isbn/class_no/shelf_no）与借阅状态
    （on_loan/loan_id/reader_name/...）——列表页要在一屏里看到"这册在谁手上"。
    """

    id: int
    barcode: str
    record_id: int | None = None
    library_id: int | None = None
    location: str | None = None
    call_no: str | None = None
    volume: str | None = None
    status: str | None = None
    withdraw_no: str | None = None
    withdraw_reason: str | None = None
    withdrawn_at: str | None = None
    withdrawn_by: str | None = None
    title: str | None = None
    isbn: str | None = None
    class_no: str | None = None
    shelf_no: str | None = None
    created_at: str | None = None
    on_loan: bool = False
    loan_id: int | None = None
    reader_name: str | None = None
    reader_card_no: str | None = None
    due_on: str | None = None


class HoldingPage(Page[HoldingListItem]):
    """`GET /api/holdings` 的完整响应。"""


class RecordBrief(BaseModel):
    """体检类列表的一行（`api/inspect.py::_brief`）。

    比 `RecordListItem` 精简：体检只关心"哪条记录有问题"，不需要条码列表与册数。
    五个体检接口（无条码/无分类号/分类号待补/同书异号/异书同号/905 漂移）共用它。
    """

    id: int
    library_id: int | None = None
    title: str | None = None
    isbn: str | None = None
    author: str | None = None
    class_no: str | None = None
    year: int | None = None
    source: str | None = None


class RecordBriefPage(Page[RecordBrief]):
    """体检类列表的完整响应。"""


class ReservationReader(BaseModel):
    """预约行里内嵌的读者摘要（不是完整读者对象）。"""

    id: int | None = None
    card_no: str | None = None
    name: str | None = None
    class_name: str | None = None
    phone: str | None = None


class ReservationItem(BaseModel):
    """预约队列的一行（`services/reservations.py::serialize_many`）。"""

    id: int
    library_id: int | None = None
    record_id: int | None = None
    reader_id: int | None = None
    status: str | None = None
    status_label: str | None = None
    reserved_at: str | None = None
    ready_at: str | None = None
    expires_at: str | None = None
    fulfilled_at: str | None = None
    cancelled_at: str | None = None
    cancel_reason: str | None = None
    note: str | None = None
    seconds_left: int | None = None
    queue_position: int | None = None
    available_count: int = 0
    title: str | None = None
    author: str | None = None
    class_no: str | None = None
    isbn: str | None = None
    call_no: str | None = None
    reader: ReservationReader | None = None


class ReservationPage(Page[ReservationItem]):
    """`GET /api/reservations` 的完整响应。

    ⚠️ 这个接口比别的列表多一个 `summary` 字段（见 `api/reservations.py`），
    这里一并声明——**分页外壳并不全站一致**，铺开时要逐个确认。
    """

    summary: dict = {}


class InventorySession(BaseModel):
    """一次盘点会话（`services/inventory.py::session_dict`）。

    `stats` 是动态统计（应盘/已扫/缺失/外借…），键随盘点阶段变化，
    这里保留 `dict` —— 强行定死会把以后新增的统计项过滤掉。
    """

    id: int
    library_id: int | None = None
    name: str | None = None
    status: str | None = None
    status_label: str | None = None
    expected_count: int = 0
    started_at: str | None = None
    started_by: str | None = None
    closed_at: str | None = None
    closed_by: str | None = None
    note: str | None = None
    stats: dict = {}
    truncated: bool | None = None


class InventorySessionList(BaseModel):
    """`GET /api/inventory/sessions` 的响应。

    ⚠️ 这个接口**没有分页**（返回 `{items: [...]}` 而不是 `Page`）——
    另一个"分页外壳不统一"的例子。
    """

    items: list[InventorySession] = []


class ResourceRecordItem(BaseModel):
    """资源库记录（`GET /api/resource-libraries/{id}/records`）。"""

    id: int
    title: str | None = None
    isbn: str | None = None
    author: str | None = None
    year: int | None = None
    class_no: str | None = None
    source_tag: str | None = None
    updated_at: str | None = None


class ResourceRecordPage(Page[ResourceRecordItem]):
    """资源库记录列表的完整响应。"""


class BookCard(BaseModel):
    """读者门户的书目卡片（`services/recommendations.py::cards_for_records`）。

    `extra="allow"`：卡片允许调用方追加字段（`curated_cards` 会塞
    `recommendation_id` / `note`）。定死结构会把那些字段过滤掉——
    这是"加 response_model 反而弄坏功能"的典型形态，所以这里显式允许扩展。
    """

    model_config = ConfigDict(extra="allow")

    record_id: int
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    year: int | None = None
    class_no: str | None = None
    isbn: str | None = None
    call_no: str | None = None
    shelf_no: str = ""
    active_count: int = 0
    available_count: int = 0


class BookCardPage(Page[BookCard]):
    """`GET /api/portal/books` 的完整响应。"""


class UserItem(BaseModel):
    """账号（`services/auth.py::user_public`）。

    ⚠️ `last_login_at` / `created_at` 在这里是 **datetime**，不是字符串——
    `user_public` 用 `_aware()` 返回时间对象，Pydantic 会序列化成 ISO 串。
    别的接口（`reader_dict` 等）用的是 `str(...)`，**两类不能混**：
    混了就会出现"有的接口带时区、有的不带"。
    """

    id: int
    username: str
    display_name: str | None = None
    enabled: bool = True
    role_id: int | None = None
    role_name: str | None = None
    role_title: str | None = None
    permissions: dict = {}
    levels: dict = {}
    is_admin: bool = False
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class UserList(BaseModel):
    """`GET /api/users` 的响应（无分页）。"""

    items: list[UserItem] = []


class RoleItem(BaseModel):
    """角色（`GET /api/roles`）。与 `services/auth.py::role_public` 逐字对应。

    ⚠️ `levels` 是**摊平后的模块档位**（`{模块: none|read|write}`），前端角色卡
    用它算「可写 N 个模块 / 只读 N 个模块」。第一版漏了这个字段 → `response_model`
    把它过滤掉 → 前端 `Object.values(undefined)` 抛异常 → **整个角色标签页白屏**。
    加 `response_model` 时少写一个字段，界面就整块崩 —— 这是最贵的教训。
    """

    id: int
    name: str
    title: str
    permissions: dict = {}
    levels: dict = {}
    builtin: bool = False
    note: str | None = None
    user_count: int = 0


class RoleList(BaseModel):
    """`GET /api/roles` 的响应（无分页）。"""

    items: list[RoleItem] = []
