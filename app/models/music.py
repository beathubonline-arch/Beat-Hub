import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class SalesModel(str, Enum):
    EXCLUSIVE = "exclusive"
    NON_EXCLUSIVE = "non_exclusive"


class TrackContentType(str, Enum):
    BEAT = "beat"
    TRACK = "track"


class AlbumContentType(str, Enum):
    BEAT_COLLECTION = "beat_collection"
    ALBUM = "album"


class ProductCurrency(str, Enum):
    KES = "KES"
    USD = "USD"


class Album(Base):
    __tablename__ = "albums"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    creator_profile_id = Column(String(36), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    genre = Column(String(100), nullable=True)
    artwork_path = Column(String(1000), nullable=True)
    release_date = Column(DateTime, nullable=True)
    content_type = Column(String(30), nullable=False, default=AlbumContentType.BEAT_COLLECTION.value, server_default="beat_collection", index=True)
    is_published = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator_profile = relationship("Profile", foreign_keys=[creator_profile_id], back_populates="albums")
    album_tracks = relationship("AlbumTrack", back_populates="album", cascade="all, delete-orphan", order_by="AlbumTrack.position")

    @property
    def artwork_url(self):
        value = getattr(self, "_artwork_url", None)
        return value or self.artwork_path

    @artwork_url.setter
    def artwork_url(self, value):
        self._artwork_url = value


class Track(Base):
    __tablename__ = "tracks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    creator_profile_id = Column(String(36), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    genre = Column(String(100), nullable=True)
    bpm = Column(Integer, nullable=True)
    tags = Column(Text, nullable=True)
    cover_art_path = Column(String(1000), nullable=True)
    audio_file_path = Column(String(1000), nullable=False)
    preview_file_path = Column(String(1000), nullable=True)
    price = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String(3), nullable=False, default=ProductCurrency.KES.value, server_default="KES", index=True)
    sales_model = Column(SAEnum(SalesModel, name="salesmodel", native_enum=False), nullable=False, default=SalesModel.NON_EXCLUSIVE)
    content_type = Column(String(20), nullable=False, default=TrackContentType.BEAT.value, server_default="beat", index=True)
    is_sold = Column(Boolean, nullable=False, default=False, server_default="0")
    is_published = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator_profile = relationship("Profile", foreign_keys=[creator_profile_id], back_populates="tracks")
    album_tracks = relationship("AlbumTrack", back_populates="track", cascade="all, delete-orphan")

    @property
    def cover_art_url(self):
        value = getattr(self, "_cover_art_url", None)
        return value or self.cover_art_path

    @cover_art_url.setter
    def cover_art_url(self, value):
        self._cover_art_url = value


class AlbumTrack(Base):
    __tablename__ = "album_tracks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    album_id = Column(String(36), ForeignKey("albums.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id = Column(String(36), ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)

    album = relationship("Album", back_populates="album_tracks")
    track = relationship("Track", back_populates="album_tracks")

    __table_args__ = (UniqueConstraint("album_id", "track_id", name="uq_album_track"),)
