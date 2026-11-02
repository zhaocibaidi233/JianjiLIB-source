# -*- coding: utf-8 -*-
"""读者隐私：监护人同意记录与匿名化（契约 C-19 / D15）。

不物理删除 Reader：借阅历史、统计、审计还要用；匿名化清掉可识别字段，
保留 reader_type/grade 等匿名维度。**不 commit**。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (LOAN_ACTIVE, READER_INACTIVE, RESERVATION_ACTIVE_STATUSES,
                      LoanORM, ReaderAccount, ReaderORM, ReaderSession,
                      ReservationORM)


def _lock_reader(db: Session, reader: ReaderORM | None) -> ReaderORM:
    if reader is None or reader.id is None:
        raise ValueError("读者不存在")
    row = (db.query(ReaderORM).filter(ReaderORM.id == reader.id)
           .populate_existing().with_for_update().one_or_none())
    if row is None:
        raise ValueError("读者不存在")
    return row


def set_guardian_consent(db: Session, reader: ReaderORM, *, consent: bool,
                         by: str | None = None) -> ReaderORM:
    """记录/撤销监护人同意。没有出生日期，不自动推断是否未成年。"""
    row = _lock_reader(db, reader)
    if consent:
        row.guardian_consent_at = datetime.now(timezone.utc)
        row.guardian_consent_by = (by or "").strip()[:64] or None
    else:
        row.guardian_consent_at = None
        row.guardian_consent_by = None
    db.flush()
    return row


def anonymize_reader(db: Session, reader: ReaderORM, *,
                     operator: str | None = None) -> dict:
    """注销后的读者匿名化：清姓名/电话/班级，禁用门户账号，改匿名证号。

    - 只允许 INACTIVE；有 ACTIVE Loan / 预约时拒绝（先把业务收尾）。
    - 借阅台账的 reader_name/card_no/class 快照同步改匿名，历史不可反推到人。
    - 证书号改为 `ANON-{id}`；读者账号 username 改 `anon-{id}` 并禁用。
    """
    row = _lock_reader(db, reader)
    if row.status != READER_INACTIVE:
        raise ValueError("只有已注销的读者才能匿名化")
    if (db.query(func.count(LoanORM.id))
            .filter(LoanORM.reader_id == row.id,
                    LoanORM.status == LOAN_ACTIVE).scalar() or 0) > 0:
        raise ValueError("该读者还有未归还的借阅，不能匿名化")
    if (db.query(func.count(ReservationORM.id))
            .filter(ReservationORM.reader_id == row.id,
                    ReservationORM.status.in_(RESERVATION_ACTIVE_STATUSES))
            .scalar() or 0) > 0:
        raise ValueError("该读者还有活动预约，不能匿名化")

    anon_card = f"ANON-{row.id}"
    loans = (db.query(LoanORM).filter(LoanORM.reader_id == row.id)
             .update({LoanORM.reader_name: "匿名读者",
                      LoanORM.reader_card_no: anon_card,
                      LoanORM.reader_class: None},
                     synchronize_session=False))
    account = (db.query(ReaderAccount)
               .filter(ReaderAccount.reader_id == row.id).one_or_none())
    if account is not None:
        account.enabled = False
        account.username = f"anon-{row.id}"
        account.password_hash = "!"
    db.query(ReaderSession).filter(ReaderSession.reader_id == row.id).delete(
        synchronize_session=False)

    row.card_no = anon_card
    row.name = "匿名读者"
    row.phone = None
    row.note = None
    row.class_name = None
    row.anonymized_at = datetime.now(timezone.utc)
    db.flush()
    return {"reader_id": row.id, "card_no": anon_card,
            "loans_anonymized": int(loans or 0),
            "anonymized_at": row.anonymized_at.isoformat(),
            "operator": operator}
