"""Add product, order and payment currency fields.

Revision ID: product_currency_018
Revises: email_verification_017
"""

from alembic import op
import sqlalchemy as sa

revision = "product_currency_018"
down_revision = "email_verification_017"
branch_labels = None
depends_on = None


def _add_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if column.name not in {c["name"] for c in inspector.get_columns(table)}:
        op.add_column(table, column)


def upgrade() -> None:
    _add_if_missing("tracks", sa.Column("currency", sa.String(length=3), nullable=False, server_default="KES"))
    _add_if_missing("orders", sa.Column("currency", sa.String(length=3), nullable=False, server_default="KES"))
    _add_if_missing("payment_transactions", sa.Column("currency", sa.String(length=3), nullable=False, server_default="KES"))

    # Remove the temporary defaults after existing rows have been backfilled.
    op.alter_column("tracks", "currency", server_default=None)
    op.alter_column("orders", "currency", server_default=None)
    op.alter_column("payment_transactions", "currency", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("payment_transactions", "orders", "tracks"):
        if "currency" in {c["name"] for c in inspector.get_columns(table)}:
            op.drop_column(table, "currency")
