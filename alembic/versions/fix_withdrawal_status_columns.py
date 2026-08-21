"""Normalize BeatHub withdrawal status columns.

Revision ID: fix_withdrawal_status_001
Revises: 654395e9ee8e
"""

from alembic import op
import sqlalchemy as sa


# ---------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------

revision = "fix_withdrawal_status_001"

# THIS IS THE IMPORTANT FIX.
# This migration now belongs to the initial migration chain.
down_revision = "654395e9ee8e"

branch_labels = None
depends_on = None


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar()
    )


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
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


# ---------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------

def upgrade() -> None:

    bind = op.get_bind()

    # ================================================================
    # CREATOR WITHDRAWALS
    # ================================================================

    if _table_exists(bind, "withdrawal_requests") and _column_exists(
        bind,
        "withdrawal_requests",
        "status",
    ):

        # Convert PostgreSQL ENUM -> VARCHAR.
        #
        # This is deliberately done using PostgreSQL's text cast so
        # existing records are preserved.
        bind.execute(
            sa.text(
                """
                ALTER TABLE withdrawal_requests
                ALTER COLUMN status TYPE VARCHAR(30)
                USING status::text
                """
            )
        )

        # Normalize both:
        #
        # PENDING    -> pending
        # APPROVED   -> approved
        # PROCESSING -> processing
        # PAID       -> paid
        # REJECTED   -> rejected
        #
        # Also handles records that were already lowercase.
        bind.execute(
            sa.text(
                """
                UPDATE withdrawal_requests
                SET status = LOWER(status)
                WHERE status IS NOT NULL
                """
            )
        )

        bind.execute(
            sa.text(
                """
                ALTER TABLE withdrawal_requests
                ALTER COLUMN status SET DEFAULT 'pending'
                """
            )
        )

    # ================================================================
    # ADMIN WITHDRAWALS
    #
    # This table may already exist from the admin withdrawal feature.
    # Do NOT create it here because it is not part of the initial
    # schema supplied by the project.
    # ================================================================

    if _table_exists(bind, "admin_withdrawals") and _column_exists(
        bind,
        "admin_withdrawals",
        "status",
    ):

        # If this is currently a PostgreSQL enum, convert it.
        #
        # Using a DO block makes this safe when the column is already
        # VARCHAR/TEXT.
        bind.execute(
            sa.text(
                """
                DO $$
                DECLARE
                    current_type TEXT;
                BEGIN

                    SELECT format_type(a.atttypid, a.atttypmod)
                    INTO current_type
                    FROM pg_attribute a
                    JOIN pg_class c
                      ON c.oid = a.attrelid
                    WHERE c.relname = 'admin_withdrawals'
                      AND a.attname = 'status'
                      AND a.attnum > 0
                      AND NOT a.attisdropped;

                    IF current_type IS NOT NULL
                       AND current_type NOT IN ('character varying', 'text') THEN

                        ALTER TABLE admin_withdrawals
                        ALTER COLUMN status TYPE VARCHAR(30)
                        USING status::text;

                    END IF;

                END
                $$;
                """
            )
        )

        bind.execute(
            sa.text(
                """
                UPDATE admin_withdrawals
                SET status = LOWER(status)
                WHERE status IS NOT NULL
                """
            )
        )

        bind.execute(
            sa.text(
                """
                ALTER TABLE admin_withdrawals
                ALTER COLUMN status SET DEFAULT 'pending'
                """
            )
        )


# ---------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------

def downgrade() -> None:
    # Intentionally left empty.
    #
    # Converting back to PostgreSQL ENUMs would reintroduce the exact
    # PENDING/pending problem that caused the production failures.
    pass
