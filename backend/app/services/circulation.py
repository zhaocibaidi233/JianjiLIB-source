# -*- coding: utf-8 -*-
"""借阅规则与借还业务。

**外借状态只由 `loans` 表的 ACTIVE 行表达**，不写 `holdings.status` ——
905、馆藏统计、`records.active_count` 都按 `status == AVAILABLE` 过滤，
外借就改状态会让每借一本书都改写 MARC，且外借期间该册从「在架册数」里消失。
。

本模块只做业务判定与 flush，**不 commit**：提交/回滚由 API 层统一掌控。
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (FINE_PAID, FINE_STATUSES, FINE_UNPAID, FINE_WAIVED,
                      LOAN_ACTIVE, LOAN_LOST, LOAN_RETURNED, LOAN_STATUS_LABELS,
                      READER_ACTIVE, READER_INACTIVE, READER_SUSPENDED,
                      READER_TYPES, STATUS_AVAILABLE, STATUS_LOST,
                      HoldingORM, LoanORM, ReaderORM, RecordORM, Setting)
from .batch import next_withdraw_no
from . import item_lifecycle, record_projection

RULES_KEY = "circulation_rules"

# 与前端 DEFAULT_RULES 逐项一致（前端实现）。
DEFAULT_RULES: dict = {
    "types": {
        "student": {"label": "学生", "loan_days": 30, "max_items": 2,
                    "max_renew": 1, "renew_days": 15},
        "teacher": {"label": "教师", "loan_days": 60, "max_items": 10,
                    "max_renew": 2, "renew_days": 30},
        "staff": {"label": "职工", "loan_days": 60, "max_items": 10,
                  "max_renew": 2, "renew_days": 30},
        "other": {"label": "其他", "loan_days": 30, "max_items": 2,
                  "max_renew": 1, "renew_days": 15},
    },
    "skip_weekends": True,
    "closed_dates": [],
    "block_on_overdue": True,
    # 预约到书后的保留天数：过期自动释放，供下一位排队读者。
    "reservation_hold_days": 3,
    # 可选功能：默认关。关闭时还书不计罚、界面不出现任何罚款金额与页签；
    # 已经产生的历史金额保留在库里，重新开启后仍可收款/核对。
    "fines": {
        "enabled": False,
        "per_day": 0.10,
        "grace_days": 0,
        "max_amount": 0.0,   # 0 = 单册不封顶
        # 未缴停借：开启后，名下有未缴罚款或未缴遗失赔偿的读者不能外借
        # （归还/续借不受影响）。默认关——与罚款开关一致，学校馆可以只计费不拦人。
        "block_on_unpaid": False,
    },
}

RULE_LIMITS = {
    "loan_days": (1, 365),
    "max_items": (1, 100),
    "max_renew": (0, 10),
    "renew_days": (1, 365),
}
FINE_LIMITS = {
    "per_day": (0.0, 1000.0),
    "grace_days": (0, 365),
    "max_amount": (0.0, 100000.0),
}
MAX_CLOSED_DATES = 366

FINE_STATUS_LABELS = {
    FINE_UNPAID: "未缴",
    FINE_PAID: "已收",
    FINE_WAIVED: "已免除",
}

READER_STATUS_LABELS = {
    READER_ACTIVE: "正常",
    READER_SUSPENDED: "暂停借阅",
    READER_INACTIVE: "已注销",
}
READER_TYPE_LABELS = {k: v["label"] for k, v in DEFAULT_RULES["types"].items()}


def _coerce_int(raw, default: int, lo: int, hi: int) -> int:
    """与 services/settings.py 同一口径：bool 不算数字，越界 clamp。"""
    if isinstance(raw, bool):
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def _coerce_money(raw, default: float, lo: float, hi: float) -> float:
    """金额归一：两位小数、越界 clamp、脏值回默认；NaN/Inf 也回默认。"""
    if isinstance(raw, bool):
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return round(max(lo, min(v, hi)), 2)


def normalize_rules(raw) -> dict:
    """归一出厂规则；脏值**逐项**回退默认，绝不抛。

    类型键固定四个（`READER_TYPES`）：label 可改，键不可增删 —— 读者档案里
    存的是键，新增一个键会让历史读者失去对应规则。
    """
    out = copy.deepcopy(DEFAULT_RULES)
    if not isinstance(raw, dict):
        return out
    raw_types = raw.get("types")
    if isinstance(raw_types, dict):
        for key in READER_TYPES:
            item = raw_types.get(key)
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            if isinstance(label, str) and label.strip():
                out["types"][key]["label"] = label.strip()[:16]
            for field, (lo, hi) in RULE_LIMITS.items():
                out["types"][key][field] = _coerce_int(
                    item.get(field), out["types"][key][field], lo, hi)
    for key in ("skip_weekends", "block_on_overdue"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    out["reservation_hold_days"] = _coerce_int(
        raw.get("reservation_hold_days"), out["reservation_hold_days"], 1, 30)
    raw_fines = raw.get("fines")
    if isinstance(raw_fines, dict):
        if isinstance(raw_fines.get("enabled"), bool):
            out["fines"]["enabled"] = raw_fines["enabled"]
        if isinstance(raw_fines.get("block_on_unpaid"), bool):
            out["fines"]["block_on_unpaid"] = raw_fines["block_on_unpaid"]
        for field, (lo, hi) in FINE_LIMITS.items():
            default = out["fines"][field]
            if field == "grace_days":
                out["fines"][field] = _coerce_int(
                    raw_fines.get(field), default, int(lo), int(hi))
            else:
                out["fines"][field] = _coerce_money(
                    raw_fines.get(field), default, lo, hi)
    closed = raw.get("closed_dates")
    if isinstance(closed, list):
        seen: list[str] = []
        for v in closed[:MAX_CLOSED_DATES]:
            if isinstance(v, str):
                try:
                    iso = date.fromisoformat(v.strip()).isoformat()
                except ValueError:
                    continue
                if iso not in seen:
                    seen.append(iso)
        out["closed_dates"] = seen
    return out


def rules_value(db: Session) -> dict:
    """读全局借阅规则；行不存在 / 脏值 → 出厂默认。**绝不抛**。"""
    try:
        row = db.get(Setting, RULES_KEY)
        return normalize_rules(row.value if row is not None else None)
    except Exception:
        return copy.deepcopy(DEFAULT_RULES)


def save_rules(db: Session, value: dict) -> dict:
    """归一后写回 `settings`；返回实际落库的规则。**不 commit**。"""
    norm = normalize_rules(value)
    row = db.get(Setting, RULES_KEY)
    if row is None:
        db.add(Setting(key=RULES_KEY, value=norm))
    else:
        row.value = norm
    db.flush()
    return norm


def type_rule(rules: dict, reader_type: str | None) -> dict:
    """读者类型 → 规则；未知类型回退 `other`（旧数据的容错方向）。"""
    types = rules.get("types") or {}
    return types.get(reader_type or "") or types.get("other") or DEFAULT_RULES["types"]["other"]


def _closed_set(rules: dict) -> set[date]:
    out: set[date] = set()
    for v in rules.get("closed_dates") or []:
        try:
            out.add(date.fromisoformat(str(v)))
        except ValueError:
            continue
    return out


def compute_due_on(start: date, days: int, rules: dict) -> date:
    """`start + days`，落在周末 / 闭馆日就**向后**顺延到最近的可借日。

    例（skip_weekends=true）：周五借 1 天 → 周六、周日顺延 → 下周一。
    顺延最多找一年；一年都不可借（配置极端）则原样返回，不让借书卡死。
    """
    due = start + timedelta(days=max(1, int(days)))
    skip_weekends = rules.get("skip_weekends", True)
    closed = _closed_set(rules)
    for _ in range(370):
        if (skip_weekends and due.weekday() >= 5) or due in closed:
            due += timedelta(days=1)
            continue
        return due
    return due


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_overdue(loan: LoanORM, today: date | None = None) -> bool:
    return loan.status == LOAN_ACTIVE and loan.due_on < (today or date.today())


def active_loan_for_holding(db: Session, holding_id: int) -> LoanORM | None:
    return (db.query(LoanORM)
            .filter(LoanORM.holding_id == holding_id,
                    LoanORM.status == LOAN_ACTIVE)
            .first())


def active_loan_holding_ids(db: Session, holding_ids) -> set[int]:
    """一批馆藏里正在外借的 id（守卫用：注销 / 删除 / 转库）。"""
    ids = [int(i) for i in holding_ids]
    if not ids:
        return set()
    return {hid for (hid,) in
            db.query(LoanORM.holding_id)
            .filter(LoanORM.holding_id.in_(ids),
                    LoanORM.status == LOAN_ACTIVE).all()}


def active_loan_record_ids(db: Session, record_ids) -> set[int]:
    """一批书目记录里**有册外借**的 id（删记录守卫用）。"""
    ids = [int(i) for i in record_ids]
    if not ids:
        return set()
    return {rid for (rid,) in
            db.query(HoldingORM.record_id)
            .join(LoanORM, LoanORM.holding_id == HoldingORM.id)
            .filter(HoldingORM.record_id.in_(ids),
                    LoanORM.status == LOAN_ACTIVE).distinct().all()}


def active_loans_of_reader(db: Session, reader_id: int) -> list[LoanORM]:
    return (db.query(LoanORM)
            .filter(LoanORM.reader_id == reader_id,
                    LoanORM.status == LOAN_ACTIVE)
            .populate_existing()
            .order_by(LoanORM.due_on, LoanORM.id).all())


def overdue_count(db: Session, reader_id: int, today: date | None = None) -> int:
    return (db.query(LoanORM)
            .filter(LoanORM.reader_id == reader_id,
                    LoanORM.status == LOAN_ACTIVE,
                    LoanORM.due_on < (today or date.today())).count())


def fines_enabled(rules: dict | None) -> bool:
    """罚款开关：**唯一的可见性判据**（后端算不算、前端显不显示都按它）。"""
    return bool(((rules or {}).get("fines") or {}).get("enabled"))


def compute_fine(due_on: date | None, as_of: date, rules: dict) -> Decimal:
    """某一天归还时应计的罚款（元）。**规则关闭 / 未逾期 → 0**。

    只数「超过宽限天数的部分」：宽限 2 天、逾期 10 天 = 罚 8 天。
    `max_amount=0` 表示不封顶；结果按分四舍五入。
    """
    if not fines_enabled(rules) or due_on is None:
        return Decimal("0.00")
    cfg = rules["fines"]
    days = (as_of - due_on).days - int(cfg.get("grace_days") or 0)
    if days <= 0:
        return Decimal("0.00")
    amount = Decimal(str(cfg.get("per_day") or 0)) * days
    cap = Decimal(str(cfg.get("max_amount") or 0))
    if cap > 0:
        amount = min(amount, cap)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def reader_fine_summary(db: Session, reader_id: int, rules: dict) -> dict:
    """读者名下「已产生未缴」与「外借逾期预计」罚款。

    关闭罚款时一律回 0（界面据 `enabled` 整块隐藏）；历史金额不删，
    重新开启后仍会出现在清单里。
    """
    enabled = fines_enabled(rules)
    out = {"enabled": enabled, "unpaid": 0.0, "unpaid_count": 0,
           "overdue_estimate": 0.0}
    if not enabled:
        return out
    total, count = (db.query(func.coalesce(func.sum(LoanORM.fine_amount), 0),
                             func.count(LoanORM.id))
                    .filter(LoanORM.reader_id == reader_id,
                            LoanORM.fine_status == FINE_UNPAID).one())
    out["unpaid"] = float(total or 0)
    out["unpaid_count"] = int(count or 0)
    today = date.today()
    active_overdue = (db.query(LoanORM)
                      .filter(LoanORM.reader_id == reader_id,
                              LoanORM.status == LOAN_ACTIVE,
                              LoanORM.due_on < today).all())
    estimate = sum((compute_fine(x.due_on, today, rules)
                    for x in active_overdue), Decimal("0.00"))
    out["overdue_estimate"] = float(estimate)
    return out


def reader_compensation_summary(db: Session, reader_id: int) -> dict:
    """读者名下丢书赔偿（**独立于罚款开关**：罚款可以关，赔偿始终可用）。"""
    lost_count = (db.query(func.count(LoanORM.id))
                  .filter(LoanORM.reader_id == reader_id,
                          LoanORM.status == LOAN_LOST)
                  .scalar() or 0)
    total, count = (db.query(func.coalesce(func.sum(LoanORM.compensation_amount), 0),
                             func.count(LoanORM.id))
                    .filter(LoanORM.reader_id == reader_id,
                            LoanORM.compensation_status == FINE_UNPAID)
                    .one())
    return {"lost_count": int(lost_count or 0),
            "unpaid": round(float(total or 0), 2),
            "unpaid_count": int(count or 0)}


def settle_compensation(db: Session, *, loan: LoanORM | None, status: str,
                        operator: str | None) -> LoanORM:
    """丢书赔偿收款 / 免除 / 撤销。**不 commit**。

    两个终端同时按旧状态提交时，后到者必须失败而不是覆盖先到者：先锁行读
    当前状态，再与调用方传进来的旧状态比对（调用方无需新增任何字段）。
    """
    if loan is None:
        raise ValueError("借阅记录不存在")
    if status not in FINE_STATUSES:
        raise ValueError(f"赔偿状态只能是 {' / '.join(FINE_STATUSES)}")
    loan_id, expected = loan.id, loan.compensation_status
    current = _lock_loan(db, loan_id)
    if current is None:
        raise ValueError("借阅记录不存在")
    if current.compensation_status != expected:
        raise ValueError("赔偿状态已被其他终端修改，请刷新后重试")
    if current.status != LOAN_LOST:
        raise ValueError("只有已按遗失处理的借阅才有赔偿")
    if current.compensation_amount is None or Decimal(str(current.compensation_amount)) <= 0:
        raise ValueError("该借阅没有登记赔偿")
    current.compensation_status = status
    if status == FINE_UNPAID:
        current.compensation_settled_at = None
        current.compensation_operator = None
    else:
        current.compensation_settled_at = now_utc()
        current.compensation_operator = operator
    db.flush()
    return current


def settle_fine(db: Session, *, loan: LoanORM | None, status: str,
                operator: str | None) -> LoanORM:
    """收款 / 免除 / 撤销（改回未缴）。**不 commit**。

    与赔偿同一套并发协议：锁行后比对调用方读到的旧状态，不一致即 `ValueError`，
    避免两台终端各自 PAID / WAIVED 时 later-write-wins。
    """
    if loan is None:
        raise ValueError("借阅记录不存在")
    if status not in FINE_STATUSES:
        raise ValueError(f"罚款状态只能是 {' / '.join(FINE_STATUSES)}")
    loan_id, expected = loan.id, loan.fine_status
    current = _lock_loan(db, loan_id)
    if current is None:
        raise ValueError("借阅记录不存在")
    if current.fine_status != expected:
        raise ValueError("罚款状态已被其他终端修改，请刷新后重试")
    if current.fine_amount is None or Decimal(str(current.fine_amount)) <= 0:
        raise ValueError("该借阅没有罚款记录")
    if current.status != LOAN_RETURNED:
        raise ValueError("该册尚未归还，罚款要等归还时结算")
    current.fine_status = status
    if status == FINE_UNPAID:
        current.fine_settled_at = None
        current.fine_operator = None
    else:
        current.fine_settled_at = now_utc()
        current.fine_operator = operator
    db.flush()
    return current


def loan_dict(loan: LoanORM) -> dict:
    today = date.today()
    overdue = is_overdue(loan, today)
    days_at_return = 0
    if (loan.status == LOAN_RETURNED and loan.returned_at is not None
            and loan.due_on is not None):
        days_at_return = max(0, (loan.returned_at.astimezone().date()
                                 - loan.due_on).days)
    return {
        "id": loan.id, "library_id": loan.library_id,
        "holding_id": loan.holding_id, "record_id": loan.record_id,
        "barcode": loan.barcode, "title": loan.title,
        "reader_id": loan.reader_id, "reader_card_no": loan.reader_card_no,
        "reader_name": loan.reader_name, "reader_class": loan.reader_class,
        "loaned_at": str(loan.loaned_at) if loan.loaned_at else None,
        "due_on": loan.due_on.isoformat() if loan.due_on else None,
        "returned_at": str(loan.returned_at) if loan.returned_at else None,
        "renew_count": loan.renew_count or 0,
        "last_renewed_at": (str(loan.last_renewed_at)
                            if loan.last_renewed_at else None),
        "operator_loan": loan.operator_loan,
        "operator_return": loan.operator_return,
        "operator_renew": loan.operator_renew,
        "status": loan.status,
        "status_label": LOAN_STATUS_LABELS.get(loan.status, loan.status),
        "note": loan.note,
        "overdue": overdue,
        "days_overdue": ((today - loan.due_on).days if overdue else 0),
        "days_overdue_at_return": days_at_return,
        "fine_amount": float(loan.fine_amount or 0),
        "fine_status": loan.fine_status,
        "fine_status_label": FINE_STATUS_LABELS.get(loan.fine_status or ""),
        "fine_settled_at": (str(loan.fine_settled_at)
                            if loan.fine_settled_at else None),
        "fine_operator": loan.fine_operator,
        "lost_at": str(loan.lost_at) if loan.lost_at else None,
        "lost_operator": loan.lost_operator,
        "lost_note": loan.lost_note,
        "compensation_amount": float(loan.compensation_amount or 0),
        "compensation_status": loan.compensation_status,
        "compensation_status_label": FINE_STATUS_LABELS.get(
            loan.compensation_status or ""),
        "compensation_settled_at": (str(loan.compensation_settled_at)
                                    if loan.compensation_settled_at else None),
        "compensation_operator": loan.compensation_operator,
    }


def reader_dict(r: ReaderORM, rules: dict | None = None) -> dict:
    return {
        "id": r.id, "library_id": r.library_id, "card_no": r.card_no,
        "name": r.name, "reader_type": r.reader_type,
        "reader_type_label": type_rule(rules or DEFAULT_RULES, r.reader_type)["label"],
        "grade": r.grade, "class_name": r.class_name, "phone": r.phone,
        "status": r.status,
        "status_label": READER_STATUS_LABELS.get(r.status, r.status),
        "note": r.note,
        # 隐私（契约 C-19/D15）：同意留痕与匿名化时间供档案页展示；都不是敏感内容本身。
        "guardian_consent_at": (r.guardian_consent_at.isoformat()
                                if r.guardian_consent_at else None),
        "guardian_consent_by": r.guardian_consent_by,
        "anonymized_at": (r.anonymized_at.isoformat()
                          if r.anonymized_at else None),
        "created_at": str(r.created_at) if r.created_at else None,
        "updated_at": str(r.updated_at) if r.updated_at else None,
    }


def _fmt_date(d: date | None) -> str:
    return d.isoformat() if d else ""


def lock_record(db: Session, record_id: int) -> None:
    """锁顺序：读者 → 书目（升序）→ 馆藏 → 借阅/预约，持有至事务结束。"""
    with db.no_autoflush:
        db.query(RecordORM.id).filter(RecordORM.id == record_id).with_for_update().all()


def _lock_reader(db: Session, reader_id: int) -> ReaderORM | None:
    with db.no_autoflush:
        return (db.query(ReaderORM).filter(ReaderORM.id == reader_id)
                .populate_existing().with_for_update().one_or_none())


def _lock_holding(db: Session, holding: HoldingORM) -> HoldingORM:
    holding_id, record_id = holding.id, holding.record_id
    lock_record(db, record_id)
    with db.no_autoflush:
        current = (db.query(HoldingORM).filter(HoldingORM.id == holding_id)
                   .populate_existing().with_for_update().one_or_none())
    if current is None:
        raise ValueError("借阅对应的馆藏不存在")
    if current.record_id != record_id:
        raise ValueError("馆藏所属书目已变更，请刷新后重试")
    return current


def _lock_loan(db: Session, loan_id: int) -> LoanORM | None:
    with db.no_autoflush:
        return (db.query(LoanORM).filter(LoanORM.id == loan_id)
                .populate_existing().with_for_update().one_or_none())


def borrow_block_reason(db: Session, reader: ReaderORM, rules: dict,
                        active: list[LoanORM] | None = None) -> str | None:
    """读者维度的借阅停借投影：逾期未还 / 未缴罚款或赔偿。

    契约 §10.3：`borrow_blocked` 是投影，不写 Reader.status；Reader.status 只表示
    人工纪律性暂停。返回中文原因（None = 可通过）。
    """
    if active is None:
        active = active_loans_of_reader(db, reader.id)
    overdue = [x for x in active if x.due_on < date.today()]
    if rules.get("block_on_overdue", True) and overdue:
        sample = overdue[0]
        return (f"该读者有 {len(overdue)} 册逾期未还"
                f"（最早《{sample.title or sample.barcode or ''}》，"
                f"应还 {_fmt_date(sample.due_on)}），请先归还")
    if fines_enabled(rules) and rules["fines"].get("block_on_unpaid"):
        unpaid_fine = (db.query(func.count(LoanORM.id))
                       .filter(LoanORM.reader_id == reader.id,
                               LoanORM.fine_status == FINE_UNPAID).scalar() or 0)
        if unpaid_fine > 0:
            return (f"该读者有 {unpaid_fine} 笔未缴罚款，缴清后再借"
                    f"（可在「读者赔罚」结清）")
        unpaid_comp = (db.query(func.count(LoanORM.id))
                       .filter(LoanORM.reader_id == reader.id,
                               LoanORM.compensation_status == FINE_UNPAID).scalar() or 0)
        if unpaid_comp > 0:
            return (f"该读者有 {unpaid_comp} 笔遗失赔偿未缴，缴清后再借"
                    f"（可在「读者赔罚」结清）")
    return None


def borrow(db: Session, *, reader: ReaderORM, holding: HoldingORM,
           title: str | None, rules: dict, operator: str | None) -> LoanORM:
    """外借一册。业务不满足时抛 `ValueError`（API 转 400）。**不 commit**。"""
    # 不同册也必须竞争同一个读者锁；获锁后重新读状态和当前 ACTIVE 额度。
    reader = _lock_reader(db, reader.id)
    if reader is None:
        raise ValueError("读者不存在")
    holding = _lock_holding(db, holding)
    if holding.library_id != reader.library_id:
        raise ValueError("读者与这本书不属于同一个图书库，不能跨库借阅")
    if reader.status == READER_SUSPENDED:
        raise ValueError("该读者已暂停借阅，不能借书（仍可归还）")
    if reader.status == READER_INACTIVE:
        raise ValueError("该读者已注销，不能借书")
    if reader.status != READER_ACTIVE:
        raise ValueError(f"读者状态为「{reader.status}」，不能借书")
    item_lifecycle.assert_checkout_allowed(db, holding)

    rule = type_rule(rules, reader.reader_type)
    active = active_loans_of_reader(db, reader.id)
    if len(active) >= rule["max_items"]:
        raise ValueError(
            f"该读者已借 {len(active)} 册，达到上限 {rule['max_items']} 册，"
            f"请先归还")
    blocked = borrow_block_reason(db, reader, rules, active)
    if blocked:
        raise ValueError(blocked)

    loan = LoanORM(
        library_id=holding.library_id,
        holding_id=holding.id, record_id=holding.record_id,
        barcode=holding.barcode, title=title,
        reader_id=reader.id, reader_card_no=reader.card_no,
        reader_name=reader.name, reader_class=reader.class_name,
        loaned_at=now_utc(),
        due_on=compute_due_on(date.today(), rule["loan_days"], rules),
        renew_count=0, operator_loan=operator, status=LOAN_ACTIVE)
    db.add(loan)
    db.flush()
    return loan


def return_loan(db: Session, *, holding: HoldingORM, rules: dict,
                operator: str | None) -> tuple[LoanORM, bool]:
    """归还一册。返回 `(借阅行, 归还时是否已逾期)`。**不 commit**。

    罚款在**这一刻**按当前规则算好写进 `loans`（快照）：之后管理员改费率 /
    封顶 / 宽限，不会追溯已经归还的记录。
    """
    holding = _lock_holding(db, holding)
    if holding.status == STATUS_LOST:
        raise ValueError("该册已标记遗失，不能归还；如找回请先在条码管理恢复馆藏")
    loan = (db.query(LoanORM)
            .filter(LoanORM.holding_id == holding.id, LoanORM.status == LOAN_ACTIVE)
            .populate_existing().with_for_update().one_or_none())
    if loan is None:
        raise ValueError("该册当前没有外借记录，无需归还")
    today = date.today()
    was_overdue = loan.due_on < today
    loan.returned_at = now_utc()
    loan.operator_return = operator
    loan.status = LOAN_RETURNED
    fine = compute_fine(loan.due_on, today, rules)
    if fine > 0:
        loan.fine_amount = fine
        loan.fine_status = FINE_UNPAID
    db.flush()
    return loan, was_overdue


def mark_lost(db: Session, *, loan: LoanORM | None, holding: HoldingORM | None,
              amount: float | Decimal = 0, paid: bool = False,
              note: str | None = None,
              operator: str | None = None) -> LoanORM:
    """把外借的一册按遗失处理。**不 commit**。

    三件事在同一事务里完成：借阅结束为 LOST、馆藏转 LOST（生成注销号、重建 905）、
    登记赔偿（0 元表示只标记遗失、不记赔款）。赔偿与逾期罚款是两笔独立的钱。
    """
    if loan is None:
        raise ValueError("借阅记录不存在")
    if holding is None or holding.id != loan.holding_id:
        raise ValueError("借阅对应的馆藏不存在")
    holding = _lock_holding(db, holding)
    loan = _lock_loan(db, loan.id)
    if loan is None:
        raise ValueError("借阅记录不存在")
    if loan.status != LOAN_ACTIVE:
        raise ValueError("该借阅不外借，不能按遗失处理")
    if holding.id != loan.holding_id:
        raise ValueError("借阅对应的馆藏已变更，请刷新后重试")
    if holding.status != STATUS_AVAILABLE:
        raise ValueError("该册已不在架（已注销或已遗失），请核对馆藏状态")

    try:
        amt = Decimal(str(amount if amount is not None else 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, ArithmeticError):
        raise ValueError("赔偿金额格式不正确")
    if amt < 0 or amt > Decimal("1000000"):
        raise ValueError("赔偿金额应在 0 ~ 1000000 元之间")

    now = now_utc()
    loan.status = LOAN_LOST
    loan.lost_at = now
    loan.lost_operator = operator
    loan.lost_note = (note or "").strip()[:256] or None

    item_lifecycle.mark_lost_status(
        db, holding,
        withdraw_no=next_withdraw_no(db, holding.library_id),
        reason="遗失", operator=operator)
    record_projection.rebuild_905(db, holding.record_id)

    if amt > 0:
        loan.compensation_amount = amt
        loan.compensation_status = FINE_PAID if paid else FINE_UNPAID
        loan.compensation_settled_at = now if paid else None
        loan.compensation_operator = operator if paid else None
    else:
        loan.compensation_amount = Decimal("0.00")
        loan.compensation_status = None
        loan.compensation_settled_at = None
        loan.compensation_operator = None
    db.flush()
    return loan


def renew_loan(db: Session, *, loan: LoanORM, rules: dict,
               operator: str | None = None) -> LoanORM:
    """续借。已逾期 / 读者停用 / 次数用尽 → `ValueError`。**不 commit**。

    `operator` 记进 `loans.operator_renew`：借出与归还有署名，续借没有的话，
    台账上「谁把这本续了 15 天」就查不到。
    """
    reader = _lock_reader(db, loan.reader_id)
    holding = db.get(HoldingORM, loan.holding_id)
    if holding is None:
        raise ValueError("借阅对应的馆藏不存在")
    holding = _lock_holding(db, holding)
    loan = _lock_loan(db, loan.id)
    if loan is None:
        raise ValueError("借阅记录不存在")
    if loan.status != LOAN_ACTIVE:
        raise ValueError("该借阅已结束（已归还或已遗失），不能续借")
    if loan.due_on < date.today():
        raise ValueError(
            f"该册已逾期（应还 {_fmt_date(loan.due_on)}），不能续借，请先归还")
    if holding.status != STATUS_AVAILABLE:
        raise ValueError("该册已注销或已遗失，不能续借")
    if reader is not None and reader.status != READER_ACTIVE:
        raise ValueError(f"读者状态为「{READER_STATUS_LABELS.get(reader.status, reader.status)}」，"
                         f"不能续借")
    rule = type_rule(rules, reader.reader_type if reader is not None else "other")
    if (loan.renew_count or 0) >= rule["max_renew"]:
        raise ValueError(
            f"已续借 {loan.renew_count} 次，达到上限 {rule['max_renew']} 次")
    base = max(date.today(), loan.due_on)
    loan.due_on = compute_due_on(base, rule["renew_days"], rules)
    loan.renew_count = (loan.renew_count or 0) + 1
    loan.last_renewed_at = now_utc()
    loan.operator_renew = operator
    db.flush()
    return loan
