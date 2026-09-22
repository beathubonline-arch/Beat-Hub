"""Add persisted AI release campaigns."""
from alembic import op
import sqlalchemy as sa

revision = "0031_release_campaigns"
down_revision = "0030_exclusive_sold_reconcile"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "release_campaigns" in set(inspector.get_table_names()):
        return
    op.create_table(
        "release_campaigns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("creator_profile_id", sa.String(36), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track_id", sa.String(36), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal", sa.String(120), nullable=True),
        sa.Column("audience", sa.String(255), nullable=True),
        sa.Column("release_date", sa.String(40), nullable=True),
        sa.Column("positioning", sa.Text(), nullable=False),
        sa.Column("hooks_json", sa.Text(), nullable=False),
        sa.Column("captions_json", sa.Text(), nullable=False),
        sa.Column("video_ideas_json", sa.Text(), nullable=False),
        sa.Column("rollout_json", sa.Text(), nullable=False),
        sa.Column("promo_copy_json", sa.Text(), nullable=False),
        sa.Column("checklist_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_release_campaigns_creator_profile_id", "release_campaigns", ["creator_profile_id"])
    op.create_index("ix_release_campaigns_track_id", "release_campaigns", ["track_id"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "release_campaigns" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_release_campaigns_track_id", table_name="release_campaigns")
    op.drop_index("ix_release_campaigns_creator_profile_id", table_name="release_campaigns")
    op.drop_table("release_campaigns")
