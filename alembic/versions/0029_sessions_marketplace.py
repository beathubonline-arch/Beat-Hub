"""BeatHub Sessions marketplace foundation."""
from alembic import op
import sqlalchemy as sa
revision = "0029_sessions_marketplace"
down_revision = "growth_agent_runs_028"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    # The baseline migration creates the current model metadata on a brand-new
    # database. Guard these creates so both empty databases and existing
    # production databases can reach this revision safely.
    if "session_services" not in tables:
        op.create_table("session_services",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("creator_profile_id", sa.String(36), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(120), nullable=False), sa.Column("service_type", sa.String(40), nullable=False),
            sa.Column("description", sa.Text()), sa.Column("price", sa.Numeric(12,2), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(3), nullable=False, server_default="KES"),
            sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("location_mode", sa.String(20), nullable=False, server_default="remote"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        op.create_index("ix_session_services_creator_profile_id","session_services",["creator_profile_id"])
        op.create_index("ix_session_services_service_type","session_services",["service_type"])

    if "session_bookings" not in tables:
        op.create_table("session_bookings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("service_id", sa.String(36), sa.ForeignKey("session_services.id", ondelete="CASCADE"), nullable=False),
            sa.Column("client_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("preferred_at", sa.DateTime(), nullable=False), sa.Column("note", sa.Text()),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        op.create_index("ix_session_bookings_service_id","session_bookings",["service_id"])
        op.create_index("ix_session_bookings_client_user_id","session_bookings",["client_user_id"])

def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "session_bookings" in tables:
        op.drop_table("session_bookings")
    if "session_services" in tables:
        op.drop_table("session_services")
