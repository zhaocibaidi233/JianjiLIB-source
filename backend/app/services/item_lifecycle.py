# -*- coding: utf-8 -*-
"""Item（馆藏册）处置状态变更的**唯一入口**。

契约依据：`实现说明`
  §5.2 Item、§7.1 Item 处置状态、§8.1 命令目录（CMD-06/07/21/22）、
  §9.1 不变量（INV-05/06/22/26）、§15.7 停流通/审查清理、§19 D2/D14。

职责边界（Phase 1，不改表结构）：

  * 只写 `holdings.status` 与既有的注销字段（`withdraw_no/reason/at/by`）。
    停流通需要的 `suspended_at/by`、`review_batch_no`、`approval_ref`、
    `destination` 现表里**还没有列**（§15.7 的「扩」在 Phase 2 才做），
    因此这些值只进 `ItemTransitionResult.audit`，由调用方决定落审计表。
  * **不重建 905**、不推进预约队列、不写赔偿。905/预约是投影与跨聚合副作用，
    由调用方在自己的事务里处理；赔偿仍归 `services.circulation.mark_lost`。
  * **不 commit**（§9.3）：只 `flush()`，事务边界归 API / 命令处理器。
  * 不改 Loan 状态。`mark_lost_status` 只做册的处置状态，
    借阅行的收尾与赔偿是 `circulation.mark_lost` 的职责。

并发（§9.2）：所有变更命令先 `SELECT ... FOR UPDATE` 重读该册再判定状态，
与 `services.circulation` 的锁顺序（Record → Item → Loan）兼容 ——
外借路径先锁书目再锁馆藏，本模块只锁馆藏，不构成环。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import (HoldingORM, LoanORM, LOAN_ACTIVE, STATUS_AVAILABLE,
                      STATUS_LOST, STATUS_SUSPENDED, STATUS_WITHDRAWN)

# 契约 §5.2 生命周期的起点。Phase 1 还没有写入方（`complete_cataloging` 属
# Phase 3），models 里也不放这个值 —— 这里只用于 WithdrawItem 的前置状态判定。
STATUS_IN_PROCESS = "IN_PROCESS"

STATUS_LABELS = {
    STATUS_AVAILABLE: "在架",
    STATUS_IN_PROCESS: "编目中（未上架）",
    STATUS_SUSPENDED: "停流通",
    STATUS_WITHDRAWN: "已注销",
    STATUS_LOST: "已遗失",
}

# 可以注销的处置状态（§7.1：AVAILABLE / SUSPENDED / IN_PROCESS → WITHDRAWN）
WITHDRAWABLE_STATUSES = (STATUS_AVAILABLE, STATUS_SUSPENDED, STATUS_IN_PROCESS)

# 可以恢复到在架的状态（§7.1：WITHDRAWN / LOST → AVAILABLE）
RESTORABLE_STATUSES = (STATUS_WITHDRAWN, STATUS_LOST)


@dataclass(frozen=True)
class ItemTransitionResult:
    """一次处置状态变更的结果。

    `audit` 是「这次变更该记什么」的完整载荷（含 Phase 2 才会有列的原因/批次/
    审批/去向），调用方可以直接写审计表或操作日志，不必再回查。
    """
    holding_id: int
    record_id: int
    from_status: str
    to_status: str
    affected_record_ids: tuple[int, ...]
    audit: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
#  内部工具
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: str | None) -> str | None:
    """去空白；空串按「未提供」处理（审计里不出现空字符串噪音）。"""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _require(value: str | None, message: str) -> str:
    value = _text(value)
    if value is None:
        raise ValueError(message)
    return value


def _label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "未知")


def _lock_holding(db: Session, holding: HoldingORM | None) -> HoldingORM:
    """锁定该册并**重读**状态：判定基于库里的当前值，而不是调用方手里的快照。

    注意：`populate_existing()` 会用库里的值覆盖本会话中该册**尚未 flush** 的
    改动。处置状态是这条命令的唯一写入目标，调用方不应在调用前手改同一行。
    """
    if holding is None or holding.id is None:
        raise ValueError("馆藏不存在")
    with db.no_autoflush:
        row = (db.query(HoldingORM)
               .filter(HoldingORM.id == holding.id)
               .populate_existing()
               .with_for_update()
               .one_or_none())
    if row is None:
        raise ValueError("馆藏不存在")
    return row


def _active_loan(db: Session, holding_id: int) -> LoanORM | None:
    """该册当前的 ACTIVE 借阅（没有则为 None）。

    「在借」不是 Item.status（D2），只由 Loan 派生。这里不加锁：调用方已持有
    馆藏行锁，而外借路径（circulation.borrow）也是先锁馆藏再查重，两者互斥。
    """
    return (db.query(LoanORM)
            .filter(LoanORM.holding_id == holding_id, LoanORM.status == LOAN_ACTIVE)
            .first())


def _audit(action: str, *, holding: HoldingORM, from_status: str, to_status: str,
           at: datetime, reason: str | None = None,
           review_batch_no: str | None = None,
           approval_ref: str | None = None,
           destination: str | None = None,
           operator: str | None = None,
           **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "reason": reason,
        "review_batch_no": review_batch_no,
        "approval_ref": approval_ref,
        "destination": destination,
        "operator": operator,
        "at": at.isoformat(),
        "from_status": from_status,
        "to_status": to_status,
        "holding_id": holding.id,
        "record_id": holding.record_id,
        "barcode": holding.barcode,
        "library_id": holding.library_id,
    }
    payload.update(extra)
    return payload


def _result(holding: HoldingORM, from_status: str, to_status: str,
            audit: dict[str, Any]) -> ItemTransitionResult:
    return ItemTransitionResult(
        holding_id=holding.id,
        record_id=holding.record_id,
        from_status=from_status,
        to_status=to_status,
        affected_record_ids=(holding.record_id,),
        audit=audit,
    )


def _reject_status(holding: HoldingORM, *, action: str, expected: str,
                   extra: str | None = None) -> None:
    """状态不符时的统一拒绝消息（中文，带当前状态与目标动作）。"""
    raise ValueError(
        f"该册当前为「{_label(holding.status)}」，只有「{expected}」的馆藏才能{action}"
        + (f"；{extra}" if extra else ""))


# ---------------------------------------------------------------------------
#  命令：停流通 / 解除停流通（CMD-21 / CMD-22）
# ---------------------------------------------------------------------------

def suspend_item(db: Session, holding: HoldingORM, *, reason: str,
                 review_batch_no: str | None = None,
                 approval_ref: str | None = None,
                 destination: str | None = None,
                 operator: str | None = None) -> ItemTransitionResult:
    """停流通 / 另库保存：`AVAILABLE → SUSPENDED`。**不 commit**。

    依据 D14：这是「审查清理的不适宜/外观差」的落点，**不是注销** ——
    该册仍是馆藏资产（进资产台账/馆藏总量），只是不进 905、不可借、不可转库。

    有 ACTIVE Loan 时拒绝（INV-06）：册还在读者手上，先还书再处置。
    原因、审查批次、审批依据、去向在 Phase 1 没有列，只写进 `result.audit`。
    """
    reason = _require(reason, "停流通原因不能为空")
    h = _lock_holding(db, holding)
    if h.status != STATUS_AVAILABLE:
        _reject_status(h, action="停流通", expected="在架",
                       extra="请先核对馆藏状态，已注销/已遗失的册走恢复流程")
    if _active_loan(db, h.id) is not None:
        raise ValueError("该册有未归还的借阅记录（外借中），不能停流通，请先办理归还")

    now = _now()
    from_status = h.status
    h.status = STATUS_SUSPENDED
    h.suspended_at = now
    h.suspended_by = _text(operator)
    h.suspended_reason = reason
    h.review_batch_no = _text(review_batch_no)
    h.approval_ref = _text(approval_ref)
    h.destination = _text(destination)
    db.flush()

    audit = _audit(
        "SuspendItem", holding=h, from_status=from_status, to_status=STATUS_SUSPENDED,
        at=now, reason=reason, review_batch_no=_text(review_batch_no),
        approval_ref=_text(approval_ref), destination=_text(destination),
        operator=_text(operator),
        persisted_columns=("status", "suspended_at", "suspended_by",
                           "suspended_reason", "review_batch_no",
                           "approval_ref", "destination"),
    )
    return _result(h, from_status, STATUS_SUSPENDED, audit)


def release_item(db: Session, holding: HoldingORM, *,
                 operator: str | None = None) -> ItemTransitionResult:
    """解除停流通：`SUSPENDED → AVAILABLE`。**不 commit**。

    回到在架即可借、可转库、重回 905（905 由调用方重建）。
    """
    h = _lock_holding(db, holding)
    if h.status != STATUS_SUSPENDED:
        _reject_status(h, action="解除停流通", expected="停流通")
    now = _now()
    from_status = h.status
    cleared = {
        "suspended_at": h.suspended_at,
        "suspended_by": h.suspended_by,
        "suspended_reason": h.suspended_reason,
        "review_batch_no": h.review_batch_no,
        "approval_ref": h.approval_ref,
        "destination": h.destination,
    }
    h.status = STATUS_AVAILABLE
    h.suspended_at = None
    h.suspended_by = None
    h.suspended_reason = None
    h.review_batch_no = None
    h.approval_ref = None
    h.destination = None
    db.flush()

    audit = _audit(
        "ReleaseItem", holding=h, from_status=from_status, to_status=STATUS_AVAILABLE,
        at=now, operator=_text(operator), cleared=cleared,
        persisted_columns=("status", "suspended_at", "suspended_by",
                           "suspended_reason", "review_batch_no",
                           "approval_ref", "destination"),
    )
    return _result(h, from_status, STATUS_AVAILABLE, audit)


# ---------------------------------------------------------------------------
#  命令：注销 / 恢复（CMD-06 / CMD-07）
# ---------------------------------------------------------------------------

def withdraw_item(db: Session, holding: HoldingORM, *, reason: str,
                  withdraw_no: str,
                  approval_ref: str | None = None,
                  destination: str | None = None,
                  operator: str | None = None) -> ItemTransitionResult:
    """注销（剔旧 / 损毁）：`AVAILABLE / SUSPENDED / IN_PROCESS → WITHDRAWN`。

    有 ACTIVE Loan 时拒绝（INV-06）。写 `withdraw_no / withdraw_reason /
    withdrawn_at / withdrawn_by`。**不 commit**；905 由调用方重建。
    """
    reason = _require(reason, "注销原因不能为空（剔旧 / 损毁 / 其他）")
    withdraw_no = _require(withdraw_no, "注销号不能为空")
    h = _lock_holding(db, holding)
    if h.status not in WITHDRAWABLE_STATUSES:
        _reject_status(h, action="注销", expected="在架 / 停流通 / 编目中",
                       extra="已注销或已遗失的册请先恢复")
    if _active_loan(db, h.id) is not None:
        raise ValueError("该册有未归还的借阅记录（外借中），不能注销，请先办理归还")

    now = _now()
    from_status = h.status
    h.status = STATUS_WITHDRAWN
    h.withdraw_no = withdraw_no
    h.withdraw_reason = reason
    h.withdrawn_at = now
    h.withdrawn_by = _text(operator)
    # 停流通字段是“当前状态”；转成注销后清掉，历史在 result.audit 里。
    h.suspended_at = None
    h.suspended_by = None
    h.suspended_reason = None
    if approval_ref is not None:
        h.approval_ref = _text(approval_ref)
    if destination is not None:
        h.destination = _text(destination)
    db.flush()

    audit = _audit(
        "WithdrawItem", holding=h, from_status=from_status, to_status=STATUS_WITHDRAWN,
        at=now, reason=reason, approval_ref=_text(approval_ref),
        destination=_text(destination), operator=_text(operator),
        withdraw_no=withdraw_no,
        persisted_columns=("status", "withdraw_no", "withdraw_reason",
                           "withdrawn_at", "withdrawn_by", "approval_ref",
                           "destination"),
    )
    return _result(h, from_status, STATUS_WITHDRAWN, audit)


def restore_item(db: Session, holding: HoldingORM, *,
                 operator: str | None = None) -> ItemTransitionResult:
    """恢复入藏：`WITHDRAWN / LOST → AVAILABLE`，并清空 `withdraw_*`。**不 commit**。

    恢复后该册可借、可转库、重回 905（905 与预约队列由调用方推进）。
    """
    h = _lock_holding(db, holding)
    if h.status not in RESTORABLE_STATUSES:
        _reject_status(h, action="恢复", expected="已注销 / 已遗失",
                       extra="在架或停流通的册不需要恢复")
    now = _now()
    from_status = h.status
    cleared = {
        "withdraw_no": h.withdraw_no,
        "withdraw_reason": h.withdraw_reason,
        "suspended_at": h.suspended_at,
        "suspended_by": h.suspended_by,
        "suspended_reason": h.suspended_reason,
        "review_batch_no": h.review_batch_no,
        "approval_ref": h.approval_ref,
        "destination": h.destination,
    }
    h.status = STATUS_AVAILABLE
    h.withdraw_no = None
    h.withdraw_reason = None
    h.withdrawn_at = None
    h.withdrawn_by = None
    h.suspended_at = None
    h.suspended_by = None
    h.suspended_reason = None
    h.review_batch_no = None
    h.approval_ref = None
    h.destination = None
    db.flush()

    audit = _audit(
        "RestoreItem", holding=h, from_status=from_status, to_status=STATUS_AVAILABLE,
        at=now, operator=_text(operator), cleared=cleared,
        persisted_columns=("status", "withdraw_no", "withdraw_reason",
                           "withdrawn_at", "withdrawn_by", "suspended_at",
                           "suspended_by", "suspended_reason", "review_batch_no",
                           "approval_ref", "destination"),
    )
    return _result(h, from_status, STATUS_AVAILABLE, audit)


# ---------------------------------------------------------------------------
#  命令：按遗失处置（册侧；赔偿仍归 circulation.mark_lost）
# ---------------------------------------------------------------------------

def mark_lost_status(db: Session, holding: HoldingORM, *, withdraw_no: str,
                     reason: str = "遗失",
                     operator: str | None = None) -> ItemTransitionResult:
    """`AVAILABLE → LOST`。**不 commit**。

    只做册的处置状态与注销字段；**不结束借阅、不写赔偿** ——
    借阅行收尾与赔偿快照仍由 `services.circulation.mark_lost` 负责。
    因此这里**不拒绝** ACTIVE Loan：遗失恰恰发生在外借期间（CMD-11）。
    """
    withdraw_no = _require(withdraw_no, "注销号不能为空")
    reason = _text(reason) or "遗失"
    h = _lock_holding(db, holding)
    if h.status != STATUS_AVAILABLE:
        _reject_status(h, action="按遗失处理", expected="在架",
                       extra="已注销的册走注销流程，已遗失的册无需重复处理")

    now = _now()
    from_status = h.status
    h.status = STATUS_LOST
    h.withdraw_no = withdraw_no
    h.withdraw_reason = reason
    h.withdrawn_at = now
    h.withdrawn_by = _text(operator)
    db.flush()

    audit = _audit(
        "MarkLost", holding=h, from_status=from_status, to_status=STATUS_LOST,
        at=now, reason=reason, operator=_text(operator), withdraw_no=withdraw_no,
        note="只改册的处置状态；借阅关闭与赔偿由 services.circulation.mark_lost 负责",
        persisted_columns=("status", "withdraw_no", "withdraw_reason",
                           "withdrawn_at", "withdrawn_by"),
    )
    return _result(h, from_status, STATUS_LOST, audit)


# ---------------------------------------------------------------------------
#  前置校验（借出 / 转库）
# ---------------------------------------------------------------------------

def assert_checkout_allowed(db: Session, holding: HoldingORM) -> None:
    """借出前置校验：只有「在架且无 ACTIVE Loan」的册可借。违规抛 `ValueError`。

    借出/归还不改处置状态（D2），所以这里只读、不写、不加锁。
    """
    if holding is None or holding.id is None:
        raise ValueError("馆藏不存在")
    status = holding.status
    if status != STATUS_AVAILABLE:
        if status == STATUS_SUSPENDED:
            raise ValueError("该册已停流通，不能外借；如需流通请先解除停流通")
        if status == STATUS_WITHDRAWN:
            raise ValueError("该册已注销，不能外借；如确需流通请先恢复入藏")
        if status == STATUS_LOST:
            raise ValueError("该册已标记遗失，不能外借；如已找回请先恢复入藏")
        raise ValueError(f"该册当前为「{_label(status)}」，不能外借")
    if _active_loan(db, holding.id) is not None:
        raise ValueError("该册已外借（存在未归还的借阅记录），不能重复外借")


def assert_transfer_allowed(db: Session, holding: HoldingORM) -> None:
    """转库前置校验：只有「在架且无 ACTIVE Loan」的册可转库。违规抛 `ValueError`。

    契约 §7.6：SUSPENDED / WITHDRAWN / LOST 不得转库（除非先恢复/释放）；
    有 ACTIVE Loan 的册不得转库。
    """
    if holding is None or holding.id is None:
        raise ValueError("馆藏不存在")
    status = holding.status
    if status != STATUS_AVAILABLE:
        if status == STATUS_SUSPENDED:
            raise ValueError("该册已停流通，不能转库；请先解除停流通")
        if status == STATUS_WITHDRAWN:
            raise ValueError("该册已注销，不能转库；请先恢复入藏")
        if status == STATUS_LOST:
            raise ValueError("该册已标记遗失，不能转库；如已找回请先恢复入藏")
        raise ValueError(f"该册当前为「{_label(status)}」，不能转库")
    if _active_loan(db, holding.id) is not None:
        raise ValueError("该册有未归还的借阅记录（外借中），不能转库，请先办理归还")
