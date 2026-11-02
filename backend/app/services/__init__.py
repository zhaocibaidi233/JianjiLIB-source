# -*- coding: utf-8 -*-
"""业务服务层：ORM 与 core.Record 互转、检索列同步、套录本地化改写。"""
from .convert import (compute_search_columns, orm_to_record,
                      record_from_payload, record_to_orm_values)
from .localize import localize_record, next_control_no
from .settings import class_no_rule

__all__ = [
    "compute_search_columns", "orm_to_record", "record_from_payload",
    "record_to_orm_values", "localize_record", "next_control_no",
    "class_no_rule",
]
