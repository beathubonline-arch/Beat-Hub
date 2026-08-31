"""Add Paystack settlement snapshots for reconciliation.

Revision ID: pay_settlement_019
Revises: product_currency_018
"""

from alembic import op
import sqlalchemy as sa

revision = "pay_settlement_019"
down_revision = "product_currency_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "paystack_settlements" in tables:
        return

    op.create_table(
        "paystack_settlements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("paystack_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("settlement_date", sa.DateTime(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paystack_id"),
    )
    op.create_index("ix_paystack_settlements_paystack_id", "paystack_settlements", ["paystack_id"], unique=True)
    op.create_index("ix_paystack_settlements_status", "paystack_settlements", ["status"], unique=False)
    op.create_index("ix_paystack_settlements_currency", "paystack_settlements", ["currency"], unique=False)
    op.create_index("ix_paystack_settlements_settlement_date", "paystack_settlements", ["settlement_date"], unique=False)


def downgrade() -> None:
    # Keep production settlement evidence by default. This migration is
    # intentionally non-destructive; a manual DBA operation is required to
    # remove reconciliation history.
    pass
