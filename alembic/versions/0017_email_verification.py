"""Add secure email verification state to users.

Revision ID: email_verification_017
Revises: platform_ledger_uniqueness_016
"""

from alembic import op
import sqlalchemy as sa

revision = "email_verification_017"
down_revision = "platform_ledger_uniqueness_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The BeatHub baseline creates tables from the current SQLAlchemy models.
    # That means a fresh database may already contain these columns, while an
    # older production database at revision 016 may not. Reconcile both cases
    # without dropping, recreating, or changing existing data.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns("users")
    }

    if "verification_code_hash" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("verification_code_hash", sa.String(length=128), nullable=True),
        )

    if "verification_code_expires" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("verification_code_expires", sa.DateTime(), nullable=True),
        )

    if "verification_attempts" not in existing_columns:
        op.add_column(
            "users",
            sa.Column(
                "verification_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
        op.alter_column("users", "verification_attempts", server_default=None)


def downgrade() -> None:
    # Only remove columns introduced by this migration when they actually
    # exist. This keeps downgrade safe for databases where the baseline
    # already owned the columns.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns("users")
    }

    if "verification_attempts" in existing_columns:
        op.drop_column("users", "verification_attempts")
    if "verification_code_expires" in existing_columns:
        op.drop_column("users", "verification_code_expires")
    if "verification_code_hash" in existing_columns:
        op.drop_column("users", "verification_code_hash")
