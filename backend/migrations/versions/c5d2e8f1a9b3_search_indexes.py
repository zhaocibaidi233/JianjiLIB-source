"""检索索引：records 的 author/isbn trigram + 列表排序，holdings 的条码 trigram

## 为什么

编目列表的单一搜索框 `q` 过去拼成

    title ILIKE %q% OR author ILIKE %q% OR isbn LIKE %q% OR id IN (条码子查询)

`IN (子查询)` 是 OR 里的子计划，PG 无法把整个 OR 组成 BitmapOr —— 条件退化为对
`records` 顺表扫描，基线迁移建的 `ix_records_title_trgm` 形同虚设；`author_norm`
`isbn_norm`、`holdings.barcode` 则连 trigram 索引都没有。查询侧已改成"每个字段
各自走索引的 UNION 子查询"（`api/records.py::_q_match_ids`），本迁移补齐索引：

| 索引 | 服务对象 |
|---|---|
| `ix_records_author_trgm` (GIN) | `q` / `author=` 的作者模糊检索 |
| `ix_records_isbn_trgm` (GIN) | `q` 的 ISBN 带连字符模糊检索（`%...%`） |
| `ix_records_library_id_id` (btree) | 列表默认顺序：按图书库过滤 + `id DESC` 翻页 |
| `ix_holdings_barcode_trgm` (GIN) | `q` / `barcode=` 的条码片段检索（`%...%`） |
| `ix_holdings_record_barcode` (btree) | 按条码排序的 `min(barcode)` 子查询、每条记录的条码列表 |

`ix_records_title_trgm` 由基线迁移建过，本迁移不重复创建（缺了说明基线没跑全）。

## 与模型的一致性

同名的 `Index` 声明在 `app/models/catalog.py`（`RecordORM.__table_args__` 与文件末尾
的 holdings 段）——`db.create_all()` 与迁移产物一致，`alembic check` 不该再报漂移。

## 执行代价

普通 `CREATE INDEX`（Alembic 在事务里跑，不能用 CONCURRENTLY）：建索引期间
`records` / `holdings` 的写入会短暂阻塞。13k 量级实测秒级；更大的库请在低峰执行。

## downgrade

只 drop 这 5 个索引，**不 DROP EXTENSION pg_trgm** —— `ix_records_title_trgm`
`ix_rr_*_trgm` 都依赖它，卸扩展会连带毁掉那些索引（基线 downgrade 也刻意保留）。
"""
from __future__ import annotations

from alembic import op

revision = "c5d2e8f1a9b3"
down_revision = "a8c5e3d1b942"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # trigram 扩展是下面 GIN 索引的前置；全新库首次部署时基线迁移已建，这里兜底。
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ⚠️ `CREATE INDEX CONCURRENTLY` **不能在事务里跑**（PG 报 25001），而 Alembic
    # 默认把整个迁移包在一个事务里 —— 所以必须用 `autocommit_block()` 临时退出。
    #
    # 为什么值得：普通 CREATE INDEX 建索引期间会阻塞 records/holdings 的全部写入，
    # 学校白天做升级就是「借还台点了没反应」。并发建的代价是**失败不能回滚**：
    # 中断会留下一个 INVALID 索引，需要 DROP 后重跑 —— 所以每条都带
    # `IF NOT EXISTS`，重跑是安全的；迁移尾部再查一次 `indisvalid`，有问题就让它
    # 在 upgrade 阶段炸出来（而不是等检索变慢才发现索引是坏的）。
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_records_author_trgm "
                   "ON records USING gin (author_norm gin_trgm_ops)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_records_isbn_trgm "
                   "ON records USING gin (isbn_norm gin_trgm_ops)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_records_library_id_id "
                   "ON records (library_id, id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_holdings_barcode_trgm "
                   "ON holdings USING gin (barcode gin_trgm_ops)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_holdings_record_barcode "
                   "ON holdings (record_id, barcode)")

    #两个维护性检查：索引确实有效（并发建被中断时会留下 INVALID 的残骸）。
    for name in ("ix_records_author_trgm", "ix_records_isbn_trgm",
                 "ix_records_library_id_id", "ix_holdings_barcode_trgm",
                 "ix_holdings_record_barcode"):
        op.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_index "
            f"WHERE indexrelid = '{name}'::regclass::oid AND indisvalid) "
            f"THEN RAISE EXCEPTION '索引 {name} 无效（并发建索引被中断？DROP 后重跑本迁移）'; "
            f"END IF; END $$")


def downgrade() -> None:
    # 逆序 drop；不 DROP EXTENSION（见文件头说明）。
    # DROP INDEX CONCURRENTLY 同样不能进事务，且带 IF EXISTS（重跑安全）。
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_holdings_record_barcode")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_holdings_barcode_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_records_library_id_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_records_isbn_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_records_author_trgm")
