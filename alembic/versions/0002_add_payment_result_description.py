"""Add missing payment transaction result description column.

Revision ID: beathub_payment_result_description_002
Revises: beathub_baseline_001
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "beathub_payment_result_description_002"
down_revision = "beathub_baseline_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("result_description", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_transactions", "result_description")
