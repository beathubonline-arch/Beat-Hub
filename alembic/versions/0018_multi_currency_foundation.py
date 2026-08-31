"""Add explicit currency snapshots to BeatHub financial records.

Revision ID: multi_currency_018
Revises: email_verification_017

This migration is deliberately additive and backward-compatible. Existing
BeatHub monetary records are KES, so every pre-existing row is assigned KES.
No historical amount is converted or changed.
"""

from alembic import op
import sqlalchemy as sa

revision = "multi_currency_018"
down_revision = "email_verification_017"
branch_labels = None
depends_on = None

TABLE_COLUMNS = {
    "tracks": ("currency", "idx_tracks_currency"),
    "orders": ("currency", "idx_orders_currency"),
    "payment_transactions": ("currency", "idx_payment_transactions_currency"),
    "creator_ledger_entries": ("currency", "idx_creator_ledger_currency"),
    "platform_ledger_entries": ("currency", "idx_platform_ledger_currency"),
    "withdrawal_requests": ("currency", "idx_withdrawal_requests_currency"),
    "admin_withdrawals": ("currency", "idx_admin_withdrawals_currency"),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, (column_name, index_name) in TABLE_COLUMNS.items():
        if table not in tables:
            # Some test/fresh-database fixtures may not own every optional
            # financial table. Do not make the migration fail unnecessarily.
            continue

        columns = {column["name"] for column in inspector.get_columns(table)}
        if column_name not in columns:
            op.add_column(
                table,
                sa.Column(
                    column_name,
                    sa.String(length=3),
                    nullable=False,
                    server_default=sa.text("'KES'"),
                ),
            )

        # Existing deployments may have received the column from a baseline
        # before this migration. Normalize any null legacy values safely.
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column_name} = 'KES' "
                f"WHERE {column_name} IS NULL"
            )
        )
        op.alter_column(table, column_name, nullable=False, existing_type=sa.String(length=3))

        existing_indexes = {index["name"] for index in inspector.get_indexes(table)}
        if index_name not in existing_indexes:
            op.create_index(index_name, table, [column_name], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, (column_name, index_name) in TABLE_COLUMNS.items():
        if table not in tables:
            continue
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        if index_name in indexes:
            op.drop_index(index_name, table_name=table)
        columns = {column["name"] for column in inspector.get_columns(table)}
        if column_name in columns:
            op.drop_column(table, column_name)
