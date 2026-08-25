"""Final runtime guard for the production Paystack payment schema.

Revision ID: pay_runtime_008
Revises: pay_status_007

Some historical deployments were able to stamp a payment migration even when
PostgreSQL still exposed an older payment_transactions shape. This migration
is intentionally defensive: it converges the production payment schema to the
current model without deleting payment history.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_runtime_008"
down_revision = "pay_status_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "payment_transactions" not in inspector.get_table_names():
        raise RuntimeError(
            "payment_transactions table is missing; refusing to apply payment runtime guard."
        )

    columns = {
        column["name"]: column
        for column in inspector.get_columns("payment_transactions")
    }

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

    # Normalize both legacy PostgreSQL enum storage and already-VARCHAR
    # storage to the exact lowercase values used by PaymentStatus.
    status_column = next(
        (
            column
            for column in sa.inspect(bind).get_columns("payment_transactions")
            if column["name"] == "status"
        ),
        None,
    )
    if status_column is None:
        raise RuntimeError(
            "Payment runtime guard failed: payment_transactions.status is missing."
        )

    op.execute(
        sa.text(
            "ALTER TABLE payment_transactions "
            "ALTER COLUMN status DROP DEFAULT"
        )
    )

    status_type = str(status_column.get("type", "")).lower()
    if "character varying" not in status_type and "varchar" not in status_type:
        op.execute(
            sa.text(
                "ALTER TABLE payment_transactions "
                "ALTER COLUMN status TYPE VARCHAR(30) "
                "USING lower(status::text)"
            )
        )

    op.execute(
        sa.text(
            "UPDATE payment_transactions "
            "SET status = lower(status::text)"
        )
    )

    op.alter_column(
        "payment_transactions",
        "status",
        existing_type=sa.String(length=30),
        nullable=False,
        server_default=sa.text("'pending'"),
    )

    final_columns = {
        column["name"]: column
        for column in sa.inspect(bind).get_columns("payment_transactions")
    }
    required = {"status", "result_description", "completed_at", "callback_processed"}
    missing = required - set(final_columns)
    if missing:
        raise RuntimeError(
            "Payment runtime guard failed; missing columns: "
            + ", ".join(sorted(missing))
        )

    final_status = final_columns["status"]
    final_status_type = str(final_status.get("type", "")).lower()
    if "character varying" not in final_status_type and "varchar" not in final_status_type:
        raise RuntimeError(
            "Payment runtime guard failed: status is not VARCHAR-backed."
        )
    if final_status.get("nullable") is not False:
        raise RuntimeError(
            "Payment runtime guard failed: status must be NOT NULL."
        )


def downgrade() -> None:
    # Production payment schema is intentionally forward-only.
    pass
