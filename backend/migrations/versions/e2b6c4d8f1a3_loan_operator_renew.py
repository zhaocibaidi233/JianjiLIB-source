"""loans.operator_renew：续借经办人

## 为什么

借出与归还有 `operator_loan` / `operator_return`，续借只更新 `last_renewed_at`。
台账上「谁把这本续了 15 天」查不到，读者来问「谁给我续的」也答不上。

只留**最近一次**续借的人，与 `last_renewed_at` 同一口径；不做续借流水表——
续借次数上限通常是 1~2，为一个低频动作开一张表不划算。

## 代价

加一列，空值表示历史数据（升级前发生的续借没有署名，不做推测性回填）。
"""
from alembic import op

revision = "e2b6c4d8f1a3"
down_revision = "d7f3a2c9e5b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 幂等：列已存在就跳过。
    #
    # 为什么需要：`op.add_column` 在列已存在时抛 DuplicateColumn，而这在两种真实
    # 场景里会撞上——① 迁移中途失败后重跑（前一次其实已经加上了）；② 有人用
    # `create_all()` 先建了表结构再补跑迁移（本机验证时就这么撞的）。
    # 一条能安全重跑的迁移，比一条"必须刚好一次成功"的迁移有用得多。
    op.execute(
        "ALTER TABLE loans ADD COLUMN IF NOT EXISTS operator_renew VARCHAR(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE loans DROP COLUMN IF EXISTS operator_renew")
