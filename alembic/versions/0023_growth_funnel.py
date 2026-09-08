"""Add persistent human-in-loop growth funnel tables.

Revision ID: growth_funnel_023
Revises: push_subscriptions_022
"""
from alembic import op
import sqlalchemy as sa

revision = "growth_funnel_023"
down_revision = "push_subscriptions_022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "growth_prospects" not in tables:
        op.create_table(
            "growth_prospects",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("prospect_type", sa.String(length=50), nullable=True),
            sa.Column("platform", sa.String(length=50), nullable=True),
            sa.Column("public_url", sa.Text(), nullable=False),
            sa.Column("location", sa.String(length=100), nullable=True),
            sa.Column("fit_score", sa.Integer(), nullable=True),
            sa.Column("why_fit", sa.Text(), nullable=True),
            sa.Column("recommended_angle", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="found"),
            sa.Column("matched_track_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["matched_track_id"], ["tracks.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("public_url", name="uq_growth_prospects_public_url"),
        )
        op.create_index("ix_growth_prospects_status", "growth_prospects", ["status"])
        op.create_index("ix_growth_prospects_matched_track_id", "growth_prospects", ["matched_track_id"])

    if "growth_touches" not in tables:
        op.create_table(
            "growth_touches",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("prospect_id", sa.String(length=36), nullable=False),
            sa.Column("stage", sa.String(length=40), nullable=False),
            sa.Column("channel", sa.String(length=50), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("approved_by_human", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["prospect_id"], ["growth_prospects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_growth_touches_prospect_id", "growth_touches", ["prospect_id"])
        op.create_index("ix_growth_touches_stage", "growth_touches", ["stage"])
        op.create_index("ix_growth_touches_created_at", "growth_touches", ["created_at"])

    if "growth_experiments" not in tables:
        op.create_table(
            "growth_experiments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("channel", sa.String(length=50), nullable=False),
            sa.Column("hypothesis", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="planned"),
            sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("visits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("registrations", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("purchases", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_growth_experiments_status", "growth_experiments", ["status"])


def downgrade() -> None:
    # Forward-only: growth analytics are operational history and are not
    # silently destroyed by an application downgrade.
    pass
