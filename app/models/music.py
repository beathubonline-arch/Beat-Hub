import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


# ======================================================================
# SALES MODEL
# ======================================================================

class SalesModel(str, Enum):
    EXCLUSIVE = "exclusive"
    NON_EXCLUSIVE = "non_exclusive"


# ======================================================================
# ALBUM
# ======================================================================

class Album(Base):
    __tablename__ = "albums"

    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    creator_profile_id = Column(
        String(36),
        ForeignKey(
            "profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    title = Column(
        String(255),
        nullable=False,
    )

    slug = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    description = Column(
        Text,
        nullable=True,
    )

    genre = Column(
        String(100),
        nullable=True,
    )

    artwork_path = Column(
        String(1000),
        nullable=True,
    )

    release_date = Column(
        DateTime,
        nullable=True,
    )

    is_published = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # ------------------------------------------------------------------
    # RELATIONSHIPS
    # ------------------------------------------------------------------

    creator_profile = relationship(
        "Profile",
        foreign_keys=[creator_profile_id],
    )

    album_tracks = relationship(
        "AlbumTrack",
        back_populates="album",
        cascade="all, delete-orphan",
        order_by="AlbumTrack.position",
    )

    # ------------------------------------------------------------------
    # TEMPLATE COMPATIBILITY
    # ------------------------------------------------------------------

    @property
    def artwork_url(self):
        """
        Compatibility property used by public album/store templates.

        If artwork_path already contains a public URL, return it directly.
        Otherwise return the stored path.
        """
        return self.artwork_path


# ======================================================================
# TRACK
# ======================================================================

class Track(Base):
    __tablename__ = "tracks"

    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    creator_profile_id = Column(
        String(36),
        ForeignKey(
            "profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    title = Column(
        String(255),
        nullable=False,
    )

    slug = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    description = Column(
        Text,
        nullable=True,
    )

    genre = Column(
        String(100),
        nullable=True,
    )

    bpm = Column(
        Integer,
        nullable=True,
    )

    tags = Column(
        Text,
        nullable=True,
    )

    cover_art_path = Column(
        String(1000),
        nullable=True,
    )

    audio_file_path = Column(
        String(1000),
        nullable=False,
    )

    preview_file_path = Column(
        String(1000),
        nullable=True,
    )

    price = Column(
        Numeric(12, 2),
        nullable=False,
        default=0,
    )

    sales_model = Column(
        SAEnum(
            SalesModel,
            name="salesmodel",
            native_enum=False,
            values_callable=lambda enum_cls: [
                member.value for member in enum_cls
            ],
        ),
        nullable=False,
        default=SalesModel.NON_EXCLUSIVE,
    )

    is_sold = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    is_published = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # ------------------------------------------------------------------
    # RELATIONSHIPS
    # ------------------------------------------------------------------

    creator_profile = relationship(
        "Profile",
        foreign_keys=[creator_profile_id],
    )

    album_tracks = relationship(
        "AlbumTrack",
        back_populates="track",
        cascade="all, delete-orphan",
    )

    # ------------------------------------------------------------------
    # TEMPLATE COMPATIBILITY
    # ------------------------------------------------------------------

    @property
    def cover_art_url(self):
        """
        Public template compatibility.

        The templates use track.cover_art_url while the database stores
        cover_art_path.
        """
        return self.cover_art_path


# ======================================================================
# ALBUM / TRACK LINK
# ======================================================================

class AlbumTrack(Base):
    __tablename__ = "album_tracks"

    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    album_id = Column(
        String(36),
        ForeignKey(
            "albums.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    track_id = Column(
        String(36),
        ForeignKey(
            "tracks.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    position = Column(
        Integer,
        nullable=False,
        default=0,
    )

    # ------------------------------------------------------------------
    # RELATIONSHIPS
    # ------------------------------------------------------------------

    album = relationship(
        "Album",
        back_populates="album_tracks",
    )

    track = relationship(
        "Track",
        back_populates="album_tracks",
    )

    __table_args__ = (
        UniqueConstraint(
            "album_id",
            "track_id",
            name="uq_album_track",
        ),
    )
