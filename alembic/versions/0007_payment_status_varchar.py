"""Normalize production payment status storage.

Revision ID: pay_status_007
Revises: pay_callback_006

Older production databases contain payment_transactions.status as a native
PostgreSQL enum. The current application uses a VARCHAR-backed SQLAlchemy enum
and persists the lowercase values: pending, completed, failed.

This migration converts the existing column to VARCHAR(30), normalizes any
existing enum labels to lowercase, and verifies the final schema before the
revision is stamped.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_status_007"
down_revision = "pay_callback_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "payment_transactions" not in inspector.get_table_names():
        raise RuntimeError(
            "payment_transactions table is missing; refusing to normalize payment status."
        )

    status_column = next(
        (c for c in inspector.get_columns("payment_transactions") if c["name"] == "status"),
        None,
    )
    if status_column is None:
        raise RuntimeError(
            "payment_transactions.status is missing; refusing to normalize payment status."
        )

    # PostgreSQL enum -> VARCHAR. The USING expression safely converts both
    # native-enum labels and existing string values to lowercase text.
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
    )

    final_status = next(
        (
            c
            for c in sa.inspect(bind).get_columns("payment_transactions")
            if c["name"] == "status"
        ),
        None,
    )
    if final_status is None:
        raise RuntimeError("Payment status verification failed: status column is missing.")
    if final_status.get("nullable") is not False:
        raise RuntimeError("Payment status verification failed: status must be NOT NULL.")


def downgrade() -> None:
    # Do not recreate the incompatible native enum automatically in production.
    pass
