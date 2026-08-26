"""Make creator accounts structurally ready for creator-only routes.

Revision ID: creator_consistency_013
Revises: merch_repair_012

This migration repairs legitimate creator accounts that were left without a
Profile during earlier signup/migration states. It never promotes buyers and
does not use profile metadata to grant creator authorization.

PostgreSQL note: SQLAlchemy's ``Enum(UserRole)`` persists the Python enum
member names (VISITOR, BUYER, CREATOR, ADMIN) by default. The migration must
therefore compare against ``'CREATOR'`` rather than the Python enum value
string ``'creator'``.
"""

import re
import uuid

from alembic import op
import sqlalchemy as sa

revision = "creator_consistency_013"
down_revision = "merch_repair_012"
branch_labels = None
depends_on = None


def _slug_base(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip()).strip("-").lower()
    return value[:120] or "creator"


def upgrade() -> None:
    bind = op.get_bind()

    # IMPORTANT: app.models.user.UserRole is declared with SQLAlchemy Enum,
    # which persists enum MEMBER NAMES by default. Therefore PostgreSQL stores
    # CREATOR, not the Python enum value "creator".
    #
    # Only accounts whose canonical database role is creator are eligible for
    # repair. A profile flag is never consulted as an authorization source.
    rows = bind.execute(
        sa.text(
            """
            SELECT u.id, u.email, u.username
            FROM users AS u
            LEFT JOIN profiles AS p ON p.user_id = u.id
            WHERE u.role = 'CREATOR' AND p.id IS NULL
            ORDER BY u.created_at ASC, u.id ASC
            """
        )
    ).mappings().all()

    for row in rows:
        email = str(row.get("email") or "").strip()
        username = str(row.get("username") or "").strip()
        local_part = email.split("@", 1)[0] if "@" in email else email
        stage_name = (username or local_part or "BeatHub Creator")[:120]

        base = _slug_base(stage_name)
        slug = base
        suffix = 2
        while bind.execute(
            sa.text("SELECT 1 FROM profiles WHERE slug = :slug LIMIT 1"),
            {"slug": slug},
        ).first():
            slug = f"{base}-{suffix}"
            suffix += 1

        bind.execute(
            sa.text(
                """
                INSERT INTO profiles
                    (id, user_id, stage_name, slug, is_producer, is_dj, is_artist)
                VALUES
                    (:id, :user_id, :stage_name, :slug, TRUE, FALSE, FALSE)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": str(row["id"]),
                "stage_name": stage_name,
                "slug": slug,
            },
        )

    # Existing creator profiles must remain marked as producer metadata so the
    # creator-facing UI remains consistent. This does not affect authorization;
    # the application authorizes from users.role == creator.
    bind.execute(
        sa.text(
            """
            UPDATE profiles AS p
            SET is_producer = TRUE
            FROM users AS u
            WHERE p.user_id = u.id
              AND u.role = 'CREATOR'
              AND COALESCE(p.is_producer, FALSE) = FALSE
            """
        )
    )


def downgrade() -> None:
    # Deliberately forward-only. Removing repaired creator profiles during a
    # downgrade could destroy legitimate creator data and recreate the exact
    # inconsistency this migration is designed to eliminate.
    pass
