"""Reconcile exclusive beat sold state with completed ownership.

An exclusive track is sold only when a completed order or ownership lock exists.
Pending, cancelled, failed, or abandoned checkout must never reserve inventory.
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_exclusive_sold_reconcile"
down_revision = "0029_sessions_marketplace"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if not {"tracks", "orders"}.issubset(tables):
        return

    has_locks = "exclusive_ownership_locks" in tables
    ownership_clause = """
        EXISTS (
            SELECT 1
            FROM exclusive_ownership_locks l
            WHERE l.track_id = tracks.id
        )
        OR
    """ if has_locks else ""

    op.execute(sa.text(f"""
        UPDATE tracks
        SET is_sold = (
            {ownership_clause}
            EXISTS (
                SELECT 1
                FROM orders o
                WHERE o.track_id = tracks.id
                  AND lower(CAST(o.status AS TEXT)) = 'completed'
            )
        )
        WHERE lower(CAST(sales_model AS TEXT)) = 'exclusive'
    """))


def downgrade():
    # Data reconciliation is intentionally irreversible. Reverting code must
    # not fabricate sold inventory for unpaid/abandoned checkouts.
    pass
