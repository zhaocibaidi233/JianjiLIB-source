"""records.extra_905：905 扩展子字段的持久载体

## 为什么

`905$Z`（著者号）与 `905$S`（四角号）是别人家编目系统塞进 905 的约定子字段，
我方不产生但必须原样保留（`services.batch.EXTRA_905_CODES`）。它们原先只挂在
「重建后的第一条 905」上：

- 全部册注销/遗失后 905 为空 → extras 无处挂载，**永久丢失**；
- 恢复馆藏后也回不来（记录里已无载体）。

存到 `records.extra_905` 后，重建与「当前有没有在架册」解耦。

## 数据形态

`[["Z", "L824"], ["S", "3421"]]` —— 与 `_extra_905_subfields()` 的保序去重
列表一致，JSONB 数组。NULL 表示从未登记过。

## 存量数据

本次迁移**回填**已有记录的 extras（从 `fields` 里的 905 抽取）。迁移只跑一次，
之后由 `_rebuild_905` 读写这一列保持一致。
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a8c5e3d1b942"
down_revision = "f4a9c2e8b713"
branch_labels = None
depends_on = None

EXTRA_CODES = ("Z", "S")


def upgrade() -> None:
    op.add_column("records",
                  sa.Column("extra_905", postgresql.JSONB(astext_type=sa.Text()),
                            nullable=True))
    # 回填：把现有 fields 里 905 的 $Z/$S 抽出来存列（保序去重）。
    # 用 Python 侧处理而不是 SQL：JSONB 里做"保序去重"的 SQL 可读性太差，
    # 而 records 的量级（十万以内）一次性 UPDATE 可以接受。
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, fields FROM records "
        "WHERE fields::text LIKE '%\"905\"%' AND fields::text LIKE '%\"Z\"%' "
        "   OR fields::text LIKE '%\"905\"%' AND fields::text LIKE '%\"S\"%'"
    )).fetchall()
    updates = []
    for rid, fields in rows:
        if isinstance(fields, str):
            fields = json.loads(fields)
        found: list[list[str]] = []
        for f in fields or []:
            if f.get("tag") != "905":
                continue
            for code, value in (f.get("sf") or []):
                if code in EXTRA_CODES:
                    v = (value or "").strip()
                    if v and [code, v] not in found:
                        found.append([code, v])
        if found:
            updates.append({"rid": rid, "extra": json.dumps(found, ensure_ascii=False)})
    for u in updates:
        conn.execute(
            sa.text("UPDATE records SET extra_905 = CAST(:extra AS jsonb) WHERE id = :rid"),
            u)


def downgrade() -> None:
    op.drop_column("records", "extra_905")
