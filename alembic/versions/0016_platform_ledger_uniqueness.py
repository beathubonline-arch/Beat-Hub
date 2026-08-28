"""Protect platform ledger idempotency at the database level.

Revision ID: platform_ledger_uniqueness_016
Revises: schema_consistency_015

The application already checks for duplicate platform ledger rows. This
migration adds database-enforced uniqueness so concurrent requests cannot
create duplicate commission or withdrawal entries.
"""

from alembic import op
import sqlalchemy as sa

revision = "platform_ledger_uniqueness_016"
down_revision = "schema_consistency_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Fail closed if historical data already violates the invariant. Never
    # silently delete or merge financial records during a migration.
    duplicate_withdrawals = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT admin_withdrawal_id
                FROM platform_ledger_entries
                WHERE admin_withdrawal_id IS NOT NULL
                  AND entry_type = 'platform_withdrawal'
                GROUP BY admin_withdrawal_id
                HAVING COUNT(*) > 1
            ) duplicates
            """
        )
    ).scalar_one()
    if duplicate_withdrawals:
        raise RuntimeError(
            "Cannot add platform withdrawal uniqueness: duplicate financial "
            "ledger entries already exist. Reconcile them manually first."
        )

    duplicate_commissions = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT order_id
                FROM platform_ledger_entries
                WHERE order_id IS NOT NULL
                  AND entry_type = 'platform_commission'
                GROUP BY order_id
                HAVING COUNT(*) > 1
            ) duplicates
            """
        )
    ).scalar_one()
    if duplicate_commissions:
        raise RuntimeError(
            "Cannot add platform commission uniqueness: duplicate financial "
            "ledger entries already exist. Reconcile them manually first."
        )

    op.create_index(
        "uq_platform_withdrawal_ledger_once",
        "platform_ledger_entries",
        ["admin_withdrawal_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'platform_withdrawal' AND admin_withdrawal_id IS NOT NULL"),
    )
    op.create_index(
        "uq_platform_commission_ledger_once",
        "platform_ledger_entries",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'platform_commission' AND order_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Financial idempotency protections are intentionally forward-only.
    pass
