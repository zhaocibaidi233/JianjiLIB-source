# -*- coding: utf-8 -*-
"""日期边界工具（纯函数，不碰数据库）。"""
from __future__ import annotations

from datetime import date, datetime, time, timezone


def local_day_start(d: date) -> datetime:
    """本地某日 00:00 对应的 UTC 时刻（时间戳列按它做日界过滤）。

    原 `api/circulation.py` 的 `_local_day_start`：借还曲线/今日借还/逾期统计
    都按**服务器本地日**切窗。下沉到 core 供 services 层共用（services 不得
    反向 import api），api 层留同名别名保持既有引用不变。
    """
    return datetime.combine(d, time.min).astimezone(timezone.utc)
