"""荐购/受赠登记表（教材〔2021〕2号；契约 D17）

Recommendation 只做展示位；荐购/捐赠的来源、审核与“初选—审议—确认—备案”
流程单独成表，避免把登记误当采购或入藏。
"""
import sqlalchemy as sa
from alembic import op

revision = "a1b3c5d7e9f1"
down_revision = "f5a7c9e1d3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acquisition_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library_id", sa.Integer(),
                  sa.ForeignKey("book_libraries.id",
                                name="fk_acquisition_requests_library_id_book_libraries"),
                  nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("author", sa.String(128)),
        sa.Column("isbn", sa.String(32)),
        sa.Column("publisher", sa.String(128)),
        sa.Column("source_type", sa.String(16), nullable=False,
                  server_default="READER"),
        sa.Column("source_name", sa.String(128)),
        sa.Column("contact", sa.String(64)),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(512)),
        sa.Column("stage", sa.String(16), nullable=False,
                  server_default="PROPOSED"),
        sa.Column("source_checked", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("source_checked_at", sa.DateTime(timezone=True)),
        sa.Column("source_checked_by", sa.String(64)),
        sa.Column("source_check_note", sa.String(256)),
        sa.Column("review_note", sa.String(512)),
        sa.Column("reviewed_by", sa.String(64)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("approval_ref", sa.String(128)),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_acquisition_requests_library_id", "acquisition_requests",
                    ["library_id"])
    op.create_index("ix_acquisition_requests_stage", "acquisition_requests",
                    ["stage"])
    op.create_index("ix_acquisition_requests_library_stage",
                    "acquisition_requests", ["library_id", "stage"])


def downgrade() -> None:
    op.drop_table("acquisition_requests")
