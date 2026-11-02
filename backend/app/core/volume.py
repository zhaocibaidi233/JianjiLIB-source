# -*- coding: utf-8 -*-
"""卷册号 / 排架号的**纯规则**（无 IO，可单测）—— · P0。

这里只放字符串规则，不碰数据库，与 `core/callno.py` 同性质：
索书号规则可以在没有数据库的情况下被单测覆盖。

索书号唯一口径：

    call_no = 基号 + （VOLUME_SEP + 卷册号，可空）
    基号    = 分类号 + "/" + 书次号

例：K209/1（无卷册）、K209/1:上册、K209/1:上册/中册/下册（合订本，卷册自带斜杠）。

**只允许通过 `call_no_base()` / `join_call_no()` 读写卷册段**——合订本的卷册号
自身含斜杠，任何 `call_no.split('/')[2]` 都会切错。
"""
from __future__ import annotations

import re
import unicodedata

# 卷册号分隔符。
#
# 为什么是 ":" 而不是 "/"：
#   `/` 在大陆规范里固定用于分隔**分类号与书次号**（某市图书馆、
#   地方图书排架行业标准 均如此定义），并且已被
#   **附加区分号**占用 —— 佛山标准规定「同一责任者的同一分类号的不同著作，
#   从第二种起用 '/' 加序号区分」（如 I210.4/L824/2）。卷册号若也用 "/"，
#   会与附加区分号产生歧义，故改用 ":"。
#   （某省图书馆的多卷书惯例是 "/v.1"，同样说明 "/" 段的归属是分工问题。）
#
# 历史：最初按历史数据样本定为 "/"，后经规范核对回退为 ":"。
VOLUME_SEP = ":"

# 转库匹配键（`records.volume_key`）的分段分隔符：`"{200$h}|{200$i}"`。
# 与 `VOLUME_SEP` 不同用途，别混：`VOLUME_SEP` 是**索书号**里的卷册段分隔符，
# 这个是**匹配键**内部的分段符（见 `normalize_volume_key`）。
VOLUME_KEY_SEP = "|"

# 与 records.volume / holdings.volume 的列宽一致；超过 → 400（硬上限，禁止静默截断）
VOLUME_MAX_LEN = 32
# 与 records.shelf_no 的列宽一致；超过 → 400（硬上限）
SHELF_NO_MAX_LEN = 64
# 软建议：超过只提示，不阻断保存（PRD Q5=B）
SHELF_NO_SOFT_MAX_LEN = 32

# 排架号允许字符集（PRD §2.3）：中英文、数字、- / . 空格
_SHELF_NO_ALLOWED = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff\-/. ]+$")


def derive_volume_seq(volume: str | None) -> int | None:
    """从卷册号文本里解析出排序号；解析不出返回 None。

    只认阿拉伯数字：`"2"→2`、`"(3)"→3`、`"第2卷"→2`、`"上册"→None`、
    `"上册/中册/下册"→None`、`""→None`。

    中文数字（"第二卷"）不在此解析——那类写法由候选清单自带的 seq 提供
    （前端 `VOLUME_OPTIONS`，命中候选直接用 option.seq）。
    """
    s = (volume or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


_WS_RUN = re.compile(r"\s+")


def _norm_key_part(value: str | None) -> str:
    """匹配键的单段规范化：NFKC + 删除所有空白 + 小写。

    卷册键只用于转库匹配，不是展示值：
      - NFKC 把全角数字/字母收敛到半角（`２` → `2`、`Ａ` → `a`）；
      - 删除内部空白，`上 册` 与 `上册` 视为同一卷；分辑名里的空格没有检索意义。
    """
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    return _WS_RUN.sub("", s).strip().lower()


def normalize_volume_key(number: str | None, title: str | None) -> str | None:
    """`200$h`（分辑/册号）+ `200$i`（分辑/册名）→ 稳定的转库匹配键。

    用途（契约 §15.5）：转库时按 `ISBN + volume_key` 匹配目标记录，避免把不同卷
    合并到同一条书目；无 ISBN 不合并。

    规则：
      - NFKC 归一（全角数字/字母 → 半角）；删除所有空白；统一 `lower()`；
      - 两边都空 → `None`（表示"没有卷信息"，调用方据此走"要求人工确认"分支）；
      - 只有一边 → 返回该边的规范化值，**不带分隔符**（只有册名时键里没有空段，
        不会与"有册号无册名"撞成同一形态）；
      - 两边都有 → `"{number}|{title}"`（`VOLUME_KEY_SEP`）。

    ⚠️ **不接收 `200$v`**：`200$v` 是卷标识，只在 4-- 嵌套时表示卷，不参与 D1 的
    分卷判据（§2.6 / D1）。本函数只有两个参数，接线方不得把 `$v` 塞进任一段。

    纯函数：不读 DB、不 import models，可在无数据库时单测。
    """
    n = _norm_key_part(number)
    t = _norm_key_part(title)
    if not n and not t:
        return None
    if not n:
        return t
    if not t:
        return n
    return f"{n}{VOLUME_KEY_SEP}{t}"


def call_no_base(call_no: str | None, volume: str | None) -> str:
    """剥掉卷册号后缀，返回基号 '分类号/书次号'。

    用**后缀精确匹配**而非 split('/')：合订本卷册号自带斜杠（"上册/中册/下册"），
    按分隔符切会切错。匹配不上就原样返回（保证对历史数据幂等）。
    """
    s = (call_no or "").strip()
    v = (volume or "").strip()
    if not s or not v:
        return s
    suffix = VOLUME_SEP + v
    # 长度必须严格大于后缀：否则基号会被剥成空串
    if len(s) > len(suffix) and s.endswith(suffix):
        return s[: -len(suffix)]
    return s


def join_call_no(base: str | None, volume: str | None) -> str | None:
    """基号 + 卷册号后缀（`VOLUME_SEP` 分隔）。基号为空则返回 None。"""
    b = (base or "").strip()
    v = (volume or "").strip()
    if not b:
        return None
    return f"{b}{VOLUME_SEP}{v}" if v else b


def shelf_no_soft_warning(shelf_no: str | None) -> str | None:
    """排架号软校验提示（**不阻断保存**，只在输入框下方显示灰/橙提示）。

    返回 None 表示没问题。后端**不校验、不拦截、不改写**，只有超 64（列宽）才 400。
    """
    s = (shelf_no or "").strip()
    if not s:
        return None
    if len(s) > SHELF_NO_SOFT_MAX_LEN:
        return f"排架号较长（{len(s)} 字），建议不超过 {SHELF_NO_SOFT_MAX_LEN} 字"
    if not _SHELF_NO_ALLOWED.fullmatch(s):
        return "含非常用字符，仅作提示，不影响保存"
    return None
