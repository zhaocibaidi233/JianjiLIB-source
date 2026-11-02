from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

READER_ACTIVE = "ACTIVE"           # 正常，可借


READER_SUSPENDED = "SUSPENDED"     # 暂停借阅（欠书 / 纪律原因），可归还


READER_INACTIVE = "INACTIVE"       # 注销（转学 / 离校），不删行


READER_STATUSES = (READER_ACTIVE, READER_SUSPENDED, READER_INACTIVE)


READER_TYPES = ("student", "teacher", "staff", "other")


LOAN_ACTIVE = "ACTIVE"


LOAN_RETURNED = "RETURNED"


LOAN_LOST = "LOST"                 # 遗失处理：结束借阅、馆藏转 LOST，不等同于归还


LOAN_STATUSES = (LOAN_ACTIVE, LOAN_RETURNED, LOAN_LOST)


LOAN_STATUS_LABELS = {
    LOAN_ACTIVE: "外借",
    LOAN_RETURNED: "已归还",
    LOAN_LOST: "已遗失",
}


FINE_UNPAID = "UNPAID"


FINE_PAID = "PAID"


FINE_WAIVED = "WAIVED"


FINE_STATUSES = (FINE_UNPAID, FINE_PAID, FINE_WAIVED)


RESERVATION_PENDING = "PENDING"


RESERVATION_READY = "READY"


RESERVATION_FULFILLED = "FULFILLED"


RESERVATION_CANCELLED = "CANCELLED"


RESERVATION_EXPIRED = "EXPIRED"


RESERVATION_ACTIVE_STATUSES = (RESERVATION_PENDING, RESERVATION_READY)


RESERVATION_STATUSES = (RESERVATION_PENDING, RESERVATION_READY,
                        RESERVATION_FULFILLED, RESERVATION_CANCELLED,
                        RESERVATION_EXPIRED)


RESERVATION_STATUS_LABELS = {
    RESERVATION_PENDING: "排队中",
    RESERVATION_READY: "待取书",
    RESERVATION_FULFILLED: "已外借",
    RESERVATION_CANCELLED: "已取消",
    RESERVATION_EXPIRED: "已过期",
}


class ReaderORM(Base):
    """读者档案（**不是登录账号**，见）。

    读者绝不能写进 `users` 表：`auth_enabled()` 的判据是「users 有没有行」，
    多一个读者账号会误触发整站登录，还会混进「最后一个管理员」守卫。
    """
    __tablename__ = "readers"
    __table_args__ = (
        # 借书证号按图书库独立（与条码同一口径：各校独立编号）
        UniqueConstraint("library_id", "card_no", name="uq_readers_library_card_no"),
        Index("ix_readers_library_status", "library_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id", name="fk_readers_library_id_book_libraries"),
        index=True)
    card_no: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    # student / teacher / staff / other（规则里的 types 键；label 可变，键不变）
    reader_type: Mapped[str] = mapped_column(String(16), default="student", index=True)
    grade: Mapped[str | None] = mapped_column(String(32))
    class_name: Mapped[str | None] = mapped_column(String(64), index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=READER_ACTIVE, index=True)
    note: Mapped[str | None] = mapped_column(String(256))
    # 未成年人监护人同意（PIPL 第 31 条）。没有出生日期，不自动推断年龄；
    # 由学校在发证/注册时记录同意来源。
    guardian_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    guardian_consent_by: Mapped[str | None] = mapped_column(String(64))
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LoanORM(Base):
    """借阅台账。

    `holding_id` / `record_id` / `reader_id` 都**不加外键**：册可能注销后删除、
    记录可能被删、读者只有状态没有硬删除，但台账必须留下来能独立读懂
    （与 `library_transfers` / `record_logs` 同一口径）。所以同时冗余
    barcode / title / 读者证号与姓名。

    「一册同时最多一条 ACTIVE」由**部分唯一索引**在数据库层保证：
    两台终端同时扫同一本时，API 的查重可能都通过，INSERT 会有一方失败 ——
    这是最后一道防线，不能只靠应用层 `if not exists`。
    """
    __tablename__ = "loans"
    __table_args__ = (
        Index("uq_loans_active_holding", "holding_id", unique=True,
              postgresql_where=text("status = 'ACTIVE'")),
        Index("ix_loans_reader_status", "reader_id", "status"),
        Index("ix_loans_library_status", "library_id", "status"),
        Index("ix_loans_due_on", "due_on"),
        # dashboard「近 14 天借还曲线」与流通统计的时段过滤都落在 loaned_at /
        # returned_at 上（api/dashboard.py、api/circulation.py），记录数增长后
        # 没有索引就是顺表扫描。2026-10-24 补。
        Index("ix_loans_loaned_at", "loaned_at"),
        Index("ix_loans_returned_at", "returned_at"),
        Index("ix_loans_library_fine_status", "library_id", "fine_status"),
        Index("ix_loans_library_compensation_status", "library_id",
              "compensation_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(Integer, index=True)
    holding_id: Mapped[int] = mapped_column(Integer, index=True)
    record_id: Mapped[int] = mapped_column(Integer)
    barcode: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    reader_id: Mapped[int] = mapped_column(Integer, index=True)
    reader_card_no: Mapped[str | None] = mapped_column(String(32))
    reader_name: Mapped[str | None] = mapped_column(String(64))
    reader_class: Mapped[str | None] = mapped_column(String(64))
    loaned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_on: Mapped[date] = mapped_column(Date)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renew_count: Mapped[int] = mapped_column(Integer, default=0)
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator_loan: Mapped[str | None] = mapped_column(String(64))
    operator_return: Mapped[str | None] = mapped_column(String(64))
    # 续借经办人：借出/归还有署名，续借原来只有时间戳——台账上「谁把这本续了
    # 15 天」查不到。只留**最近一次**续借的人，与
    # `last_renewed_at` 同一口径。
    operator_renew: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=LOAN_ACTIVE, index=True)
    note: Mapped[str | None] = mapped_column(String(256))
    # 罚款快照（还书时按当时规则算出，之后不随规则变化）；关闭罚款时不产生新金额，
    # 但已有金额保留 —— 重新开启后还能继续收款/核对。
    fine_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, server_default="0", nullable=False)
    fine_status: Mapped[str | None] = mapped_column(String(16))
    fine_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fine_operator: Mapped[str | None] = mapped_column(String(64))
    # 丢书处理：借阅结束为 LOST，馆藏转 LOST 并生成注销号。
    # 赔偿是**独立于逾期罚款**的一笔钱：罚款可选、赔偿始终可用；两者都挂在这条借阅上。
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_operator: Mapped[str | None] = mapped_column(String(64))
    lost_note: Mapped[str | None] = mapped_column(String(256))
    compensation_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, server_default="0", nullable=False)
    compensation_status: Mapped[str | None] = mapped_column(String(16))
    compensation_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    compensation_operator: Mapped[str | None] = mapped_column(String(64))


INV_OPEN = "OPEN"


INV_CLOSED = "CLOSED"


INV_STATUSES = (INV_OPEN, INV_CLOSED)


INV_OK = "OK"                    # 本库在架，正常扫到


INV_DUPLICATE = "DUPLICATE"      # 本次盘点已扫过


INV_ON_LOAN = "ON_LOAN"          # 本库册，但系统显示仍外借


INV_WITHDRAWN = "WITHDRAWN"      # 本库册，但已注销 / 已遗失


INV_OTHER_LIBRARY = "OTHER_LIBRARY"  # 条码属于其他图书库


INV_UNKNOWN = "UNKNOWN"          # 全库都没有这个条码


INV_RESULTS = (INV_OK, INV_DUPLICATE, INV_ON_LOAN, INV_WITHDRAWN,
               INV_OTHER_LIBRARY, INV_UNKNOWN)


INV_FOUND_RESULTS = (INV_OK, INV_ON_LOAN)   # 实物确实在架上


INV_PROBLEM_RESULTS = (INV_ON_LOAN, INV_WITHDRAWN,
                       INV_OTHER_LIBRARY, INV_UNKNOWN)


class InventorySessionORM(Base):
    """一次盘点。同一图书库同时最多一个 OPEN —— **数据库唯一索引兜底**。

    服务层有前置检查，但那是 check-then-act：两个窗口同时点「开始盘点」会都通过
    检查、各建一个 OPEN 会话，后续扫码落到哪个会话全看手速。部分唯一索引让第二个
    INSERT 直接失败，服务层再把它翻译成一句人话。
    """
    __tablename__ = "inventory_sessions"
    __table_args__ = (
        Index("ix_inventory_sessions_library_status", "library_id", "status"),
        Index("uq_inventory_sessions_open", "library_id", unique=True,
              postgresql_where=text("status = 'OPEN'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id",
                   name="fk_inventory_sessions_library_id_book_libraries"),
        index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default=INV_OPEN, index=True)
    # 建盘时的在架册数快照：盘点期间新编目的册不计入“应盘”，避免进度永远差几册。
    expected_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_by: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(256))
    # 结束时的清单快照（stats + 未扫到 / 外借 / 异常明细）。
    # 不存的话，盘点结束后再借书或注销，历史盘点的「未扫到」会跟着变 —— 账就活了。
    result_snapshot: Mapped[dict | None] = mapped_column(JSONB)


class InventoryScanORM(Base):
    """盘点扫描流水（**每次扫描都留一行**，重复扫描也留，便于查操作过程）。

    `holding_id` 不加外键：盘点台账要活过馆藏被删除的那一天。
    `title` / `call_no` / `location` / `reader_name` 都是扫描时的快照。
    """
    __tablename__ = "inventory_scans"
    __table_args__ = (
        Index("ix_inventory_scans_session_holding", "session_id", "holding_id"),
        Index("ix_inventory_scans_session_barcode", "session_id", "barcode"),
        Index("ix_inventory_scans_session_result", "session_id", "result"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_sessions.id", ondelete="CASCADE"), index=True)
    library_id: Mapped[int] = mapped_column(Integer, index=True)
    barcode: Mapped[str] = mapped_column(String(32))
    holding_id: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(Text)
    call_no: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(64))
    reader_name: Mapped[str | None] = mapped_column(String(64))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scanned_by: Mapped[str | None] = mapped_column(String(64))


class RecommendationORM(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("library_id", "record_id",
                         name="uq_recommendations_library_record"),
        Index("ix_recommendations_library_sort", "library_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id",
                   name="fk_recommendations_library_id_book_libraries"),
        index=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE",
                   name="fk_recommendations_record_id_records"),
        index=True)
    note: Mapped[str | None] = mapped_column(String(256))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# --- 荐购 / 受赠登记（教材〔2021〕2号；契约 D17）--------------------------------
# Recommendation 只是读者侧的展示位，**不是**采购/准入。荐购与捐赠走本表：
# 记录来源、走「初选—审议—确认—备案」，捐赠还要求来源审核通过才能确认。
ACQ_PROPOSED = "PROPOSED"     # 初选（登记）
ACQ_REVIEWED = "REVIEWED"     # 审议
ACQ_APPROVED = "APPROVED"     # 确认
ACQ_FILED = "FILED"           # 备案
ACQ_REJECTED = "REJECTED"     # 不予采纳
ACQ_STAGES = (ACQ_PROPOSED, ACQ_REVIEWED, ACQ_APPROVED, ACQ_FILED, ACQ_REJECTED)
ACQ_STAGE_LABELS = {
    ACQ_PROPOSED: "初选",
    ACQ_REVIEWED: "审议",
    ACQ_APPROVED: "确认",
    ACQ_FILED: "备案",
    ACQ_REJECTED: "不予采纳",
}

ACQ_READER = "READER"         # 读者荐购
ACQ_TEACHER = "TEACHER"       # 教师推荐
ACQ_DONATION = "DONATION"     # 捐赠
ACQ_PURCHASE = "PURCHASE"     # 采购建议
ACQ_SOURCE_TYPES = (ACQ_READER, ACQ_TEACHER, ACQ_DONATION, ACQ_PURCHASE)
ACQ_SOURCE_LABELS = {
    ACQ_READER: "读者荐购",
    ACQ_TEACHER: "教师推荐",
    ACQ_DONATION: "捐赠",
    ACQ_PURCHASE: "采购建议",
}


class AcquisitionRequestORM(Base):
    """荐购/受赠登记。不是订单，也不直接产生书目或馆藏。"""
    __tablename__ = "acquisition_requests"
    __table_args__ = (
        Index("ix_acquisition_requests_library_stage", "library_id", "stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id",
                   name="fk_acquisition_requests_library_id_book_libraries"),
        index=True)
    title: Mapped[str] = mapped_column(String(256))
    author: Mapped[str | None] = mapped_column(String(128))
    isbn: Mapped[str | None] = mapped_column(String(32))
    publisher: Mapped[str | None] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(16), default=ACQ_READER)
    # 推荐人/捐赠方：捐赠未填来源不得确认（见 services.acquisitions）。
    source_name: Mapped[str | None] = mapped_column(String(128))
    contact: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(String(512))
    stage: Mapped[str] = mapped_column(String(16), default=ACQ_PROPOSED, index=True)
    # 捐赠来源审核（教材〔2021〕2号：来源未审核不得上架）。
    source_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_checked_by: Mapped[str | None] = mapped_column(String(64))
    source_check_note: Mapped[str | None] = mapped_column(String(256))
    review_note: Mapped[str | None] = mapped_column(String(512))
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_ref: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReservationORM(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        Index("uq_reservations_active", "library_id", "record_id", "reader_id",
              unique=True,
              postgresql_where=text("status IN ('PENDING', 'READY')")),
        Index("ix_reservations_library_status", "library_id", "status",
              "reserved_at"),
        Index("ix_reservations_reader_status", "reader_id", "status"),
        Index("ix_reservations_record_status", "record_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id",
                   name="fk_reservations_library_id_book_libraries"),
        index=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE",
                   name="fk_reservations_record_id_records"),
        index=True)
    reader_id: Mapped[int] = mapped_column(
        ForeignKey("readers.id", name="fk_reservations_reader_id_readers"),
        index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=RESERVATION_PENDING, index=True)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(String(256))
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LibraryTransfer(Base):
    """图书转库台账——**每册一行**。

    为什么必须单独建表而不是只改数据：转库是不可逆的物理搬书动作，且重新编号
    （`barcode_mode=renumber`）后**原条码就消失了**。没有台账就回答不了
    「这批书什么时候、从哪个库转到哪个库、原来是什么号」——而财产登记账这类
    上级检查用的账目恰恰要能回答这个问题。

    `record_id` / `holding_id` 是**转出前**的 id：转库后原记录往往已被删除
    （册全部转走就该删，否则留下无条码记录），所以这里**不加外键**——
    台账要活过它记录的那些行。
    """
    __tablename__ = "library_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 一次操作一个批号（与注销号同构：`YYYYMMDD-序号`），把同批次的册串起来
    batch_no: Mapped[str] = mapped_column(String(32), index=True)
    from_library_id: Mapped[int] = mapped_column(Integer, index=True)
    to_library_id: Mapped[int] = mapped_column(Integer, index=True)
    record_id: Mapped[int] = mapped_column(Integer)          # 转出前的记录 id
    to_record_id: Mapped[int | None] = mapped_column(Integer)  # 转入后挂靠的记录 id
    holding_id: Mapped[int] = mapped_column(Integer)         # 转出前的馆藏 id
    old_barcode: Mapped[str | None] = mapped_column(String(32))
    new_barcode: Mapped[str | None] = mapped_column(String(32))
    old_call_no: Mapped[str | None] = mapped_column(String(64))
    new_call_no: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)          # 转出时快照，供人读
    operator: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
