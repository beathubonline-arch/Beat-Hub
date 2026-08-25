"""Repair merchandise order split columns on existing production databases.

Revision ID: merch_split_011
Revises: merch_schema_010

Migration 0010 owns the merchandise schema, but some production databases
were already stamped at that revision before the commission/net columns were
introduced. This forward-only repair migration makes the live database match
the current application without touching or deleting merchandise orders.
"""

from alembic import op
import sqlalchemy as sa

revision = "merch_split_011"
down_revision = "merch_schema_010"
branch_labels = None
depends_on = None

TABLE = "beathub_merchandise_orders"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if TABLE not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns(TABLE)}

    additions = {
        "commission_amount": sa.Numeric(12, 2),
        "net_amount": sa.Numeric(12, 2),
        "commission_percent_at_purchase": sa.Numeric(5, 2),
        "payment_provider": sa.String(length=32),
    }

    for name, column_type in additions.items():
        if name not in columns:
            op.add_column(TABLE, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    # Forward-only production compatibility repair. Never remove accounting
    # columns or alter existing merchandise orders during a downgrade.
    pass
