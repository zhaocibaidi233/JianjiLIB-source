from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary,
    Numeric, SmallInteger, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

class BookLibrary(Base):
    """图书库（每校/每馆一个，编目成品库的分区）。"""
    __tablename__ = "book_libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecordORM(Base):
    """图书库书目记录（编目成品，按图书库分区）。"""
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("book_libraries.id"), index=True)
    marc_raw: Mapped[bytes] = mapped_column(LargeBinary)          # ISO 2709 原始字节（GBK）
    leader: Mapped[str] = mapped_column(String(24), default=" " * 24)
    fields: Mapped[list] = mapped_column(JSONB)               # 解析后的字段数组
    source: Mapped[str] = mapped_column(String(64), default="local")

    # 检索冗余列（写入时生成）
    isbn_norm: Mapped[str | None] = mapped_column(String(16), index=True)
    title_norm: Mapped[str | None] = mapped_column(Text)
    title_pinyin: Mapped[str | None] = mapped_column(Text)
    title_pinyin_initials: Mapped[str | None] = mapped_column(Text)
    author_norm: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    class_no: Mapped[str | None] = mapped_column(String(32), index=True)

    # 定价冗余列—— `010$d` 的解析结果，**金额与币种成对存**（见 core/pricing.py）。
    # 为什么要冗余：改造前统计页与财产登记表要把**全库** fields（JSONB）拉进内存逐条正则
    # 解析，十万级就是每次都搬几十 MB 再全表重算；现在改一句 SQL 聚合。
    # **真源仍是 fields 里的 010$d**，这两列只是缓存副本 —— 一致性由文件末尾的
    # before_insert / before_update 钩子保证，不靠"记得在写入点写上"。
    # 币种必须一起存：本馆默认币种可在字典中心改，只存金额的话改一次币种全部缓存值就错了。
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(String(3))

    # 编目员署名（轻量，见 core/operator.py）——「按编目员工作量统计」的依据
    created_by: Mapped[str | None] = mapped_column(String(64), index=True)

    # 卷册匹配键（Phase 2）：由 200$h/$i 规范化的稳定键，用于转库/同种合并；
    # 不是展示字段，也不从 Item.volume_label 反推。
    volume_key: Mapped[str | None] = mapped_column(String(64))
    # 卷册序（仅排序/统计用；为空回退按卷册文本序）。P1-D 起消费，本轮无 UI。
    volume_seq: Mapped[int | None] = mapped_column(Integer())

    # --- 文献类型 / 读者对象（2026-10-09 加，照真实书商数据补；见）------
    # 文献类型：取自字典中心「文献类型」（`settings.dictionaries` 的 `item_types`），
    # 如「中文图书」。书商把等价值写在 `909$e`（实测那份数据**整库恒为「中文图书」**）。
    # ⚠️ 与 `holdings.circulation_type` **不是一回事**：那个是**册级流通类型**，
    # 且全仓从未写过/读过（休眠列），别拿来存文献类型。
    item_type: Mapped[str | None] = mapped_column(String(32))
    # 读者对象：**CNMARC `100$a` 第 17-19 位**的三字符代码（如 `em `）。
    # ⚠️ 加它之前 `ai/fields.py::fill_100()` 把这个位置**写死成 `em `** —— 那是编造的；
    # 现在改为用这里的值（为空才回退默认）。书商的等价值写在 `982$a` / `985$x`（「学生用书」）。
    reader_level: Mapped[str | None] = mapped_column(String(8))

    # --- 905 扩展子字段的**持久载体**--------------
    # `[["Z", "L824"], ["S", "3421"]]`：别人家编目系统塞在 905 里、我方不产生但
    # 必须原样保留的码（见 services.record_projection.EXTRA_905_CODES）。
    #
    # 为什么必须单独存一列：它们原先只挂在「重建后的第一条 905」上，**全部册注销/
    # 遗失后 905 为空，extras 无处挂载就永久丢了**（恢复馆藏也回不来，因为记录里
    # 已无载体）。存在列上之后，重建与"当前有没有在架册"无关。
    marc_extras: Mapped[list | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # ⚠️ `onupdate` 用 `clock_timestamp()` 而不是 `now()`：PG 的 `now()` 是**事务开始
    # 时间**，同一事务里连续更新两次时戳不变——乐观锁（records.py 的
    # expected_updated_at 比对）就会漏掉这些更新。`clock_timestamp()` 取真实时钟，
    # 每次 UPDATE 都前进。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.clock_timestamp())

    __table_args__ = (
        Index("ix_records_title_trgm", "title_norm",
              postgresql_using="gin", postgresql_ops={"title_norm": "gin_trgm_ops"}),
        # 单一搜索框 `q` 的另外两条 OR 腿（author / isbn，见 records._q_match_ids）。
        # 没有 trigram 时 `%q%` 只能顺表扫——一条腿退化成全表扫描，整条 OR 就都白搭，
        # 连 title 那条 trigram 也用不上。
        Index("ix_records_author_trgm", "author_norm",
              postgresql_using="gin", postgresql_ops={"author_norm": "gin_trgm_ops"}),
        Index("ix_records_isbn_trgm", "isbn_norm",
              postgresql_using="gin", postgresql_ops={"isbn_norm": "gin_trgm_ops"}),
        # 列表默认顺序：按图书库过滤 + `id DESC` 翻页。单列 library_id 索引命中后
        # 仍要把整批取出再排序回表；复合后能顺着索引取前 N 条。
        Index("ix_records_library_id_id", "library_id", "id"),
        # 转库目标匹配：同馆、同 ISBN、同卷册键。没有这条索引时，ISBN 命中后
        # 还要把同 ISBN 的所有记录拉回来逐条比 volume_key。
        Index("ix_records_library_isbn_volume", "library_id", "isbn_norm", "volume_key"),
    )


class ResourceLibrary(Base):
    """套录资源库（独立 res schema，双库模式）。

    含书商导入批次与"联机套录下载"自动缓存库。
    """
    __tablename__ = "resource_libraries"
    __table_args__ = ({"schema": "res"},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    origin: Mapped[str | None] = mapped_column(String(256))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceRecordORM(Base):
    """资源库记录（原料库，不参与编目工作流；联机下载自动回存更新）。"""
    __tablename__ = "resource_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("res.resource_libraries.id", ondelete="CASCADE"), index=True)
    marc_raw: Mapped[bytes] = mapped_column(LargeBinary)
    leader: Mapped[str] = mapped_column(String(24))
    fields: Mapped[list] = mapped_column(JSONB)

    isbn_norm: Mapped[str | None] = mapped_column(String(16), index=True)
    title_norm: Mapped[str | None] = mapped_column(Text)
    title_pinyin: Mapped[str | None] = mapped_column(Text)
    title_pinyin_initials: Mapped[str | None] = mapped_column(Text)
    author_norm: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    class_no: Mapped[str | None] = mapped_column(String(32))
    source_tag: Mapped[str | None] = mapped_column(String(64))   # 进入来源（导入批次/联机源名）
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_rr_title_trgm", "title_norm",
              postgresql_using="gin", postgresql_ops={"title_norm": "gin_trgm_ops"}),
        # 责任者也要模糊检索（录入栏「套录」框能直接输作者名）。
        # 已存在的库要跑 `scripts/migrate_author_trgm.py --apply` 补这个索引。
        Index("ix_rr_author_trgm", "author_norm",
              postgresql_using="gin", postgresql_ops={"author_norm": "gin_trgm_ops"}),
        {"schema": "res"},
    )


class SourceORM(Base):
    """套录源配置——全参数可编辑（host/库/账号/优先级/启停），换源不改代码。"""
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    type: Mapped[str] = mapped_column(String(16))            # local / z3950
    config: Mapped[dict] = mapped_column(JSONB)               # SourceConfig 字典（含 host/port/db/账号）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fail_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallNoCounter(Base):
    """书次号计数表：分类号 -> 当前最大书次号（__barcode__ 键复用为条码流水号）。"""
    __tablename__ = "callno_counters"

    class_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    current_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CLCClass(Base):
    """《中图法》一级大类参考表（默认 22 条）。

    本产品固定只维护 22 个一级大类，用于统计分组、读者门户分类标签和一级候选；
    不提供细分树、类表维护或占位类登记。分类号本身按字符串著录，不要求存在于本表。
    `level` / `parent_code` 保留给历史数据兼容。
    """
    __tablename__ = "clc_classes"

    class_no: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    letter: Mapped[str] = mapped_column(String(2), index=True)
    level: Mapped[int] = mapped_column(Integer)
    parent_code: Mapped[str | None] = mapped_column(String(16), index=True)
    is_alt: Mapped[bool] = mapped_column(Boolean, default=False)   # 交替类目 []


class SubjectTerm(Base):
    """主题词表：从资源库 606 字段挖掘的频次词表（AI 标引候选菜单用）。"""
    __tablename__ = "subject_terms"

    term: Mapped[str] = mapped_column(String(128), primary_key=True)
    freq: Mapped[int] = mapped_column(Integer, default=1)


# --- 馆藏侧检索索引（故意声明在本文件）-----------------------------------------
# 本轮 P2 的改动范围只含本文件（`models/holdings.py` 不在范围内），但索引必须
# 进 `Base.metadata`：`db.create_all()`（全新安装 / 测试库）与 Alembic 迁移
# `*_search_indexes.py` 的产物要保持一致，否则 `alembic check` 会一直提示
# "库里多两个索引"。等 holdings.py 能改时，把这两行原样搬回 `HoldingORM.__table_args__` 即可。
from .holdings import HoldingORM as _HoldingORM  # noqa: E402  (仅用于挂索引)

# 条码片段检索是图书库的主入口之一（`records._barcode_subquery` 与 `q` 的条码腿）。
# `%...%` 用不上唯一约束 `uq_holdings_library_barcode` 的 btree，必须 trigram GIN。
Index("ix_holdings_barcode_trgm", _HoldingORM.__table__.c.barcode,
      postgresql_using="gin", postgresql_ops={"barcode": "gin_trgm_ops"})
# 按条码排序走 `min(barcode)` 相关子查询、列表每条记录再取一次条码列表：
# (record_id, barcode) 让这两处都顺着索引取，不必逐行回表排序。
Index("ix_holdings_record_barcode", _HoldingORM.__table__.c.record_id,
      _HoldingORM.__table__.c.barcode)
