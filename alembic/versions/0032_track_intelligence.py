"""Add track intelligence snapshot to release campaigns."""
from alembic import op
import sqlalchemy as sa

revision = "0032_track_intelligence"
down_revision = "0031_release_campaigns"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    cols = {c["name"] for c in inspector.get_columns("release_campaigns")}
    if "track_intelligence_json" not in cols:
        op.add_column("release_campaigns", sa.Column("track_intelligence_json", sa.Text(), nullable=True))

def downgrade():
    inspector = sa.inspect(op.get_bind())
    cols = {c["name"] for c in inspector.get_columns("release_campaigns")}
    if "track_intelligence_json" in cols:
        op.drop_column("release_campaigns", "track_intelligence_json")
