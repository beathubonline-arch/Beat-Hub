"""Ensure payment transaction result description exists.

Revision ID: pay_result_desc_003
Revises: pay_result_desc_002

This is a defensive follow-up migration for production databases that may
have been stamped at the previous payment migration while the column creation
was interrupted during an earlier deployment.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_result_desc_003"
down_revision = "pay_result_desc_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("payment_transactions")}

    if "result_description" not in columns:
        op.add_column(
            "payment_transactions",
            sa.Column("result_description", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    # Intentionally do not remove this production compatibility column.
    pass
