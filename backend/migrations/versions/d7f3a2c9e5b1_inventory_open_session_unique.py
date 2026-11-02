"""盘点：同一图书库同时只能有一个 OPEN 会话（唯一索引兜底）

## 为什么需要数据库兜底

服务层 `create_session` 先查「有没有进行中的盘点」再插，这是标准的 check-then-act：
两个窗口同时点「开始盘点」会**都通过检查**、各建一个 OPEN 会话。之后扫码落进哪个
会话全看手速，两边的盘点结果互相覆盖，而界面上看不出发生过什么。

部分唯一索引（只约束 `status = 'OPEN'` 的行）让第二个 INSERT 直接失败，服务层把
`IntegrityError` 翻译成同一句人话。历史 OPEN 会话（CLOSED 的那些）不受影响。

## 升级前请确认没有遗留

如果真实库里已经存在「同一图书库的多条 OPEN」（说明过去确实踩过），本迁移会失败并
报 `could not create unique index`。这不是脚本坏了，是数据真的有问题：先把多余的
OPEN 会话结束掉，再重跑。**不要**为了让迁移过而删索引。

## 执行代价

普通 `CREATE UNIQUE INDEX`（事务内），`inventory_sessions` 只有几百行，毫秒级。
"""
from alembic import op

revision = "d7f3a2c9e5b1"
down_revision = "c5d2e8f1a9b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_sessions_open "
        "ON inventory_sessions (library_id) WHERE status = 'OPEN'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_inventory_sessions_open")
