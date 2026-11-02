# -*- coding: utf-8 -*-
"""AI 服务层（纯规则，无大模型）。

现存 `fields.py`：字段自动补齐与复查（`autofill_fields`）。
"""
from .fields import autofill_fields

__all__ = ["autofill_fields"]
