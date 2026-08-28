"""BeatHub forward-only schema consistency guard.

Revision ID: schema_consistency_015
Revises: pay_runtime_008

This migration does not rewrite historical revisions and does not drop data.
It verifies the production-critical payment schema and normalizes the small
set of values that must match the current application contract.
"""

from alembic import op
import sqlalchemy as sa

revision = "schema_consistency_015"
down_revision = "pay_runtime_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "payment_transactions" not in tables:
        raise RuntimeError(
            "Schema guard refused to continue: payment_transactions is missing."
        )

    columns = {
        c["name"]: c
        for c in inspector.get_columns("payment_transactions")
    }
    required = {
        "status",
        "result_description",
        "completed_at",
        "callback_processed",
    }
    missing = required - set(columns)
    if missing:
        raise RuntimeError(
            "Schema guard refused to continue: payment_transactions is missing "
            + ", ".join(sorted(missing))
        )

    # Keep payment state exactly aligned with the application's lowercase
    # PaymentStatus contract. Do not touch payment amounts or history.
    op.execute(
        sa.text(
            "UPDATE payment_transactions "
            "SET status = lower(status::text) "
            "WHERE status IS NOT NULL"
        )
    )

    invalid = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM payment_transactions "
            "WHERE status NOT IN ('pending', 'completed', 'failed')"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError(
            f"Schema guard found {invalid} payment transaction(s) with an invalid status."
        )

    status = {
        c["name"]: c
        for c in sa.inspect(bind).get_columns("payment_transactions")
    }["status"]
    status_type = str(status.get("type", "")).lower()
    if "character varying" not in status_type and "varchar" not in status_type:
        raise RuntimeError(
            "Schema guard refused to continue: payment_transactions.status "
            "must be VARCHAR-backed."
        )
    if status.get("nullable") is not False:
        op.alter_column(
            "payment_transactions",
            "status",
            existing_type=sa.String(length=30),
            nullable=False,
        )

    # callback_processed is deliberately NOT used to determine whether money
    # is owed. It is only a processing marker; the unique transaction/order
    # constraints and application transaction logic remain authoritative.
    callback = {
        c["name"]: c
        for c in sa.inspect(bind).get_columns("payment_transactions")
    }["callback_processed"]
    if callback.get("nullable") is not False:
        op.alter_column(
            "payment_transactions",
            "callback_processed",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        )


def downgrade() -> None:
    # Forward-only production guard. Historical financial data and schema
    # repairs must never be automatically rolled back.
    pass
