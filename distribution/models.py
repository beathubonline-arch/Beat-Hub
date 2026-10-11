"""Separate SQLAlchemy tables for distribution drafts; no production migration yet."""
from datetime import datetime, timezone
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class DistributionBase(DeclarativeBase):
    pass

class DistributionRelease(DistributionBase):
    __tablename__ = "distribution_releases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    artist: Mapped[str] = mapped_column(String(255), nullable=False)
    release_type: Mapped[str] = mapped_column(String(12), nullable=False)
    artwork_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    release_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    rights_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    tracks: Mapped[list["DistributionTrack"]] = relationship(back_populates="release", cascade="all, delete-orphan")

class DistributionTrack(DistributionBase):
    __tablename__ = "distribution_tracks"
    __table_args__ = (UniqueConstraint("release_id", "position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("distribution_releases.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    audio_key: Mapped[str] = mapped_column(Text, nullable=False)
    writers: Mapped[str] = mapped_column(Text, nullable=False)
    isrc: Mapped[str | None] = mapped_column(String(12), nullable=True)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    release: Mapped[DistributionRelease] = relationship(back_populates="tracks")
