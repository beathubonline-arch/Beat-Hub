"""Add the immutable BeatHub platform financial ledger.

Revision ID: platform_financial_ledger_015
Revises: music_content_types_014
"""

from alembic import op
import sqlalchemy as sa

revision = "platform_financial_ledger_015"
down_revision = "music_content_types_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "platform_ledger_entries" not in tables:
        op.create_table(
            "platform_ledger_entries",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("entry_type", sa.String(length=40), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("order_id", sa.String(length=36), nullable=True),
            sa.Column("admin_withdrawal_id", sa.String(length=36), nullable=True),
            sa.Column("provider", sa.String(length=40), nullable=True),
            sa.Column("provider_reference", sa.String(length=120), nullable=True),
            sa.Column("description", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
            sa.ForeignKeyConstraint(["admin_withdrawal_id"], ["admin_withdrawals.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_platform_ledger_entries_entry_type", "platform_ledger_entries", ["entry_type"])
        op.create_index("ix_platform_ledger_entries_order_id", "platform_ledger_entries", ["order_id"])
        op.create_index("ix_platform_ledger_entries_admin_withdrawal_id", "platform_ledger_entries", ["admin_withdrawal_id"])
        op.create_index("ix_platform_ledger_entries_provider_reference", "platform_ledger_entries", ["provider_reference"])
        op.create_index("ix_platform_ledger_entries_created_at", "platform_ledger_entries", ["created_at"])

    # Backfill completed-order commission credits.
    # IDs must fit the model's String(36) primary key.  The previous
    # implementation used 'platform_' || order_id, which is 45 characters
    # for a normal UUID order id and crashes on PostgreSQL.
    # md5() produces a deterministic 32-character identifier, giving the
    # same commission entry for the same order on retries while fitting the
    # schema and avoiding dependence on PostgreSQL UUID extensions.
    bind.execute(
        sa.text(
            """
            INSERT INTO platform_ledger_entries
                (id, entry_type, amount, order_id, provider, description, created_at)
            SELECT
                md5('platform_commission:' || o.id),
                'platform_commission',
                o.commission_amount,
                o.id,
                'paystack',
                'BeatHub commission from order ' || o.order_number,
                COALESCE(o.completed_at, o.created_at, CURRENT_TIMESTAMP)
            FROM orders o
            WHERE CAST(o.status AS TEXT) IN ('COMPLETED', 'completed')
              AND NOT EXISTS (
                  SELECT 1
                  FROM platform_ledger_entries ple
                  WHERE ple.order_id = o.id
                    AND ple.entry_type = 'platform_commission'
              )
            """
        )
    )


def downgrade() -> None:
    # Forward-only financial history. Do not silently destroy accounting data.
    pass
