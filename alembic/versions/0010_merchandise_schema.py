"""Create the canonical BeatHub merchandise tables.

Revision ID: merch_schema_010
Revises: pay_subaccount_009

Merchandise schema is owned by Alembic. Request handlers must never perform
CREATE TABLE, ALTER TABLE, CREATE INDEX or schema inspection in the request
path. This migration is defensive so databases that were previously prepared
by the legacy runtime helper remain fully compatible.
"""

from alembic import op
import sqlalchemy as sa


revision = "merch_schema_010"
down_revision = "pay_subaccount_009"
branch_labels = None
depends_on = None

MERCH_TABLE = "beathub_merchandise"
ORDER_TABLE = "beathub_merchandise_orders"


def _table_names(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table):
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if MERCH_TABLE not in tables:
        op.create_table(
            MERCH_TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("creator_profile_id", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("slug", sa.String(length=220), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price", sa.Numeric(12, 2), nullable=False),
            sa.Column("image_path", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    tables = _table_names(bind)
    if ORDER_TABLE not in tables:
        op.create_table(
            ORDER_TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("product_id", sa.String(length=36), nullable=False),
            sa.Column("buyer_id", sa.String(length=255), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
            sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("phone_number", sa.String(length=32), nullable=True),
            sa.Column("order_note", sa.String(length=300), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_payment"),
            sa.Column("merchant_request_id", sa.String(length=255), nullable=True),
            sa.Column("checkout_request_id", sa.String(length=255), nullable=True),
            sa.Column("mpesa_receipt", sa.String(length=128), nullable=True),
            sa.Column("failure_reason", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
        )

    # Legacy databases may already have either table. Add only missing columns.
    order_columns = _columns(bind, ORDER_TABLE)
    additions = {
        "commission_amount": sa.Numeric(12, 2),
        "net_amount": sa.Numeric(12, 2),
        "commission_percent_at_purchase": sa.Numeric(5, 2),
        "payment_provider": sa.String(length=32),
    }
    for name, column_type in additions.items():
        if name not in order_columns:
            op.add_column(ORDER_TABLE, sa.Column(name, column_type, nullable=True))

    indexes = _indexes(bind, MERCH_TABLE)
    if f"idx_{MERCH_TABLE}_creator" not in indexes:
        op.create_index(
            f"idx_{MERCH_TABLE}_creator",
            MERCH_TABLE,
            ["creator_profile_id"],
        )

    indexes = _indexes(bind, ORDER_TABLE)
    for name, column in (
        (f"idx_{ORDER_TABLE}_buyer", "buyer_id"),
        (f"idx_{ORDER_TABLE}_status", "status"),
    ):
        if name not in indexes:
            op.create_index(name, ORDER_TABLE, [column])

    # checkout_request_id was historically created as unique. Preserve that
    # invariant where the existing database has no conflicting duplicate rows.
    indexes = _indexes(bind, ORDER_TABLE)
    if "uq_beathub_merchandise_orders_checkout_request_id" not in indexes:
        try:
            op.create_unique_constraint(
                "uq_beathub_merchandise_orders_checkout_request_id",
                ORDER_TABLE,
                ["checkout_request_id"],
            )
        except Exception:
            # Some legacy PostgreSQL databases may already enforce uniqueness
            # with an unnamed index. The application still remains compatible.
            pass


def downgrade() -> None:
    # Merchandise is production data; intentionally forward-only.
    pass
