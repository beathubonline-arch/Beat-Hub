"""Add Paystack subaccount identity for producer settlement.

Revision ID: pay_subaccount_009
Revises: pay_runtime_008

This is additive only. Existing producer profiles remain valid and no
historical payment/order data is changed.
"""

from alembic import op
import sqlalchemy as sa


revision = "pay_subaccount_009"
down_revision = "pay_runtime_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("profiles")
    }

    if "paystack_subaccount_code" not in columns:
        op.add_column(
            "profiles",
            sa.Column("paystack_subaccount_code", sa.String(length=100), nullable=True),
        )

    indexes = {
        index["name"] for index in inspector.get_indexes("profiles")
    }
    if "ix_profiles_paystack_subaccount_code" not in indexes:
        op.create_index(
            "ix_profiles_paystack_subaccount_code",
            "profiles",
            ["paystack_subaccount_code"],
            unique=True,
        )


def downgrade() -> None:
    # Forward-only in production; removing a settlement identity could make
    # an already-configured producer's future payouts ambiguous.
    pass
