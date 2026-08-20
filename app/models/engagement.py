import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EngagementType(str, enum.Enum):
    VIEW = "view"
    PREVIEW_PLAY = "preview_play"
    DOWNLOAD = "download"


class EngagementEvent(Base):
    """
    Lightweight event log used for BeatHub marketplace analytics.

    Events are intentionally separate from Track so engagement data can grow
    without adding counters directly to the Track table.
    """

    __tablename__ = "engagement_events"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    track_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tracks.id"),
        nullable=False,
        index=True,
    )

    creator_profile_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("profiles.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    event_type: Mapped[EngagementType] = mapped_column(
        Enum(EngagementType),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    track = relationship("Track")
    creator_profile = relationship("Profile")
    user = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<EngagementEvent "
            f"{self.event_type.value} "
            f"track={self.track_id}>"
        )
