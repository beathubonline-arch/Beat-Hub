"""BeatHub baseline migration.

Revision ID: beathub_baseline_001
Revises:

The original baseline was intentionally a no-op because older production
Databases were created by the legacy application. That is unsafe for a fresh
production database: later migrations expect the core tables to exist.

The baseline now bootstraps the SQLAlchemy-owned core schema exactly once.
Later migrations remain responsible for forward-compatible repairs and
feature-specific tables. Merchandise tables remain owned by their dedicated
migration.
"""

from alembic import op

from app.database import Base
# Importing the model package registers every SQLAlchemy model with Base.
import app.models  # noqa: F401,E402


revision = "beathub_baseline_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    # Production financial/user data is forward-only. Never drop the entire
    # BeatHub schema during a downgrade.
    pass
