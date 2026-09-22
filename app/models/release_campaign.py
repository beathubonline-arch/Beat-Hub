"""Persisted creator release campaigns for the BeatHub AI Career Agent."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class ReleaseCampaign(Base):
    __tablename__ = "release_campaigns"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    creator_profile_id = Column(String(36), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id = Column(String(36), ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    goal = Column(String(120), nullable=True)
    audience = Column(String(255), nullable=True)
    release_date = Column(String(40), nullable=True)
    positioning = Column(Text, nullable=False)
    hooks_json = Column(Text, nullable=False)
    captions_json = Column(Text, nullable=False)
    video_ideas_json = Column(Text, nullable=False)
    rollout_json = Column(Text, nullable=False)
    promo_copy_json = Column(Text, nullable=False)
    checklist_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator_profile = relationship("Profile", foreign_keys=[creator_profile_id])
    track = relationship("Track", foreign_keys=[track_id])
