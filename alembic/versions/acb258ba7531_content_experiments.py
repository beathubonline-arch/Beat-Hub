"""content experiments

Revision ID: acb258ba7531
Revises: 0031_release_campaigns
"""
from alembic import op
import sqlalchemy as sa

revision = "acb258ba7531"
down_revision = "0031_release_campaigns"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "content_experiment_variants" in set(inspector.get_table_names()):
        return
    op.create_table(
        "content_experiment_variants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_group_id", sa.String(36), nullable=False),
        sa.Column("creator_profile_id", sa.String(36), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track_id", sa.String(36), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hook_number", sa.Integer(), nullable=False),
        sa.Column("hook_text", sa.Text(), nullable=False),
        sa.Column("visual_treatment", sa.String(80), nullable=False),
        sa.Column("shot_direction", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False, server_default="all"),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_watch_time_seconds", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("saves", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sends", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profile_visits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("registrations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purchases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("experiment_group_id", "hook_number", "visual_treatment", name="uq_content_experiment_variant"),
    )
    op.create_index("ix_content_experiment_variants_group", "content_experiment_variants", ["experiment_group_id"])
    op.create_index("ix_content_experiment_variants_creator", "content_experiment_variants", ["creator_profile_id"])
    op.create_index("ix_content_experiment_variants_track", "content_experiment_variants", ["track_id"])
    op.create_index("ix_content_experiment_variants_status", "content_experiment_variants", ["status"])
    op.execute("ALTER TABLE content_experiment_variants ENABLE ROW LEVEL SECURITY")


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "content_experiment_variants" not in set(inspector.get_table_names()):
        return
    op.drop_table("content_experiment_variants")
