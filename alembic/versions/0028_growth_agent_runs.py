"""Track Growth Agent execution runs.

Revision ID: 0028_growth_agent_runs
Revises: 0027_fix_day_29
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_growth_agent_runs"
down_revision = "0027_fix_day_29"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "growth_agent_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_key", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="local"),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("diagnosis", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_table("growth_agent_runs")
