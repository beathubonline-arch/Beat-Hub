"""Fix BeatHub withdrawal status columns.

Convert creator and admin withdrawal status columns from PostgreSQL
ENUM types to VARCHAR so status values are stored consistently.
"""

from alembic import op
import sqlalchemy as sa


# IMPORTANT:
# Give this revision a unique ID.
revision = "fix_withdrawal_status_001"

# If your latest migration has a different revision ID, replace this
# value with that migration's revision ID.
down_revision = None

branch_labels = None
depends_on = None


def upgrade():
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


def downgrade():
    # We intentionally do not convert the columns back to PostgreSQL
    # ENUMs because doing so would reintroduce the original problem.
    pass
