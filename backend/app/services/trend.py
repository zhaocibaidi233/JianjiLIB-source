# -*- coding: utf-8 -*-
"""借还曲线（近 N 天，按本地日归并）。

口径的唯一实现：`api/circulation.py` 的 `/circulation/stats` 与
`api/dashboard.py` 原来各写了一遍"把窗口内借阅整行拉进 Python 按日归并"，
本模块抽成一条聚合 SQL 供两处共用（两处本就承诺同一口径）。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from ..core.dates import local_day_start
from ..models import LoanORM


def loan_day_trend(db: Session, *, library_id: int | None = None,
                   days: int = 14, today: date | None = None) -> list[dict]:
    """近 `days` 天借还曲线：`[{"date": "YYYY-MM-DD", "borrowed": n, "returned": n}]`。

    口径与旧 Python 归并版逐字对齐：

    - **按本地日归并**：直接 `group by date(loaned_at)` 会按 UTC 切日，晚上 8 点
      后的借阅会算到第二天。分桶边界全部在 Python 算好（`local_day_start`）
      传进 SQL 的 CASE，不依赖数据库时区 —— `date_trunc` 需要给数据库传时区名，
      而 Windows 本地时区名（"China Standard Time"）PG 不认识，这条路走不通；
    - **一册可以同时进 borrowed 和 returned**：外借与归还按各自的时间列独立
      聚合（同一天还当天借的书，两边各 +1），与旧版行为一致；
    - **窗口外的行不计**：旧版靠 `if d in buckets` 丢弃，这里用范围过滤，
      语义相同（归还/外借落在窗口右侧未来的行不存在，左侧旧行被切掉）。

    旧版每次请求把窗口内**全部 LoanORM 整行**（含十几个冗余列）拉进内存；
    现在数据库里数完，应用侧只收 ≤2N 行 `(日, 计数)`。
    """
    today = today or date.today()
    labels = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    window_start = local_day_start(labels[0])
    window_end = local_day_start(labels[-1] + timedelta(days=1))

    def _bucket(col):
        """把时间戳列按 Python 算好的本地日边界映射成日期标签。"""
        whens = [(and_(col >= local_day_start(d),
                       col < local_day_start(d + timedelta(days=1))),
                  d.isoformat()) for d in labels]
        return case(*whens)

    q = db.query(LoanORM)
    if library_id:
        q = q.filter(LoanORM.library_id == library_id)

    result = {d.isoformat(): {"date": d.isoformat(), "borrowed": 0, "returned": 0}
              for d in labels}
    for col, key in ((LoanORM.loaned_at, "borrowed"),
                     (LoanORM.returned_at, "returned")):
        bucket = _bucket(col)
        for day_label, n in (q.filter(col.isnot(None),
                                      col >= window_start, col < window_end)
                             .with_entities(bucket, func.count())
                             .group_by(bucket)
                             .all()):
            if day_label is not None:
                result[day_label][key] = int(n)
    return [result[d.isoformat()] for d in labels]
