"""Normalize BeatHub withdrawal status columns.

Revision ID: fix_withdrawals_002
Revises: 654395e9ee8e
"""

from alembic import op
import sqlalchemy as sa


revision = "fix_withdrawals_002"

# IMPORTANT:
# This points directly to the original schema.
down_revision = "654395e9ee8e"

branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                )
                """
            ),
            {
                "table_name": table_name,
            },
        ).scalar()
    )


def _column_exists(
    bind,
    table_name: str,
    column_name: str,
) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                    AND column_name = :column_name
                )
                """
            ),
            {
                "table_name": table_name,
                "column_name": column_name,
            },
        ).scalar()
    )


def upgrade():

    bind = op.get_bind()

    # ---------------------------------------------------------
    # CREATOR WITHDRAWALS
    # ---------------------------------------------------------

    if _table_exists(
        bind,
        "withdrawal_requests",
    ) and _column_exists(
        bind,
        "withdrawal_requests",
        "status",
    ):

        # Convert PostgreSQL enum to text/varchar.
        op.execute(
            """
            ALTER TABLE withdrawal_requests
            ALTER COLUMN status TYPE VARCHAR(30)
            USING status::text
            """
        )

        # Normalize old uppercase enum values.
        op.execute(
            """
            UPDATE withdrawal_requests
            SET status = LOWER(status)
            WHERE status IS NOT NULL
            """
        )

        # Make sure old/null rows become valid.
        op.execute(
            """
            UPDATE withdrawal_requests
            SET status = 'pending'
            WHERE status IS NULL
            """
        )

    # ---------------------------------------------------------
    # ADMIN WITHDRAWALS
    # ---------------------------------------------------------

    if _table_exists(
        bind,
        "admin_withdrawals",
    ) and _column_exists(
        bind,
        "admin_withdrawals",
        "status",
    ):

        op.execute(
            """
            ALTER TABLE admin_withdrawals
            ALTER COLUMN status TYPE VARCHAR(30)
            USING status::text
            """
        )

        op.execute(
            """
            UPDATE admin_withdrawals
            SET status = LOWER(status)
            WHERE status IS NOT NULL
            """
        )

        op.execute(
            """
            UPDATE admin_withdrawals
            SET status = 'pending'
            WHERE status IS NULL
            """
        )


def downgrade():
    # Deliberately left empty.
    #
    # Converting these columns back to PostgreSQL ENUMs would
    # recreate the exact PENDING/pending problem we are fixing.
    pass
