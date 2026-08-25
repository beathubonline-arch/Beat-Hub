"""Final defensive payment schema compatibility migration.

Revision ID: pay_result_desc_003
Revises: pay_result_desc_002

Some production databases may already be stamped at pay_result_desc_002 while
still missing one of the columns required by the current PaymentTransaction
model. This migration makes the schema converge safely without removing any
existing production data.
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

    if "completed_at" not in columns:
        op.add_column(
            "payment_transactions",
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    # Keep production payment-result columns intact on downgrade.
    pass
