"""Add persistent 30-day growth campaign calendar.

Revision ID: growth_campaign_024
Revises: growth_funnel_023
"""
from alembic import op
import sqlalchemy as sa

revision = "growth_campaign_024"
down_revision = "growth_funnel_023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "growth_campaign_days" in tables:
        return

    op.create_table(
        "growth_campaign_days",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("theme", sa.String(length=100), nullable=False),
        sa.Column("primary_channel", sa.String(length=50), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("content_action", sa.Text(), nullable=False),
        sa.Column("outreach_action", sa.Text(), nullable=False),
        sa.Column("measurement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="planned"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day_number", name="uq_growth_campaign_days_day_number"),
    )
    op.create_index("ix_growth_campaign_days_date", "growth_campaign_days", ["date"])
    op.create_index("ix_growth_campaign_days_status", "growth_campaign_days", ["status"])


def downgrade() -> None:
    pass
