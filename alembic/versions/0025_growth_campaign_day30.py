"""Seed the final day of the 30-day growth campaign.

Revision ID: growth_campaign_day30_025
Revises: growth_campaign_024
"""
from datetime import date, datetime, timedelta
import uuid

from alembic import op
import sqlalchemy as sa

revision = "growth_campaign_day30_025"
down_revision = "growth_campaign_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(sa.text("SELECT 1 FROM growth_campaign_days WHERE day_number = 30 LIMIT 1")).first()
    if exists:
        return

    table = sa.table(
        "growth_campaign_days",
        sa.column("id", sa.String()),
        sa.column("day_number", sa.Integer()),
        sa.column("date", sa.Date()),
        sa.column("theme", sa.String()),
        sa.column("primary_channel", sa.String()),
        sa.column("objective", sa.Text()),
        sa.column("content_action", sa.Text()),
        sa.column("outreach_action", sa.Text()),
        sa.column("measurement", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("notes", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    now = datetime.utcnow()
    op.bulk_insert(table, [{
        "id": str(uuid.uuid4()),
        "day_number": 30,
        "date": date.today() + timedelta(days=29),
        "theme": "Month review",
        "primary_channel": "analytics",
        "objective": "Decide what deserves month 2",
        "content_action": "Publish a transparent progress recap using real numbers only.",
        "outreach_action": "Rank prospects by funnel progress and fit; identify one growth loop to double down on.",
        "measurement": "Review the full funnel and choose the next experiment from observed evidence.",
        "status": "planned",
        "notes": None,
        "created_at": now,
        "updated_at": now,
    }])


def downgrade() -> None:
    pass
