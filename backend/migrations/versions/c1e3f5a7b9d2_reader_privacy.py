"""Phase 5：读者监护人同意与匿名化标记

契约 C-19 / D15：不满 14 周岁读者信息属敏感个人信息；毕业/到期应可删除或匿名化。
不删 Reader 行（借阅历史/统计还要用），只清姓名/电话/班级并标记 anonymized_at。
"""
from alembic import op

revision = "c1e3f5a7b9d2"
down_revision = "b8d0f2e4c6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for ddl in (
        "ALTER TABLE readers ADD COLUMN IF NOT EXISTS guardian_consent_at "
        "TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE readers ADD COLUMN IF NOT EXISTS guardian_consent_by VARCHAR(64)",
        "ALTER TABLE readers ADD COLUMN IF NOT EXISTS anonymized_at "
        "TIMESTAMP WITH TIME ZONE",
    ):
        op.execute(ddl)


def downgrade() -> None:
    for column in ("anonymized_at", "guardian_consent_by", "guardian_consent_at"):
        op.execute(f"ALTER TABLE readers DROP COLUMN IF EXISTS {column}")
