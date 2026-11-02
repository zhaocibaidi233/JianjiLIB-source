# -*- coding: utf-8 -*-
"""Alembic 迁移环境。

连接串来源（与运行时同源，避免"迁移连的库和程序连的库不是一个"）：
  1. JIANJI_DATABASE_URL 环境变量（临时指到别的库时用，如生成/验证基线）；
  2. app/db.py 的 DATABASE_URL（JIANJI_DB_PASSWORD / pgpass 兜底，见 app/db.py）。

include_schemas=True：资源库表在独立的 `res` schema 里，
必须让 Alembic 看见两个 schema，否则 res.* 的变更不会被 autogenerate 发现。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 backend/app 可导入（alembic.ini 的 prepend_sys_path 也会加，这里兜底一份）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, DATABASE_URL  # noqa: E402
import app.models  # noqa: E402,F401  (把全部模型注册进 Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return os.environ.get("JIANJI_DATABASE_URL") or DATABASE_URL


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库（alembic upgrade head --sql）。"""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行。"""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            # res schema 里的表也要比对（autogenerate / alembic check 都靠它）
            include_name=lambda name, type_, parent_names: True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
