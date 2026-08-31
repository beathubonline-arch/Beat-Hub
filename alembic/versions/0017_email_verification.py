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
    op.add_column("users", sa.Column("verification_code_hash", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("verification_code_expires", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("verification_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("users", "verification_attempts", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "verification_attempts")
    op.drop_column("users", "verification_code_expires")
    op.drop_column("users", "verification_code_hash")
