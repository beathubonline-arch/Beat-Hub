"""Reconcile payment callback processing state with production schema.

Revision ID: pay_callback_006
Revises: pay_schema_005

Production PostgreSQL contains payment_transactions.callback_processed as a
NOT NULL column. The application model previously omitted that column, so new
PaymentTransaction INSERTs supplied no value and PostgreSQL rejected them.
This migration makes the existing column safe for new rows and creates it when
needed on databases that do not yet have it.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_callback_006"
down_revision = "pay_schema_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "payment_transactions" not in tables:
        raise RuntimeError(
            "payment_transactions table is missing; refusing to complete "
            "payment callback schema migration."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("payment_transactions")
    }

    if "callback_processed" not in columns:
        op.add_column(
            "payment_transactions",
            sa.Column(
                "callback_processed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    else:
        # Existing production rows may contain NULL because the column was
        # introduced independently of the current application model.
        op.execute(
            sa.text(
                "UPDATE payment_transactions "
                "SET callback_processed = false "
                "WHERE callback_processed IS NULL"
            )
        )
        op.alter_column(
            "payment_transactions",
            "callback_processed",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        )

    # Final verification: do not stamp this revision unless the required
    # production column is actually present and non-nullable.
    final_columns = {
        column["name"]: column
        for column in sa.inspect(bind).get_columns("payment_transactions")
    }
    callback_column = final_columns.get("callback_processed")
    if callback_column is None:
        raise RuntimeError(
            "Payment callback schema reconciliation failed: "
            "callback_processed is still missing."
        )
    if callback_column.get("nullable") is not False:
        raise RuntimeError(
            "Payment callback schema reconciliation failed: "
            "callback_processed must be NOT NULL."
        )


def downgrade() -> None:
    # Never remove callback state from production payment history.
    pass
