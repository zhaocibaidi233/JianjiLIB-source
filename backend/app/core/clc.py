# -*- coding: utf-8 -*-
"""分类号截取（**套录 / 外部导入时生效**）——纯规则，无 IO。
具体规则以本模块常量为准。

背景：小学图书馆编目员套录（Z39.50 某外部源 / 资源库）进来的 `690$a` 分类号普遍
过长（如 `S759.991-49`、`G624.203`），索书号上架标签太长。需求是**外部数据进库那
一瞬间把分类号截短**。

分类号有两处"尾巴"，各自可配置：

  1. **小数点后保留几位** —— `digits`（0..6，默认 1）。
     只认小数点后**开头连续的数字串**：一遇到**非数字**（复分号，或**第二个小数点**）
     就停止，其后的内容一律丢弃 —— 所以 `digits` 取值再大也不会把复分号里的数字
     并进来（`O156.2+1` digits=5 → `O156.2`，而不是 `O156.21`）。`digits = 0` → 连
     小数点一起去掉（`G624.203` → `G624`）；超过实际小数位 → 原样，不补零。

  2. **总论复分号（`-`）后保留几位** —— `dash_digits`（0..6，默认 3）。
     `-` 在《中图法》里就叫**总论复分号**（界面文案用这个标准名）；它出现在小数点哪一侧
     含义完全不同、处理方式**相反**（见下"形态三分"）。`dash_digits = 0` → 连这个 `-`
     一起去掉（`R-092` → `R`）。字段名里的 `dash` 指的就是那个短横，**不是**另一套术语。

  3. **其它复分号（`+` / `/`）怎么处理** —— `marks` 三选一：`auto` / `keep` / `drop`
     。`auto` 档下 `-` 归上面第 2 条管；`keep` / `drop` 是"**不看形态**，
     一律保留 / 一律去掉"的覆盖档，连 `-` 一起管（语义与 逐字一致）。
     ⚠️ `keep` 档下 `digits = 0` 时，小数点只在**源串里本就紧贴在复分号前**才留着
     （`TJ411.+1`）；源串本来有小数位、只是被 0 位削掉的，复分号要**接回主体** ——
     `S759.991-49` → `S759-49`，**不是** `S759.-49`（那个号中图法里不存在）。

  4. **字母后数字位数阈值** —— `base_digits`（0..6，**默认 0 = 关闭**）。
     量的是**紧随前导字母的那段数字**有几位（`F224` → 3、`Q95` → 2、`R` → 0），
     **超过**它就**整段去掉总论复分号**：`F224-39` → `F224`。**默认关**是因为它是一次
     **行为变更**（真实表里有 33 条会因此变短），要用户显式打开。只在 `marks="auto"`
     且形态 R1（见下）时才有意义 —— 形态 R2 本来就把 `-` 去掉了。

**形态三分（只在 `marks="auto"`，即默认档下生效；）** —— 判据是**总论复分号在
小数点的哪一侧**，与字母个数无关（按形态判断，不看字母个数）：

  | 形态 | 例子 | 处理 |
  |---|---|---|
  | **R1** `-` 在小数点**前**（或没有小数点） | `R-092.1`、`B81-093.51`、`TJ-49`、`Q95-49` | **保留** `-` 及其后开头数字串（按 `dash_digits` 截），**小数点及其后一律丢弃** |
  | **R2** `-` 在小数点**后** | `S759.991-49`、`TJ411.1-49`、`G259.1-66` | 小数点与总论复分号**都丢掉** → 回到主体（第一个 `.` 之前） |
  | **R3** 无总论复分号 | `G624.203`、`Q95`、`TJ411.+1` | 走上面第 1 / 3 条（小数点后 `digits` 位 + 复分号口径） |

  ★ **R1 修掉的是一个边界问题**：`R-092.1` 曾产出 `R.1` —— `-092` 被当成
  复分号抹掉，剩下的 `R.1` 是无效分类号。
  ★ **R2 的处理方式**：`S759.991-49` 从 `S759.9` 变为 `S759`。
  ★ R1 里总论复分号后数字串**之后**的东西（如 `Q94-34+1` 的 `+1`）**原样接回** —— `-34`
  与 `+1` 是两个不同维度的复分，丢掉会把 `Q94-34+1` / `Q94-34+2` 混成一格。

**为什么 `dash_digits` 默认 3**：常见总论复分号在 `-` 后为 1–3 位数字；
默认 3 位对绝大多数分类号等于不截断，同时又是个真能用的旋钮（调小就真会削）。

**规则干跑与「空小数位保留复分号」已移除**：现在一个旋钮只答一件事、
判定只有一条路径。保留项与取整规则以本模块常量为准。

本模块是**纯函数**（与 `core/volume.py` / `core/callno.py` 同性质），可在没有
数据库的情况下被单测覆盖。

调用点只有两处，都是"**外部数据进入图书库**"的那一瞬间（用户裁定口径：
「套录 + 外部导入都截，手工输入不拦」）：
  1. `services/localize.localize_record()` —— 套录
     （`POST /api/cataloging/prepare`、`POST /api/quick-entry`）；
  2. `services/batch.import_records_to_db()` 与 `api/import_export.py` 的清单直导
     —— 外部文件直落图书库（`POST /import/iso?target=records`、`POST /jobs/import-iso`）。

**绝对不要**放进 `core/clean.clean_record()`，也不要放进
`services/convert.record_to_orm_values()`：前者在每次保存/导入时都会跑，后者的
调用方包含 `PUT /records`（编辑保存）—— 一旦挂上去，用户"改了设置再保存一次"
就会被再削一次，且**不可逆**。库里已有的记录一律不动。。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 默认保留的小数点位数（全局设置未配置时的取值）。
DEFAULT_CLASS_NO_DIGITS = 1
# 默认保留的**总论复分号**位数。取 3 是有依据的：
# 常见总论复分号的 `-` 后为 1–3 位数字，默认 3 位对绝大多数分类号等于不截断，
# 同时又是个真能用的旋钮。
DEFAULT_DASH_DIGITS = 3
# 两项位数的可配置上限（取值范围均为 0..CLASS_NO_DIGITS_MAX）。
CLASS_NO_DIGITS_MAX = 6
# 「字母后数字位数」阈值：**超过**它就去掉总论复分号。**0 = 关闭**，
# 且出厂默认就是 0 —— 打开后会改变带总论复分号分类号的截取结果
# （`F224-39` → `F224`），属于**行为变更**，必须由用户显式打开，不能悄悄生效。
DEFAULT_BASE_DIGITS = 0

# 复分号口径。`auto` 是默认档； 之后它还多管一件事：`-` 的去留
# 由**位置**决定（见 `shorten_class_no()` 的形态三分）。故设置页里 `auto` 的中文名是
# 「按 - 的位置判定」——「形态」是本文档的实现词，不上界面。
MARKS_MODES = ("auto", "keep", "drop")
DEFAULT_MARKS_MODE = "auto"

# 复分号字符集：`-` / `+` / `/`（`-` 放首位才是字面量，不是范围）。
# ⚠️ `-` 虽然也在里面（`keep` / `drop` 两个覆盖档仍要"一律"处理它），但 `auto` 档下
#    `-` **不走**这里 —— 它的去留由"在小数点前还是后"决定，见 `shorten_class_no()`。
_MARK_RE = re.compile(r"[-+/]")
# 同上，元组形式：判断"某段是不是以复分号开头"用（字符串的 `"" in s` 恒为真，不能用）
_MARK_PREFIXES = ("-", "+", "/")
# 开头连续的数字串（小数点后 / 总论复分号后都只认这一段）。
# Python 的 `\d` 认 Unicode 数字类别（含全角 `９`），前端镜像必须用 `\p{Nd}` 才对称。
_DIGITS_RE = re.compile(r"\d*")
# 前导字母 + **紧随其后**的数字串（`base_digits` 量的就是第 2 组；）。
# 用 `match` 只认开头，后面还有什么都无所谓（`F224-39` 上只取到 `F224` 里的 `224`）。
_LEAD_RE = re.compile(r"([A-Za-z]+)(\d*)")


def _base_digits(head: str) -> int | None:
    """`-` 之前那一段（代码里叫 `head`）中，**紧随前导字母的数字串**有几位。

    `Q95` → 2、`F224` → 3、`R` → 0。**没有前导字母**（畸形输入如 `123-45`）→ `None`
    —— 规则不适用，调用方据此**不猜**（与本模块其它防御分支同一口径）。
    """
    m = _LEAD_RE.match(head)
    return len(m.group(2)) if m else None


@dataclass(frozen=True)
class ClassNoRule:
    """一条分类号截取规则：小数点位数 + 总论复分号位数 + 复分号口径 + 字母后数字位数阈值。

    四个字段**各带默认**，所以 `ClassNoRule()` 就是出厂口径。构造时不校验，
    夹取与白名单在两个地方各做一次：`shorten_class_no()`（最后防线，防调用方漏校验）
    与 `rule_from_raw()`（从库里读脏值的容错口）。frozen ⇒ 可安全共享实例。

    ⚠️ **字段顺序即位置参数顺序**，前三位定下来了：`ClassNoRule(1, 3, "auto")`。
    改动字段个数前先数一遍位置参数调用点 —— 删掉原第 4 个字段
    `keep_mark_when_empty` 时，`base_digits` 就此顶上第 4 位，
    `ClassNoRule(1, 3, "auto", False, 2)` 这类旧写法会**静默错位**（`False` 变成
    `base_digits` 的值），所以那批调用点必须一起改。
    """
    digits: int = DEFAULT_CLASS_NO_DIGITS
    dash_digits: int = DEFAULT_DASH_DIGITS
    marks: str = DEFAULT_MARKS_MODE
    base_digits: int = DEFAULT_BASE_DIGITS

    def as_dict(self) -> dict:
        """按固定键序出参（`GET /settings/class_no` 的返回体，前端逐键绑定）。

        ⚠️ 新键只能**追加在末尾**：前端 `normalizeClassNoRule()` 是按键取值（不看序），
        但键序一乱会让"两份实现的 diff"看不出改了哪一项。
        """
        return {"digits": self.digits,
                "dash_digits": self.dash_digits,
                "marks": self.marks,
                "base_digits": self.base_digits}


# 出厂规则（模块级单例；frozen 数据类可安全共享，勿改成可变对象）。
DEFAULT_CLASS_NO_RULE = ClassNoRule()


def _clean_digits(raw, default: int) -> int:
    """把一个"位数"设置归一：非整数（含 `bool`，它是 int 子类会伪装成 1）→ 默认；越界 → clamp。

    `digits` / `dash_digits` / `base_digits` **三项共用**本函数 —— 三项的容错口径必须逐字
    相同，否则会出现"小数点位数容错、总论复分号位数不容错"这种没人能解释的不对称。
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    return max(0, min(raw, CLASS_NO_DIGITS_MAX))


def rule_from_raw(raw) -> ClassNoRule:
    """把**可能是脏值**的输入归一成 `ClassNoRule`。**绝不抛**。

    容错口径与 `services/settings.py` 的其它设置项一致（读设置不该让编目流程失败）：
      - 已经是 `ClassNoRule` → 原样；非 dict → 出厂规则；
      - `digits` / `dash_digits` / `base_digits`：非整数（含 `bool`）→ 各自默认；
        可解析但越界 → clamp；
      - `marks`：不在 `MARKS_MODES` → 默认；
      - **白名单外的键一律不看**：所以库里存量值多带的 `keep_mark_when_empty`
        （已移除的旋钮）**不需要迁移**，读出来自动被忽略。
    """
    if isinstance(raw, ClassNoRule):
        return raw
    if not isinstance(raw, dict):
        return DEFAULT_CLASS_NO_RULE
    marks = raw.get("marks", DEFAULT_MARKS_MODE)
    if marks not in MARKS_MODES:
        marks = DEFAULT_MARKS_MODE
    return ClassNoRule(
        digits=_clean_digits(raw.get("digits", DEFAULT_CLASS_NO_DIGITS),
                             DEFAULT_CLASS_NO_DIGITS),
        dash_digits=_clean_digits(raw.get("dash_digits", DEFAULT_DASH_DIGITS),
                                  DEFAULT_DASH_DIGITS),
        marks=marks,
        base_digits=_clean_digits(raw.get("base_digits", DEFAULT_BASE_DIGITS),
                                  DEFAULT_BASE_DIGITS))


def _as_rule(rule) -> ClassNoRule:
    """接受 `ClassNoRule`（正式路径）或**整数位数**（旧签名）。

    旧签名兼容入口：`shorten_class_no(x, 1)` ≡ `shorten_class_no(x, ClassNoRule(digits=1))`。
    其它类型直接 `TypeError` —— 纯函数不做静默兜底，脏值该在设置边界被 `rule_from_raw`
    挡掉；在这里默默当默认处理，只会让"设置没生效"变成一个查不出来的哑 bug。
    """
    if isinstance(rule, ClassNoRule):
        return rule
    if isinstance(rule, int) and not isinstance(rule, bool):
        return ClassNoRule(digits=rule)
    raise TypeError("rule 须为 ClassNoRule 或整数位数")


def shorten_class_no(class_no: str | None,
                     rule: ClassNoRule | int = DEFAULT_CLASS_NO_RULE) -> str:
    """按规则截短分类号；空值 / 畸形输入一律原样返回（**绝不抛**）。

    纯字符串规则：空值、无小数点、畸形输入（`.5`、主体只有复分号如 `-.5`）原样返回；
    非字符串入参先按 `str()` 处理、并先 `strip()`（`"  G624.203  "` → `G624.2`）。

    >>> shorten_class_no("S759.991-49")               # R2：总论复分号在小数点后 → 回到主体
    'S759'
    >>> shorten_class_no("R-092.1")                   # R1：保住 -092，丢掉 .1（修 R.1 那个 bug）
    'R-092'
    >>> shorten_class_no("G624.203")                  # R3：保留小数点后 1 位
    'G624.2'
    >>> shorten_class_no("G624.203", ClassNoRule(digits=0))        # 0 位：连小数点一起去掉
    'G624'
    >>> shorten_class_no("B947.2", ClassNoRule(digits=3))          # 超过实际小数位：原样，不补零
    'B947.2'
    >>> shorten_class_no("O156.2+1", ClassNoRule(digits=5))        # 遇复分号即停：不并入 "+1" 的数字
    'O156.2'
    >>> shorten_class_no("R-092", ClassNoRule(dash_digits=0))      # 总论复分号 0 位：连 - 一起去掉
    'R'
    >>> shorten_class_no("Q95-49")                    # R1：总论复分号在小数点前 → 原样
    'Q95-49'
    >>> shorten_class_no("TJ011.+1")                  # 小数点后没有数字 → 只剩主体
    'TJ011'
    >>> shorten_class_no("S759.991-49", ClassNoRule(marks="keep"))
    'S759.9-49'
    >>> shorten_class_no("S759.991-49", ClassNoRule(marks="keep", digits=0))   # 0 位 → 复分号接回主体
    'S759-49'
    >>> shorten_class_no("TJ411.+1", ClassNoRule(marks="keep", digits=0))      # 小数点本就在复分号前 → 留着
    'TJ411.+1'
    >>> shorten_class_no("I207.227.42", ClassNoRule(marks="keep", digits=0))   # 第二个小数点不是复分号
    'I207'
    >>> shorten_class_no("Q95-49", ClassNoRule(marks="drop"))
    'Q95'
    >>> shorten_class_no("F224-39", ClassNoRule(base_digits=2))    # 字母后 3 位 > 2 → 去掉总论复分号
    'F224'
    >>> shorten_class_no("Q95-49", ClassNoRule(base_digits=2))     # 字母后 2 位，不超 → 原样
    'Q95-49'
    >>> shorten_class_no("F224-39")                               # 阈值 0（出厂默认）= 关闭
    'F224-39'
    >>> shorten_class_no("R-092.1", ClassNoRule(base_digits=2))    # 字母后 0 位 → 阈值不触发
    'R-092'
    >>> shorten_class_no("S759.991-49", ClassNoRule(base_digits=2))  # R2 本来就去掉，与阈值无关
    'S759'
    """
    r = _as_rule(rule)
    s = "" if class_no is None else str(class_no).strip()
    if not s:
        return s

    i_dash = s.find("-")
    i_dot = s.find(".")

    # ---- 形态 R1 / R2：只在默认档 `auto` 下生效 ----
    # `keep` / `drop` 是"不看形态、一律保留 / 一律去掉"的覆盖档，落到下面的通用逻辑，
    # 语义与 逐字一致（那一版没有形态判定）。`-` 之所以要单独看位置，就是因为
    # 这两种形态的处理**相反**：在小数点前它是"细分"，在小数点后它是"多余的长尾"。
    if i_dash >= 0 and r.marks == "auto":
        if i_dot < 0 or i_dash < i_dot:
            # R1：`-` 在小数点**前**（或没有小数点）→ 丢掉小数点及其后，截 `-` 后的数字串。
            seg = s if i_dot < 0 else s[:i_dot]
            head, _, tail = seg.partition("-")
            if not head:
                return s                   # 防御：主体只剩总论复分号（`-49` / `-.5`）
            run = _DIGITS_RE.match(tail).group(0)
            if not run:
                return s                   # 防御：总论复分号后不是数字 → 不猜
            # ：字母后紧随数字**超过** `base_digits` 位 → 整段去掉总论复分号。
            # 放在 `run` 检查**之后**（而不是之前）是有意的：`-` 后面不是数字属于畸形输入，
            # 那是上面那条"不猜，原样返回"的防线的职责，两条规则不要互相覆盖。
            # `base_digits = 0`（出厂默认）⇒ 本判定恒不触发，行为与 那一版逐字一致。
            nd = _base_digits(head)
            if r.base_digits > 0 and nd is not None and nd > r.base_digits:
                return head                # `-` 及其后**整段**丢弃（含 `Q94-34+1` 的 `+1`：
                                           # 它依附于 `-34`，`-34` 没了它无意义）
            # 夹到 0..CLASS_NO_DIGITS_MAX：本函数自带最后一道防线，即使调用方漏了校验
            # （正常路径由 api/ai.py 的 PUT 校验 + rule_from_raw 的读取夹取保证）。
            n = max(0, min(int(r.dash_digits), CLASS_NO_DIGITS_MAX))
            keep = run[:n]
            if not keep:
                return head                # `dash_digits = 0` → 连总论复分号一起去掉（与 digits=0 对称）
            # 数字串**之后**的东西原样接回：真实表里有 16 条 `Q94-34+1` / `Q94-34+2` 这类
            # ——`-34` 与 `+1` 是两个不同维度的复分，丢掉会把 `+1`/`+2` 混成一格。
            return f"{head}-{keep}{tail[len(run):]}"
        # R2：`-` 在小数点**后** → 小数点与总论复分号**都丢掉**，回到主体。
        head = s[:i_dot]
        if not head:
            return s                       # 畸形输入（理论上到不了）
        return head

    if i_dot < 0:
        # 无小数点：没有可截的数字，只剩"要不要去掉主体里的复分号"这一个问题。
        if r.marks == "drop":
            base = _MARK_RE.split(s, maxsplit=1)[0]
            # 主体只剩复分号（如 "-49"）→ 原样，绝不能削成空串（等于把 $a 清空）。
            return base or s
        # auto / keep：原样。没有小数点的粗类号里复分号是**唯一**的细分手段
        # （`Q95-49` 与 `Q95` 不是同一格），去掉会把不同的书混为一类。
        return s

    head, _, frac = s.partition(".")
    if not head:
        return s                           # 防御：畸形输入（如 ".5"）原样返回
    base = _MARK_RE.split(head, maxsplit=1)[0]
    if not base:
        # 防御：主体只剩复分号（如 "-.5" / "+.5"）→ 原样返回。
        # 否则 base 为空会把结果削成 ".5" 甚至空串，等于把 $a 清空（数据丢失）。
        return s
    d = max(0, min(int(r.digits), CLASS_NO_DIGITS_MAX))
    # 小数点后【开头连续的数字串】= run；其后的 `rest` 就是紧随的那段复分号（可为空串）。
    # 一遇非数字（`-`/`+`/`/` 复分号、第二个小数点）即停，故 digits 再大也不会并进
    # 复分号里的数字（`O156.2+1` d=5 → `O156.2`，不是 `O156.21`）。
    run = _DIGITS_RE.match(frac).group(0)
    keep, rest = run[:d], frac[len(run):]

    if r.marks == "keep":
        # 一律保留复分号：主体里的复分号也在（`head` 整体就是"主体 + 主体复分号"）。
        # 只接回**复分号段**；小数点后那个"第二个小数点"不是复分号，
        # 照旧丢弃 —— 否则 `I207.227.42` 会原样返回，等于没截。
        tail = f"{keep}{rest}" if rest.startswith(_MARK_PREFIXES) else keep
        if not tail:
            return head                    # 没数字也没复分号 → 只剩主体
        if keep:
            return f"{head}.{tail}"
        # 走到这里：`keep == ""`（`digits = 0`）但尾巴非空 —— 这段尾巴是**复分号**，
        # 不是小数位。小数点只在**源串里本来就紧贴在复分号前**时才留着
        # （`TJ411.+1`，此时 `run == ""`）；源串本来有小数位、只是被 0 位削掉
        # （`S759.991-49`）则必须**接回主体** —— 补一个"后面没有数字的小数点"
        # 会造出中图法里不存在的 `S759.-49`。
        return f"{head}.{tail}" if not run else f"{head}{tail}"

    # auto（含小数点）/ drop：去掉复分号，只留小数点后的数字串。
    # 小数点后没有数字（`TJ011.+1`）时 `keep == ""` → 只剩主体。
    return f"{base}.{keep}" if keep else base


def shorten_record_class_nos(rec, rule: ClassNoRule | int = DEFAULT_CLASS_NO_RULE) -> int:
    """就地截短记录里**所有** `690` 字段的 `$a`；返回被处理的子字段条数。

    只动 `$a`，其它子字段（如 `$v`）原样。截取结果与原文相同者也计入条数
    （本函数**幂等**：重复调用不改变结果 —— `marks="keep"` 下同样成立，因为结果里
    小数点后的开头数字串就是已截好的那一段）。

    记录对象只需提供 `get_fields(tag)` 与 `field.subfields`（`Subfield.code` /
    `.value`）—— 用鸭子类型是为了让 core 层不必 import `core.marc`，保持本模块
    可脱离记录实现单测。

    套录（`services.localize.localize_record`）与外部文件直落图书库
    （`services.batch.import_records_to_db` / `api/import_export.py`）**共用本函数**，
    两处不要各写一遍遍历。
    """
    r = _as_rule(rule)
    processed = 0
    for f in rec.get_fields("690"):
        for sf in getattr(f, "subfields", None) or []:
            if getattr(sf, "code", None) != "a":   # 只动 $a
                continue
            if sf.value is None or not str(sf.value).strip():
                continue                            # 空值不动（避免 None -> ""）
            sf.value = shorten_class_no(sf.value, r)
            processed += 1
    return processed
