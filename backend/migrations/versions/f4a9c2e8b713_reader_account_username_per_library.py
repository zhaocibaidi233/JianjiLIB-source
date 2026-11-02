"""reader_accounts.username 由全局唯一改为库内唯一

## 为什么

借书证号只在**图书库内**唯一（`readers` 表的 `uq_readers_library_card_no`）。
两个图书库各自从 0001 编起是正常状态，`_find_reader_by_card` 的「该借书证号在
多个图书库中存在，请先选择图书库」就是为这个场景写的。

但读者门户账号的登录名直接取借书证号，且 `reader_accounts.username` 建成了
**全局**唯一索引。后果：建第二个图书库后，同号读者开通门户必然失败，报错
「该借书证号对应的登录名已被其他账号占用」——而占用者其实在另一个库里，
馆员在本库账号列表里根本查不到，现场无法自行解决。

## 做法

把 `ix_reader_accounts_username` 从唯一索引改成普通索引（保留索引本身，
登录查询按它走）。唯一性由 `uq_reader_accounts_reader_id`（一个读者一个账号）
加上应用层的「库内唯一」校验来保证。

## 注意 downgrade

如果升级后已经出现了跨库同号账号，`downgrade` 重建唯一索引会失败（这是正确
的行为：数据确实不满足旧约束）。真要回退，需先清理重复账号。
"""
from alembic import op

revision = "f4a9c2e8b713"
down_revision = "d6b8f0a2c4e6"
branch_labels = None
depends_on = None

_INDEX = "ix_reader_accounts_username"
_TABLE = "reader_accounts"


def upgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(_INDEX, _TABLE, ["username"], unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(_INDEX, _TABLE, ["username"], unique=True)
