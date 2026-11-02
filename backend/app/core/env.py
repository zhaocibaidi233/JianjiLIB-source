# -*- coding: utf-8 -*-
"""环境变量兼容层。

规范名是 JIANJI_*。旧部署使用的 CATALOGING_* 仍然读取，
但只在首次遇到时提示弃用，避免升级后配置静默失效。
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

#: 规范名 -> 旧名
LEGACY_ENV_MAP: dict[str, str] = {
    "JIANJI_DATABASE_URL": "CATALOGING_DATABASE_URL",
    "JIANJI_DB_PASSWORD": "CATALOGING_DB_PASSWORD",
    "JIANJI_DB_STATEMENT_TIMEOUT": "CATALOGING_DB_STATEMENT_TIMEOUT",
    "JIANJI_DB_LOCK_TIMEOUT": "CATALOGING_DB_LOCK_TIMEOUT",
    "JIANJI_DB_IDLE_TIMEOUT": "CATALOGING_DB_IDLE_TIMEOUT",
    "JIANJI_ACCESS_TOKEN": "CATALOGING_ACCESS_TOKEN",
    "JIANJI_ACCESS_TOKEN_ROLE": "CATALOGING_ACCESS_TOKEN_ROLE",
    "JIANJI_CORS_ORIGINS": "CATALOGING_CORS_ORIGINS",
    "JIANJI_LAN_MODE": "CATALOGING_LAN_MODE",
    "JIANJI_SERVE_STATIC": "CATALOGING_SERVE_STATIC",
    "JIANJI_SESSION_DAYS": "CATALOGING_SESSION_DAYS",
    "JIANJI_YAZ_CLIENT": "CATALOGING_YAZ_CLIENT",
    "JIANJI_API_URL": "CATALOGING_API_URL",
    "JIANJI_API_TOKEN": "CATALOGING_API_TOKEN",
    "JIANJI_MCP_TIMEOUT": "CATALOGING_MCP_TIMEOUT",
    "JIANJI_TEST_URL": "CATALOGING_TEST_URL",
}

_warned: set[str] = set()


def apply_legacy_aliases() -> None:
    """把旧变量复制到规范名；规范名已存在时不覆盖。可重复调用。"""
    for canonical, legacy in LEGACY_ENV_MAP.items():
        if canonical in os.environ:
            continue
        value = os.environ.get(legacy)
        if value is None:
            continue
        os.environ[canonical] = value
        if legacy not in _warned:
            _warned.add(legacy)
            log.warning("环境变量 %s 已弃用，请改用 %s；本次启动仍兼容旧名。", legacy, canonical)
