"""Fix BeatHub withdrawal status columns.

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

    # ---------------------------------------------------------
    # CREATOR WITHDRAWALS
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # ADMIN WITHDRAWALS
    # ---------------------------------------------------------

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


def downgrade() -> None:
    pass
