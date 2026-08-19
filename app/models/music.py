import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SalesModel(str, enum.Enum):
    EXCLUSIVE = "exclusive"        # one-time purchase, becomes unavailable after sale
    NON_EXCLUSIVE = "non_exclusive"  # can be purchased by many buyers


class Album(Base):
    __tablename__ = "albums"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    creator_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("profiles.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    genre: Mapped[str | None] = mapped_column(String(80), nullable=True)
    artwork_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    release_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_published: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator_profile = relationship("Profile", back_populates="albums")
    album_tracks = relationship("AlbumTrack", back_populates="album", cascade="all, delete-orphan", order_by="AlbumTrack.position")

    def __repr__(self) -> str:
        return f"<Album {self.title}>"


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    creator_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("profiles.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    genre: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[str | None] = mapped_column(String(300), nullable=True)  # comma-separated

    cover_art_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_file_path: Mapped[str] = mapped_column(String(500), nullable=False)  # protected/master file
    preview_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # public preview/snippet

    price: Mapped[Numeric] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    sales_model: Mapped[SalesModel] = mapped_column(Enum(SalesModel), nullable=False, default=SalesModel.NON_EXCLUSIVE)

    # Authoritative sold flag for exclusive tracks. Combined with a DB-level
    # unique constraint on successful Orders (see Order model) to prevent
    # double-selling under race conditions.
    is_sold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    is_published: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator_profile = relationship("Profile", back_populates="tracks")
    album_links = relationship("AlbumTrack", back_populates="track")

    @property
    def is_available(self) -> bool:
        if self.sales_model == SalesModel.EXCLUSIVE:
            return self.is_published and not self.is_sold
        return self.is_published

    def __repr__(self) -> str:
        return f"<Track {self.title} ({self.sales_model})>"


class AlbumTrack(Base):
    """Join table attaching tracks to an album, with ordering."""

    __tablename__ = "album_tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    album_id: Mapped[str] = mapped_column(String(36), ForeignKey("albums.id"), nullable=False, index=True)
    track_id: Mapped[str] = mapped_column(String(36), ForeignKey("tracks.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    album = relationship("Album", back_populates="album_tracks")
    track = relationship("Track", back_populates="album_links")
