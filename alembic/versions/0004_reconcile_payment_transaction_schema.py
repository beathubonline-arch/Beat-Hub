"""Reconcile payment_transactions with the current PaymentTransaction model.

Revision ID: pay_schema_004
Revises: pay_result_desc_003

Production databases can be stamped at an earlier payment migration while
still missing columns added to the SQLAlchemy model. This migration is a
forward-only, defensive reconciliation: it adds only missing nullable
columns and never removes or rewrites existing payment data.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_schema_004"
down_revision = "pay_result_desc_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("payment_transactions")}

    additions = {
        "result_description": sa.Column("result_description", sa.String(length=500), nullable=True),
        "completed_at": sa.Column("completed_at", sa.DateTime(), nullable=True),
    }

    for name, column in additions.items():
        if name not in columns:
            op.add_column("payment_transactions", column)


def downgrade() -> None:
    # Payment schema reconciliation is intentionally forward-only in production.
    # Existing payment data and callback history must never be removed by a downgrade.
    pass
