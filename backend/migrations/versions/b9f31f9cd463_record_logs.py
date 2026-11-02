"""record logs —— 记录级操作日志表

「谁在何时改了哪条书目记录」。与 `agent_audit_logs`分工不同：
那张记"请求"（含只读），这张记"数据真的变了"（只读不记、业务回滚也不记）。

`record_id` / `library_id` 故意**不加外键**：记录与图书库都会被人删掉，
而日志必须留下 —— 加了外键就变成"删记录时日志跟着消失"，那还叫审计吗。
`title` / `barcode` 是写入时的冗余快照，同理：记录删了，日志也要能认得出是哪本书。

Revision ID: b9f31f9cd463
Revises: e570634ec7ff
Create Date: 2026-10-08 16:10:27.857778

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9f31f9cd463'
down_revision: str | None = 'e570634ec7ff'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table('record_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('operator', sa.String(length=64), nullable=True),
    sa.Column('library_id', sa.Integer(), nullable=True),
    sa.Column('record_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('barcode', sa.String(length=64), nullable=False),
    sa.Column('copies', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=256), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    # 索引只建会被筛选的四列（动作 / 操作人 / 图书库 / 记录）。
    # 不建 created_at 索引：日志量在本项目的规模（单机上万条）下，
    # 按时间范围筛本来就跟着 id 主键走，多一个索引只是多一份写开销。
    op.create_index(op.f('ix_record_logs_action'), 'record_logs', ['action'], unique=False)
    op.create_index(op.f('ix_record_logs_library_id'), 'record_logs', ['library_id'], unique=False)
    op.create_index(op.f('ix_record_logs_operator'), 'record_logs', ['operator'], unique=False)
    op.create_index(op.f('ix_record_logs_record_id'), 'record_logs', ['record_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_record_logs_record_id'), table_name='record_logs')
    op.drop_index(op.f('ix_record_logs_operator'), table_name='record_logs')
    op.drop_index(op.f('ix_record_logs_library_id'), table_name='record_logs')
    op.drop_index(op.f('ix_record_logs_action'), table_name='record_logs')
    op.drop_table('record_logs')
