# -*- coding: utf-8 -*-
"""版本标识。

## 为什么要单独一个文件

交付包不是 git 仓库（现场拿到的是一堆源码），出问题时第一句话永远是
「学校装的是哪一版」。版本号散落在 `package.json`、FastAPI 的 title 里，
现场没人能可靠回答这个问题。

`VERSION` 文件放在包根目录，由发布流程写入；本模块读它，并把它带进：
  - `/api/health` 的响应（现场一条 curl 就能确认版本）
  - 启动日志（`launcher.log` 里能看到每次启动的版本）
  - `/openapi.json` 的 version 字段（FastAPI 自动带出）

读不到文件时回退到 `0.0.0-dev`，**不抛异常**：版本号缺失不该让服务起不来。
"""
from __future__ import annotations

from pathlib import Path

_FALLBACK = "0.0.0-dev"


def _read() -> str:
    # backend/app/version.py → parents[2] 是包根目录（与 backend/ 同级）
    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "VERSION", root / "backend" / "VERSION"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text.splitlines()[0].strip()
    return _FALLBACK


__version__ = _read()
