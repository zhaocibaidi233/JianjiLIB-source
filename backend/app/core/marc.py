# -*- coding: utf-8 -*-
"""CNMARC 记录模型与 ISO 2709 字节级读写。

设计要点（均由真实数据实证，见、07）：
- 解析按头标声明长度顺序读取，不能按 0x1D 分割（记录间可能有 CRLF）；
- 单条坏记录容错跳过并报告，不拒绝整批；
- 写出时重算全部长度字段，**并强制按 tag 升序输出**（`record_to_iso` 内稳定排序）——
  ISO 2709 / CNMARC 要求目次区按字段号升序，而 `rec.fields` 的存储顺序不保证升序
  （905 由馆藏表重建时被 append 到末尾），排序是导出件合规的唯一保证点。
  （原文写"不强制排序"，与实现不符，已修正。）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional

FS, RS, SF = 0x1E, 0x1D, 0x1F
# 一条 ISO 2709 记录的最小字节数：leader(24) + 记录分隔符(1)。声明长度小于它的
# 记录必是坏数据（见 iter_iso 内的守卫注释）。
_MIN_RECORD_LEN = 25

ENCODING_BY_LEADER = {"a": "utf-8"}          # 头标第 9 位 -> 解码用字符集
DEFAULT_ENCODING = "gb18030"                 # 国内存量数据默认 GBK 系


class MarcError(Exception):
    pass


class MarcWriteError(MarcError):
    pass


@dataclass
class Subfield:
    code: str
    value: str


@dataclass
class Field:
    """一个 MARC 字段。控制字段(00X)用 data；数据字段用 ind1/ind2/subfields。"""
    tag: str
    ind1: str = " "
    ind2: str = " "
    data: str = ""                      # 控制字段内容，或数据字段的字段级数据（罕见）
    subfields: list[Subfield] = field(default_factory=list)

    def is_control(self) -> bool:
        return self.tag.startswith("00")

    def get(self, code: str) -> Optional[str]:
        """第一个匹配子字段的值。"""
        for sf in self.subfields:
            if sf.code == code:
                return sf.value
        return None

    def all(self, code: str) -> list[str]:
        return [sf.value for sf in self.subfields if sf.code == code]

    def set(self, code: str, value: str) -> None:
        """设置第一个匹配子字段的值，不存在则追加。"""
        for sf in self.subfields:
            if sf.code == code:
                sf.value = value
                return
        self.subfields.append(Subfield(code, value))

    def add(self, code: str, value: str) -> None:
        self.subfields.append(Subfield(code, value))


@dataclass
class Record:
    leader: str = " " * 24
    fields: list[Field] = field(default_factory=list)

    # ---- 查询 ----
    def get_fields(self, tag: str) -> list[Field]:
        return [f for f in self.fields if f.tag == tag]

    def get_first(self, tag: str, code: Optional[str] = None) -> Optional[Field]:
        for f in self.fields:
            if f.tag != tag:
                continue
            if code is None or f.get(code) is not None:
                return f
        return None

    def first_value(self, tag: str, code: str) -> Optional[str]:
        f = self.get_first(tag, code)
        return f.get(code) if f else None

    def all_values(self, tag: str, code: str) -> list[str]:
        out: list[str] = []
        for f in self.get_fields(tag):
            out.extend(f.all(code))
        return out

    def title(self) -> Optional[str]:
        """展示用完整题名：正题名（200$a）+ 分辑名（200$i，全部层级）。

        为什么拼 $i：多分卷书（如《认识中国植物》各省分册）套录回来 200$a 全同，
        只有 $i 能区分——套录候选列表、资源库检索、AI 字段提示都吃这个方法，
        不拼则候选全是同名，编目员无法确认选中的是手里哪一册。与检索冗余列
        `compute_search_columns` 的 title_norm 口径一致（2026-10-30）。
        MARC 原文不动：$i 仍在 200 字段里，编辑器照常编辑。

        为什么取**全部** $i 而非第一个：多级分辑（如《学汉语分级读物》
        `$h第2级 $i文学故事；三国演义 $h1 $i桃园三结义`）的第二层 $i 才是
        单册名，只取第一个仍分不清同套的不同册。
        CNMARC 允许 200 内 $h/$i 交替重复，`all_values` 按字段内顺序返回。

        每段去尾随 ISBD 标点再拼：套录源的 200$a 常带 ` / `（著录残留），
        不清会让题名变成「学汉语分级读物 /  文学故事；三国演义」这种断口。
        这里只做**尾部标点裁剪**（正则与 clean.normalize_title 同款但不 import
        ——clean 依赖本模块，反向 import 会成环）。
        """
        import re
        tail = re.compile(r"\s*[:;=/,.]\s*$")
        main = tail.sub("", self.first_value("200", "a") or "") or None
        vols = [tail.sub("", v) for v in self.all_values("200", "i")]
        vols = [v.strip() for v in vols if v and v.strip()]
        vols = [v for v in vols if not main or v not in main]
        if not main:
            return " ".join(vols) or None
        if not vols:
            return main
        return f"{main} {' '.join(vols)}"

    def control_value(self, tag: str) -> Optional[str]:
        """控制字段（001/005/100 等 00X 编号）的数据内容。"""
        f = self.get_first(tag)
        return f.data if f else None

    # ---- 序列化 ----
    def to_json(self) -> str:
        import json
        arr = []
        for f in self.fields:
            if f.is_control():
                arr.append({"tag": f.tag, "data": f.data})
            else:
                item: dict = {"tag": f.tag, "i1": f.ind1, "i2": f.ind2, "sf": [[s.code, s.value] for s in f.subfields]}
                if f.data:
                    item["data"] = f.data
                arr.append(item)
        return json.dumps(arr, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Record":
        import json
        rec = cls()
        for item in json.loads(text):
            if "sf" in item or "i1" in item:
                rec.fields.append(Field(item["tag"], item.get("i1", " "), item.get("i2", " "),
                                        item.get("data", ""),
                                        [Subfield(c, v) for c, v in item.get("sf", [])]))
            else:
                rec.fields.append(Field(item["tag"], data=item.get("data", "")))
        return rec

    def to_mrk(self) -> str:
        """渲染为 .mrk 文本格式（MARC break），便于人工查看与测试比对。"""
        lines = [f"=LDR  {self.leader}"]
        for f in self.fields:
            if f.is_control():
                lines.append(f"={f.tag}  {f.data}")
            else:
                body = "".join(f"${s.code}{s.value}" for s in f.subfields)
                lines.append(f"={f.tag}  {f.ind1}{f.ind2}{body}")
        return "\n".join(lines)


# ---------- 解析 ----------

def _decode(b: bytes, charset: str) -> str:
    try:
        return b.decode(charset)
    except UnicodeDecodeError:
        return b.decode(DEFAULT_ENCODING, errors="replace")


def _charset_of(leader: str) -> str:
    return ENCODING_BY_LEADER.get(leader[9:10], DEFAULT_ENCODING)


def parse_record(body: bytes) -> tuple[Optional[Record], Optional[str]]:
    """解析单条记录体（不含结尾 0x1D）。返回 (record, error)，二者必有一方为 None。"""
    if len(body) < 25:
        return None, "记录过短"
    leader = _decode(body[:24], "ascii")
    if not leader[:5].isdigit():
        return None, "头标长度非数字"
    declared = int(leader[:5])
    if len(body) != declared - 1:
        return None, f"长度不符：声明 {declared}，实际 {len(body) + 1}"
    if not leader[12:17].isdigit():
        return None, "基地址非数字"
    base = int(leader[12:17])
    directory = body[24:base - 1]
    if len(directory) % 12 != 0:
        return None, "目次区长度异常"
    charset = _charset_of(leader)
    rec = Record(leader=leader)
    for i in range(0, len(directory), 12):
        entry = directory[i:i + 12]
        try:
            tag = entry[0:3].decode("ascii")
            flen = int(entry[3:7])
            fstart = int(entry[7:12])
        except (ValueError, UnicodeDecodeError):
            return None, f"目次区条目异常(偏移{i})"
        raw = body[base + fstart: base + fstart + flen]
        if raw.endswith(bytes([FS])):
            raw = raw[:-1]
        try:
            if tag.startswith("00"):
                rec.fields.append(Field(tag, data=_decode(raw, charset)))
            else:
                ind1 = chr(raw[0]) if raw else " "
                ind2 = chr(raw[1]) if len(raw) > 1 else " "
                subs: list[Subfield] = []
                parts = raw[2:].split(bytes([SF]))
                head_data = _decode(parts[0], charset)
                for p in parts[1:]:
                    if p:
                        subs.append(Subfield(chr(p[0]), _decode(p[1:], charset)))
                rec.fields.append(Field(tag, ind1, ind2, head_data, subs))
        except Exception as e:  # 单字段损坏不拖垮整条
            return None, f"字段 {tag} 解析异常：{e}"
    return rec, None


@dataclass
class ParsedItem:
    index: int
    record: Optional[Record]
    error: Optional[str]


def iter_iso(data: bytes) -> Iterator[ParsedItem]:
    """顺序读取 ISO 2709 字节流：按头标声明长度取记录，跳过记录间空白(CRLF)。

    产出 ParsedItem；坏记录 record=None 且带 error，流程不中断。
    """
    off, idx = 0, 0
    n = len(data)
    while off < n:
        while off < n and data[off] in (0x0D, 0x0A, 0x20, 0x09):
            off += 1
        if off >= n:
            break
        head = data[off:off + 5]
        if not head.isdigit():
            yield ParsedItem(idx, None, f"偏移{off}: 长度字段非数字 {head!r}，跳至下一记录分隔符")
            nxt = data.find(bytes([RS]), off)
            if nxt < 0:
                break
            off, idx = nxt + 1, idx + 1
            continue
        declared = int(head)
        if declared < _MIN_RECORD_LEN:
            # 头标声明长度小于最小记录长度（leader 本身就占 24 字节）→ 坏数据，跳到下一
            # 0x1D 重同步。**必须守卫**：declared=0 时 `off += declared` 永不前进，本函数
            # 在这里无限循环，io.iso.read_iso 的 errors 也同步无限累积 —— 5 字节的
            # "00000" 文件就能挂死导入线程。
            yield ParsedItem(idx, None,
                             f"偏移{off}: 记录长度声明 {declared} 小于最小值 {_MIN_RECORD_LEN}，跳至下一记录分隔符")
            nxt = data.find(bytes([RS]), off)
            if nxt < 0:
                break
            off, idx = nxt + 1, idx + 1
            continue
        seg = data[off:off + declared]
        if len(seg) < declared:
            # 文末缺 0x1D 是真实存在的数据形态：书商导出时把最后一条的记录分隔符吞掉，
            # 头标里的声明长度却照旧算着它。差的那一个字节就是分隔符本身，正文完整，
            # 不该丢 —— 以前这里一律判"记录截断"，界面显示"导入 N 条"却少了末条，
            # 而且原因与真正的半截记录混在同一个错误里。判据：**剩余字节恰好等于
            # 声明长度减一**（差的正是那个 0x1D），否则仍按截断处理，不猜。
            if n - off == declared - 1:
                rec, perr = parse_record(seg)      # seg 不含分隔符，长度 = 声明长度 - 1
                if rec is not None:
                    yield ParsedItem(idx, rec, "记录末尾无 0x1D（已容错接收）")
                    idx += 1
                    break
                yield ParsedItem(idx, None, perr or "记录截断")
                break
            yield ParsedItem(idx, None, f"偏移{off}: 记录截断 声明{declared} 剩余{n - off}")
            break
        has_term = seg.endswith(bytes([RS]))
        body = seg[:-1] if has_term else seg
        rec, perr = parse_record(body)
        if rec is None and not has_term:
            # 头标声明长度没把分隔符算进去、文件里也确实没有：长度与正文自相矛盾。
            # 这种不靠"补一个字节"去猜，如实报出来（与上一段的"文末少一个字节"区分开）。
            perr = f"{perr}（记录末尾无 0x1D）"
        yield ParsedItem(idx, rec, perr)
        idx += 1
        off += declared


# ---------- 生成 ----------

_DEFAULT_LEADER = "00000nam0 2200000   450"


def record_to_iso(rec: Record, encoding: str = "gbk") -> bytes:
    """序列化为 ISO 2709 字节。重算长度/基地址/编码位；单字段>9999字节或记录>99999字节报错。"""
    if encoding.lower() in ("utf-8", "utf8"):
        enc = "utf-8"
    elif encoding.lower() in ("gbk", "gb18030", "gb2312"):
        enc = "gb18030"
    else:
        raise MarcWriteError(f"不支持的编码：{encoding}")

    directory = bytearray()
    data = bytearray()
    # ⚠️ **ISO 2709 / CNMARC 要求目次区按「字段号升序」**（``）。
    # `rec.fields` 的**存储顺序不保证升序**：905 由馆藏表重建（历史上被 append 到末尾）、
    # 导入的字段按文件里出现的顺序、套录本地化还会 insert 001。所以**在这里兜底排序** ——
    # 这是导出件（`.iso` 与 `marc_raw`）合规的**唯一**保证点。
    # 用**稳定**排序：同 tag 的多个字段（606 / 905 多册）保持各自原有的相对顺序。
    for f in sorted(rec.fields, key=lambda x: x.tag):
        if f.is_control():
            raw = f.data.encode(enc)
        else:
            chunk = bytearray()
            chunk += (f.ind1 or " ")[:1].encode(enc)
            chunk += (f.ind2 or " ")[:1].encode(enc)
            if f.data:
                chunk += f.data.encode(enc)
            for s in f.subfields:
                chunk.append(SF)
                chunk += (s.code or " ")[:1].encode(enc)
                chunk += s.value.encode(enc)
            raw = bytes(chunk)
        flen = len(raw) + 1
        if flen > 9999:
            raise MarcWriteError(f"字段 {f.tag} 长度 {flen} 字节超出 9999 上限")
        directory += f"{f.tag}{flen:04d}{len(data):05d}".encode("ascii")
        data += raw
        data.append(FS)

    base_addr = 24 + len(directory) + 1
    total = base_addr + len(data) + 1
    if total > 99999:
        raise MarcWriteError(f"记录长度 {total} 字节超出 99999 上限")

    ldr = list((rec.leader or "").ljust(24, " ")[:24] if rec.leader.strip() else list(_DEFAULT_LEADER))
    if len(ldr) < 24:
        ldr = list(_DEFAULT_LEADER)
    ldr[0:5] = f"{total:05d}"
    ldr[5] = ldr[5] if ldr[5].strip() else "n"
    ldr[6] = ldr[6] if ldr[6].strip() else "a"
    ldr[7] = ldr[7] if ldr[7].strip() else "m"
    ldr[9] = "a" if enc == "utf-8" else " "
    ldr[10], ldr[11] = "2", "2"
    ldr[12:17] = f"{base_addr:05d}"
    ldr[17] = ldr[17] if ldr[17].strip() else " "
    ldr[20:24] = "4500"

    return "".join(ldr).encode("ascii") + bytes(directory) + bytes([FS]) + bytes(data) + bytes([RS])
