# -*- coding: utf-8 -*-
"""日志脱敏：把 PII 挡在**落盘之前**。

## 为什么需要这一层

uvicorn 的访问日志把**完整 URL（含 query）**写进 `launcher.log`，而本系统有把
身份标识放在 query 里的接口：

    GET /api/circulation/reader?card_no=T00012345

于是每调一次，就有一个学生的借书证号被写进日志。日志会被启动器界面展示、被现场
打包成排障附件发出去——它本来只是排障资产，不该顺带成为一份读者名单。

## 为什么在日志层做，而不是改接口参数

`card_no` 放在 query 里是既有契约（借还台的"刷读者证"直接拼 URL，H5 与扫码枪都在用）。
改参数要动前端、动第三方调用方，而别处随时可能再引入一个新的 PII 参数名。
在日志出口统一脱敏，是唯一**不会漏**的位置：新增接口只是多一个 query 参数，过滤
规则按参数名生效。

## 边界

- 只改**消息里**的 `key=value` 对，不动结构化字段（`record.args` 被合并成字符串
  后重写，避免下游 handler 再用旧 args 拼一次原文）。
- 值按 `&`/空白/引号收尾，能覆盖中文与 JSON 片段；不做 URL 解码——解码后写回日志
  反而更容易漏（`%%31` 之类）。
"""
from __future__ import annotations

import logging
import re

#: query / JSON 片段里出现这些键名就当 PII 抹掉（大小写不敏感）。
#: 刻意**不写 `name`**：它太泛（`?name=三体` 这类检索词是有用的排障信息），
#: 读者姓名用 `reader_name` 这个明确键名覆盖。
PII_KEYS = (
    "card_no", "barcode", "username", "password", "token", "phone",
    "id_card", "reader_name",
)

_PII_RE = re.compile(
    r"(?i)\b(" + "|".join(PII_KEYS) + r")=([^&\s\"',}]+)")

REDACTED_PLACEHOLDER = "***"


def redact_access_log(record: logging.LogRecord) -> bool:
    """`logging.Filter` 的实现：就地改写 `record`，**永远返回 True**（只脱敏不拦截）。

    先 `getMessage()`（把 args 拼进去，访问日志的 msg 是 `%s - "%s %s HTTP/%s" %d`），
    有命中就把合并后的字符串当成新 msg、清空 args —— 否则 handler 会用旧 args 再拼
    一次，脱敏等于没做。
    """
    try:
        message = record.getMessage()
    except Exception:           # 异常 args 不能把日志本身弄挂
        return True
    cleaned = _PII_RE.sub(rf"\1={REDACTED_PLACEHOLDER}", message)
    if cleaned != message:
        record.msg, record.args = cleaned, ()
    return True


def install_access_log_redaction() -> None:
    """给 uvicorn 的访问日志挂上脱敏过滤器（重复调用无副作用）。

    在 `create_app()` 里调用：uvicorn 先配好 logging 再导入 app，因此这里挂上的
    logger 级过滤器对它已经存在的 handler 同样生效。
    """
    logger = logging.getLogger("uvicorn.access")
    if not any(getattr(f, "_dsh_redact", False) for f in logger.filters):
        redact_access_log._dsh_redact = True        # type: ignore[attr-defined]
        logger.addFilter(redact_access_log)
