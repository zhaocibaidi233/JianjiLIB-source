from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

class ReaderAccount(Base):
    """读者门户账号。**与馆员 users 完全分离**，避免登录开关与权限表互相污染。"""
    __tablename__ = "reader_accounts"
    __table_args__ = (
        UniqueConstraint("reader_id", name="uq_reader_accounts_reader_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reader_id: Mapped[int] = mapped_column(
        ForeignKey("readers.id", ondelete="CASCADE",
                   name="fk_reader_accounts_reader_id_readers"),
        index=True)
    # 登录名 = 借书证号（小写）。**只在图书库内唯一**，不是全局唯一——
    # 借书证号本身只在库内唯一（uq_readers_library_card_no），两个库各自从
    # 0001 编起是正常状态。跨库同号由登录接口的「请先选择图书库」分支处理。
    username: Mapped[str] = mapped_column(String(32), index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReaderSession(Base):
    """读者门户会话，只存令牌哈希，有效期与馆员会话一致。"""
    __tablename__ = "reader_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    reader_id: Mapped[int] = mapped_column(
        ForeignKey("readers.id", ondelete="CASCADE",
                   name="fk_reader_sessions_reader_id_readers"),
        index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client: Mapped[str | None] = mapped_column(String(256))
