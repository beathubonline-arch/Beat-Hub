"""Add missing payment transaction result description column.

Revision ID: pay_result_desc_002
Revises: fix_withdrawal_status_001
Create Date: 2026-08-24

The revision ID is intentionally <= 32 characters because the production
PostgreSQL alembic_version.version_num column is varchar(32).
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_result_desc_002"
down_revision = "fix_withdrawal_status_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safe for deployments where the column may already have been created.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("payment_transactions")}

    if "result_description" not in columns:
        op.add_column(
            "payment_transactions",
            sa.Column("result_description", sa.String(length=500), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("payment_transactions")}

    if "result_description" in columns:
        op.drop_column("payment_transactions", "result_description")
