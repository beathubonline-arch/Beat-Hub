"""Fix BeatHub withdrawal status storage.

Revision ID: fix_withdrawal_status_001
Revises: 654395e9ee8e
"""

from alembic import op
import sqlalchemy as sa


revision = "fix_withdrawal_status_001"
down_revision = "654395e9ee8e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------
    # CREATOR WITHDRAWALS
    # ------------------------------------------------------------

    creator_exists = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = 'withdrawal_requests'
            )
            """
        )
    ).scalar()

    if creator_exists:
        # Convert the PostgreSQL enum to VARCHAR.
        #
        # The initial schema created:
        # withdrawalstatus =
        # PENDING, APPROVED, PROCESSING, PAID, REJECTED
        #
        # Our application uses lowercase values, so VARCHAR is safer
        # and avoids PostgreSQL enum casing conflicts.
        bind.execute(
            sa.text(
                """
                ALTER TABLE withdrawal_requests
                ALTER COLUMN status TYPE VARCHAR(30)
                USING status::text
                """
            )
        )

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

    # ------------------------------------------------------------
    # ADMIN WITHDRAWALS
    # ------------------------------------------------------------

    admin_exists = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = 'admin_withdrawals'
            )
            """
        )
    ).scalar()

    if admin_exists:
        bind.execute(
            sa.text(
                """
                ALTER TABLE admin_withdrawals
                ALTER COLUMN status TYPE VARCHAR(30)
                USING status::text
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


def downgrade() -> None:
    # Intentionally leave withdrawal statuses as VARCHAR.
    #
    # Recreating PostgreSQL ENUMs would reintroduce the casing problem
    # that this migration is specifically designed to eliminate.
    pass
