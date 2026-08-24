"""Compatibility migration for the historical withdrawal-status revision.

Revision ID: fix_withdrawal_status_001
Revises: beathub_baseline_001

This revision is intentionally a no-op. Older BeatHub PostgreSQL databases
were stamped with ``fix_withdrawal_status_001``. The migration file was later
removed, which made Alembic unable to resolve the database's current revision.
Restoring the revision as a no-op preserves those databases and reconnects the
migration history to the current payment migration.
"""

revision = "fix_withdrawal_status_001"
down_revision = "beathub_baseline_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
