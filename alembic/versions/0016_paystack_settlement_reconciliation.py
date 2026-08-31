"""Persist Paystack settlements and settlement transactions.

Revision ID: paystack_settlement_reconciliation_016
Revises: platform_financial_ledger_015
"""

from alembic import op
import sqlalchemy as sa

revision = "paystack_settlement_reconciliation_016"
down_revision = "platform_financial_ledger_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "paystack_settlements" not in tables:
        op.create_table(
            "paystack_settlements",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("provider_settlement_id", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("effective_amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("total_fees", sa.Numeric(14, 2), nullable=False),
            sa.Column("total_processed", sa.Numeric(14, 2), nullable=False),
            sa.Column("settlement_date", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_settlement_id", name="uq_paystack_settlement_provider_id"),
        )
        op.create_index("ix_paystack_settlements_provider_settlement_id", "paystack_settlements", ["provider_settlement_id"])
        op.create_index("ix_paystack_settlements_status", "paystack_settlements", ["status"])
        op.create_index("ix_paystack_settlements_currency", "paystack_settlements", ["currency"])
        op.create_index("ix_paystack_settlements_settlement_date", "paystack_settlements", ["settlement_date"])

    if "paystack_settlement_transactions" not in tables:
        op.create_table(
            "paystack_settlement_transactions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("settlement_id", sa.String(length=36), nullable=False),
            sa.Column("provider_transaction_id", sa.String(length=80), nullable=False),
            sa.Column("reference", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("payment_transaction_id", sa.String(length=36), nullable=True),
            sa.Column("reconciliation_status", sa.String(length=30), nullable=False),
            sa.Column("mismatch_reason", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["payment_transaction_id"], ["payment_transactions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["settlement_id"], ["paystack_settlements.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_transaction_id", name="uq_paystack_settlement_tx_provider_id"),
        )
        op.create_index("ix_paystack_settlement_transactions_settlement_id", "paystack_settlement_transactions", ["settlement_id"])
        op.create_index("ix_paystack_settlement_transactions_provider_transaction_id", "paystack_settlement_transactions", ["provider_transaction_id"])
        op.create_index("ix_paystack_settlement_transactions_reference", "paystack_settlement_transactions", ["reference"])
        op.create_index("ix_paystack_settlement_transactions_status", "paystack_settlement_transactions", ["status"])
        op.create_index("ix_paystack_settlement_transactions_currency", "paystack_settlement_transactions", ["currency"])
        op.create_index("ix_paystack_settlement_transactions_payment_transaction_id", "paystack_settlement_transactions", ["payment_transaction_id"])
        op.create_index("ix_paystack_settlement_transactions_reconciliation_status", "paystack_settlement_transactions", ["reconciliation_status"])


def downgrade() -> None:
    # Financial reconciliation history is intentionally forward-only.
    pass
