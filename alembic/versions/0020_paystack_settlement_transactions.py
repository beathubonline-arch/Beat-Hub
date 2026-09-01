"""Add transaction-level Paystack settlement evidence.

Revision ID: pay_settlement_transactions_020
Revises: pay_settlement_019
"""

from alembic import op
import sqlalchemy as sa

revision = "pay_settlement_transactions_020"
down_revision = "pay_settlement_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "paystack_settlement_transactions" in tables:
        return

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
        sa.ForeignKeyConstraint(["settlement_id"], ["paystack_settlements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_transaction_id"], ["payment_transactions.id"], ondelete="SET NULL"),
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
    # Financial evidence is intentionally forward-only.
    pass
