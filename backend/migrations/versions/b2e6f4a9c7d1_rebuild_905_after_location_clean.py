"""rebuild 905 after holding locations were normalized

Revision ID: b2e6f4a9c7d1
Revises: a8d4e2f6c0b1
Create Date: 2026-10-23

前三个清洗迁移直接改了 holdings.location / records.shelf_no，但没有动书目里的
905。905$a 是馆藏地点、905$c 是排架号，必须按新的馆藏表重建，否则 ISO/MARC
导出仍会把旧地点里的架位号重复带出。这里统一按现行规则重建所有记录的 905。

⚠️ 不能直接 `session.query(RecordORM).all()`：ORM 映射的是**当前**模型，包含
extra_905、price、item_type 等**这条迁移之后才加的列**。空库初始化时这些列还
不存在，SELECT 会直接报 UndefinedColumn；老库升级到这一步也同样会断。
这里用只含当时字段的轻量对象接住行数据，重建后按列写回，与 ORM 演进解耦。
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from app.services.batch import remove_holdings_sync

revision = "b2e6f4a9c7d1"
down_revision = "a8d4e2f6c0b1"
branch_labels = None
depends_on = None


class _HistoricRecord:
    """只承载这条迁移运行时 records 表里存在的字段。

    `_rebuild_905` 还会写 `extra_905`，但那一列要到 a8c5e3d1b942 才出现；
    在这里它只是普通属性，不参与 SQL 写入。
    """

    def __init__(self, record_id, leader, fields, marc_raw, shelf_no, volume, class_no):
        self.id = record_id
        self.leader = leader
        self.fields = fields
        self.marc_raw = marc_raw
        self.shelf_no = shelf_no
        self.volume = volume
        self.class_no = class_no
        self.extra_905 = None


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    rows = bind.execute(sa.text(
        "SELECT id, leader, fields, marc_raw, shelf_no, volume, class_no FROM records"
    )).fetchall()
    for record_id, leader, fields, marc_raw, shelf_no, volume, class_no in rows:
        if isinstance(fields, str):
            fields = json.loads(fields)
        orm = _HistoricRecord(record_id, leader, fields, marc_raw,
                              shelf_no, volume, class_no)
        remove_holdings_sync(session, orm)
        bind.execute(
            sa.text("""
                UPDATE records
                   SET fields = CAST(:fields AS jsonb),
                       marc_raw = :marc_raw,
                       shelf_no = :shelf_no,
                       volume = :volume
                 WHERE id = :id
            """),
            {
                "fields": json.dumps(orm.fields, ensure_ascii=False),
                "marc_raw": orm.marc_raw,
                "shelf_no": orm.shelf_no,
                "volume": orm.volume,
                "id": record_id,
            },
        )
    session.flush()
    print(f"[location-clean] 重建 {len(rows)} 条书目的 905")


def downgrade() -> None:
    # 905 已按标准布局重建；回退会重新引入旧地点的重复架位号。
    pass
