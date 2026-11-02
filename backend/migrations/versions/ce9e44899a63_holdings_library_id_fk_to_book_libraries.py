"""holdings.library_id FK to book_libraries

Revision ID: ce9e44899a63
Revises: 63643b01b390
Create Date: 2026-10-06 15:41:06.717141

首个基线后增量迁移（Alembic 工作流的示范）：models 里 library_id 从裸 Integer
改为 ForeignKey("book_libraries.id")。

孤儿处理：application 层有守卫（有记录的库不允许删、记录删除级联删馆藏），
正常情况下 library_id 指向不存在库的行只能来自手工 SQL。迁移把它们**归入
最小 id 的图书库**（与旧 `migrate_holdings_library_id.py` 的兜底回填同思路），
而不是删数据。没有图书库时约束加不上，直接报错让人先建库。

"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'ce9e44899a63'
down_revision: str | None = '63643b01b390'
branch_labels: str | None = None
depends_on: str | None = None

# 显式命名（autogenerate 对无名约束无法生成可逆的 drop 指令），models 侧
# SQLAlchemy 会按 (library_id)(id) 自动建同名 FK —— create_all 与本迁移的
# 产物保持一致（基线对比靠这个）。
FK_NAME = 'fk_holdings_library_id_book_libraries'


def upgrade() -> None:
    conn = op.get_bind()
    orphan = conn.execute(text(
        "SELECT count(*) FROM holdings h "
        "LEFT JOIN book_libraries l ON l.id = h.library_id "
        "WHERE h.library_id IS NOT NULL AND l.id IS NULL")).scalar() or 0
    if orphan:
        if conn.execute(text("SELECT count(*) FROM book_libraries")).scalar() == 0:
            raise RuntimeError(
                f"检测到 {orphan} 条孤儿馆藏但 book_libraries 为空——"
                "请先建一个图书库再执行本迁移")
        conn.execute(text(
            "UPDATE holdings SET library_id = (SELECT min(id) FROM book_libraries) "
            "WHERE library_id NOT IN (SELECT id FROM book_libraries)"))
        print(f"[migration] {orphan} 条孤儿馆藏已归入最小 id 的图书库")
    op.create_foreign_key(FK_NAME, 'holdings', 'book_libraries',
                          ['library_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(FK_NAME, 'holdings', type_='foreignkey')
