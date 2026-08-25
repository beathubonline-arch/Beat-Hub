"""Repair merchandise accounting columns on already-versioned databases.

Revision ID: merch_repair_012
Revises: merch_split_011

Some production databases were stamped through the merchandise repair
migration while the four accounting columns were still absent. This
forward-only migration reconciles the live table with the application without
modifying or deleting existing merchandise orders.
"""

from alembic import op
import sqlalchemy as sa

revision = "merch_repair_012"
down_revision = "merch_split_011"
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

    additions = (
        ("commission_amount", sa.Numeric(12, 2)),
        ("net_amount", sa.Numeric(12, 2)),
        ("commission_percent_at_purchase", sa.Numeric(5, 2)),
        ("payment_provider", sa.String(length=32)),
    )

    for name, column_type in additions:
        if name not in columns:
            op.add_column(TABLE, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    # Production accounting schema is forward-only. Never remove these
    # columns during a downgrade because existing orders may depend on them.
    pass
