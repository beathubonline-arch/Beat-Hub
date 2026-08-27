"""Add explicit content classification for tracks and albums.

Revision ID: music_content_types_014
Revises: creator_consistency_013

Existing tracks are classified as beats by default, except tracks owned by
artist profiles, which are classified as finished tracks. Existing albums are
classified as beat collections for producer profiles and albums for artist
profiles. New uploads always require an explicit content type in the UI.
"""

from alembic import op
import sqlalchemy as sa

revision = "music_content_types_014"
down_revision = "creator_consistency_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tracks" in tables:
        columns = {column["name"] for column in inspector.get_columns("tracks")}
        if "content_type" not in columns:
            op.add_column(
                "tracks",
                sa.Column(
                    "content_type",
                    sa.String(length=20),
                    nullable=False,
                    server_default="beat",
                ),
            )
            op.create_index("ix_tracks_content_type", "tracks", ["content_type"])

        # Preserve the existing marketplace meaning for producer uploads,
        # while making existing artist-published audio appear as music tracks.
        bind.execute(
            sa.text(
                """
                UPDATE tracks AS t
                SET content_type = 'track'
                FROM profiles AS p
                WHERE t.creator_profile_id = p.id
                  AND COALESCE(p.is_artist, FALSE) = TRUE
                """
            )
        )

    if "albums" in tables:
        columns = {column["name"] for column in inspector.get_columns("albums")}
        if "content_type" not in columns:
            op.add_column(
                "albums",
                sa.Column(
                    "content_type",
                    sa.String(length=30),
                    nullable=False,
                    server_default="beat_collection",
                ),
            )
            op.create_index("ix_albums_content_type", "albums", ["content_type"])

        bind.execute(
            sa.text(
                """
                UPDATE albums AS a
                SET content_type = 'album'
                FROM profiles AS p
                WHERE a.creator_profile_id = p.id
                  AND COALESCE(p.is_artist, FALSE) = TRUE
                """
            )
        )


def downgrade() -> None:
    # Forward-only in production. Removing classification columns would erase
    # user-selected publishing metadata from existing content.
    pass
