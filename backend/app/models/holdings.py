from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (DateTime, ForeignKey, Index, Integer, Numeric, String,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

STATUS_AVAILABLE = "AVAILABLE"     # 在架：进 905、进统计、可借


STATUS_WITHDRAWN = "WITHDRAWN"     # 已注销（剔旧 / 损毁）


STATUS_LOST = "LOST"               # 已遗失


STATUS_SUSPENDED = "SUSPENDED"     # 停流通/另库保存


HOLDING_STATUSES = (STATUS_AVAILABLE, STATUS_WITHDRAWN, STATUS_LOST,
                    STATUS_SUSPENDED)


INACTIVE_STATUSES = (STATUS_WITHDRAWN, STATUS_LOST)


class HoldingORM(Base):
    """馆藏（一种多册）。

    条码按图书库独立：多校管理下各校是独立实体，条码各自从 1 起编。
    故唯一性约束是 (library_id, barcode) 复合，而非 barcode 全局唯一。

    status 取四个处置值（AVAILABLE / WITHDRAWN / LOST / SUSPENDED）：
      AVAILABLE  在架 —— 进 905、进统计、可借
      WITHDRAWN  已注销（剔旧 / 损毁）—— 不进 905、不进统计，可恢复
      LOST       已遗失 —— 同上，可恢复
      SUSPENDED  停流通 / 另库保存—— 不进 905、
                 仍是馆藏资产，可 release 回在架，也可审批后 withdraw
    在借由 loans 的 ACTIVE 行派生，不在本列。
    """
    __tablename__ = "holdings"
    __table_args__ = (
        UniqueConstraint("library_id", "barcode", name="uq_holdings_library_barcode"),
        # 盘点「应盘」/ 财产登记 / 统计大量按 (图书库, 状态) 过滤，单列
        # library_id 索引后仍要回表按 status 过滤。2026-10-24 补。
        Index("ix_holdings_library_status", "library_id", "status"),
        # 个别登录/财产账按登录号查册；登录号按库使用。
        Index("ix_holdings_library_accession", "library_id", "accession_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    # 冗余自 records，供条码按库独立与按库查询（避免每次 join）。
    # 2026-10-06 补外键：裸 Integer 曾允许孤儿馆藏悄悄出现（迁移脚本里专门写过
    # 孤儿检测，说明真实发生过）；FK 后按库过滤/统计不会再混入幽灵数据。
    # 库删除受 API 守卫（有记录的库不让删），故 NO ACTION 即可、无需级联。
    # 显式命名（fk_...）：迁移 ce9e44899a63 用同名约束——create_all 与迁移的
    # 产物保持逐字节一致（无命名约定时 PG 会自动命名成 *_fkey，两边漂移）。
    library_id: Mapped[int] = mapped_column(
        ForeignKey("book_libraries.id", name="fk_holdings_library_id_book_libraries"),
        index=True)
    barcode: Mapped[str | None] = mapped_column(String(32))
    # 登录号（财产账逐册编号）。Phase 2 起独立成列；历史数据默认等于 barcode。
    accession_no: Mapped[str | None] = mapped_column(String(32))
    # 入藏信息（总括/个别登录）：Phase 2 起落册级；Record.price 只是书目级默认值。
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    source: Mapped[str | None] = mapped_column(String(64))
    accession_batch_no: Mapped[str | None] = mapped_column(String(32))
    location: Mapped[str | None] = mapped_column(String(64))
    # 排架号（纯手工架位号）。Phase 2 起以册级为准；record.shelf_no 只作默认值。
    shelf_no: Mapped[str | None] = mapped_column(String(64))
    call_no: Mapped[str | None] = mapped_column(String(64), index=True)   # 分类号/书次号[/卷册号]
    # 卷册号：v1.0 起就存在但**从未被写入**的孤儿列。 起复用为
    # 种级 records.volume 的**册级冗余镜像**（由写路径同步写入），不新增列。
    volume: Mapped[str | None] = mapped_column(String(32))

    # 注销 / 恢复（对标旧版系统「图书注销/恢复」：注销号 + 注销原因 + 可恢复）
    status: Mapped[str] = mapped_column(String(16), default=STATUS_AVAILABLE, index=True)
    withdraw_no: Mapped[str | None] = mapped_column(String(32))       # 注销号（一次操作一个号）
    withdraw_reason: Mapped[str | None] = mapped_column(String(64))   # 剔旧 / 损毁 / 遗失
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(256))           # 备注

    # 停流通/审查清理（D14）：不是注销，仍是馆藏资产；905 不含。
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_by: Mapped[str | None] = mapped_column(String(64))
    suspended_reason: Mapped[str | None] = mapped_column(String(256))
    review_batch_no: Mapped[str | None] = mapped_column(String(32))
    approval_ref: Mapped[str | None] = mapped_column(String(128))
    destination: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
