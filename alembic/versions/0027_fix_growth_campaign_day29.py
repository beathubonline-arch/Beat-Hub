"""Correct day 29 after separating it from the final month-review day.

Revision ID: growth_campaign_fix_027
Revises: growth_campaign_seed_026
"""
from alembic import op
import sqlalchemy as sa

revision = "growth_campaign_fix_027"
down_revision = "growth_campaign_seed_026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """UPDATE growth_campaign_days
               SET theme = :theme,
                   primary_channel = :channel,
                   objective = :objective,
                   content_action = :content_action,
                   outreach_action = :outreach_action,
                   measurement = :measurement,
                   updated_at = CURRENT_TIMESTAMP
             WHERE day_number = 29"""
        ),
        {
            "theme": "Scale winners",
            "channel": "analytics",
            "objective": "Prepare the next growth loop",
            "content_action": "Document the best-performing content, offer and acquisition source so the next month starts from evidence.",
            "outreach_action": "Prioritize prospects and creators showing the strongest funnel progress for the next cycle.",
            "measurement": "Measure which loop produced the strongest qualified visits, registrations and purchases.",
        },
    )


def downgrade() -> None:
    pass
