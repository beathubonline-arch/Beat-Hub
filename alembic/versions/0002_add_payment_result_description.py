"""Add payment transaction callback/result columns.

Revision ID: pay_result_desc_002
Revises: fix_withdrawal_status_001

Keep the revision identifier at <= 32 characters because production
PostgreSQL databases may use varchar(32) for alembic_version.version_num.
This migration is deliberately defensive because some production databases
may already contain one or both columns.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_result_desc_002"
down_revision = "fix_withdrawal_status_001"
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

    if "completed_at" not in columns:
        op.add_column(
            "payment_transactions",
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("payment_transactions")}

    if "completed_at" in columns:
        op.drop_column("payment_transactions", "completed_at")

    if "result_description" in columns:
        op.drop_column("payment_transactions", "result_description")
