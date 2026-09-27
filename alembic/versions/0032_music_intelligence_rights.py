"""music intelligence and machine-readable licence rights

Revision ID: 0032_music_intelligence_rights
Revises: acb258ba7531
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_music_intelligence_rights"
down_revision = "acb258ba7531"
branch_labels = None
depends_on = None

TRACK_COLUMNS = {
    "mood": sa.Column("mood", sa.String(80), nullable=True),
    "energy": sa.Column("energy", sa.String(30), nullable=True),
    "instruments": sa.Column("instruments", sa.Text(), nullable=True),
    "vocal_type": sa.Column("vocal_type", sa.String(80), nullable=True),
    "intended_use": sa.Column("intended_use", sa.Text(), nullable=True),
    "similar_sound": sa.Column("similar_sound", sa.Text(), nullable=True),
    "region": sa.Column("region", sa.String(100), nullable=True),
    "commercial_use_allowed": sa.Column("commercial_use_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
    "sampling_allowed": sa.Column("sampling_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
    "remixing_allowed": sa.Column("remixing_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
    "resale_allowed": sa.Column("resale_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
    "ai_training_allowed": sa.Column("ai_training_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
    "synthetic_likeness_allowed": sa.Column("synthetic_likeness_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
    "derivatives_allowed": sa.Column("derivatives_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
    "sublicensing_allowed": sa.Column("sublicensing_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
}
RIGHT_COLUMNS = {name: sa.Column(name, sa.Boolean(), nullable=False, server_default=column.server_default.arg) for name, column in TRACK_COLUMNS.items() if isinstance(column.type, sa.Boolean)}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    track_existing = {column["name"] for column in inspector.get_columns("tracks")}
    for name, column in TRACK_COLUMNS.items():
        if name not in track_existing: op.add_column("tracks", column)
    license_existing = {column["name"] for column in inspector.get_columns("licenses")}
    for name, column in RIGHT_COLUMNS.items():
        if name not in license_existing: op.add_column("licenses", column)
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("tracks")}
    for name in ("mood", "energy", "region"):
        index_name = f"ix_tracks_{name}"
        if index_name not in indexes: op.create_index(index_name, "tracks", [name])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("tracks")}
    for name in ("mood", "energy", "region"):
        index_name = f"ix_tracks_{name}"
        if index_name in indexes: op.drop_index(index_name, table_name="tracks")
    license_existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("licenses")}
    for name in reversed(list(RIGHT_COLUMNS)):
        if name in license_existing: op.drop_column("licenses", name)
    track_existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tracks")}
    for name in reversed(list(TRACK_COLUMNS)):
        if name in track_existing: op.drop_column("tracks", name)
