"""Final production guard for PaymentTransaction schema.

Revision ID: pay_schema_005
Revises: pay_schema_004

This migration is intentionally defensive and forward-only. It verifies that
payment_transactions contains every column introduced by the current payment
model that previously caused production checkout failures. Existing payment
rows are preserved; missing nullable columns are added only when necessary.

Revision identifiers are kept well below PostgreSQL varchar(32) limits used
by existing BeatHub deployments.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_schema_005"
down_revision = "pay_schema_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = set(inspector.get_table_names())
    if "payment_transactions" not in tables:
        raise RuntimeError(
            "payment_transactions table is missing; refusing to mark the "
            "payment schema migration complete."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("payment_transactions")
    }

    required_optional_columns = {
        "result_description": sa.Column(
            "result_description", sa.String(length=500), nullable=True
        ),
        "completed_at": sa.Column(
            "completed_at", sa.DateTime(), nullable=True
        ),
    }

    for name, column in required_optional_columns.items():
        if name not in columns:
            op.add_column("payment_transactions", column)

    # Re-read the schema after changes and fail the deployment if PostgreSQL
    # still does not expose the columns required by PaymentTransaction.
    final_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("payment_transactions")
    }
    missing = set(required_optional_columns) - final_columns
    if missing:
        raise RuntimeError(
            "PaymentTransaction schema reconciliation failed; missing columns: "
            + ", ".join(sorted(missing))
        )


def downgrade() -> None:
    # Never remove production payment-history columns during a downgrade.
    pass
