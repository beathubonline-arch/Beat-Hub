import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GrowthProspect(Base):
    """Public prospect tracked through the human-approved growth funnel."""
    __tablename__ = "growth_prospects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prospect_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    public_url: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    why_fit: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="found", index=True)
    matched_track_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    matched_track = relationship("Track", foreign_keys=[matched_track_id])
    touches = relationship("GrowthTouch", back_populates="prospect", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("public_url", name="uq_growth_prospects_public_url"),)


class GrowthTouch(Base):
    """Auditable human-in-loop funnel event; never sends a message itself."""
    __tablename__ = "growth_touches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    prospect_id: Mapped[str] = mapped_column(String(36), ForeignKey("growth_prospects.id", ondelete="CASCADE"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    prospect = relationship("GrowthProspect", back_populates="touches")


class GrowthExperiment(Base):
    """Simple experiment ledger for measuring growth loops."""
    __tablename__ = "growth_experiments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned", index=True)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    registrations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    purchases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class GrowthCampaignDay(Base):
    """One operational day in BeatHub's 30-day acquisition campaign."""
    __tablename__ = "growth_campaign_days"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    day_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    theme: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_channel: Mapped[str] = mapped_column(String(50), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    content_action: Mapped[str] = mapped_column(Text, nullable=False)
    outreach_action: Mapped[str] = mapped_column(Text, nullable=False)
    measurement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
