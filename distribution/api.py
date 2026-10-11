"""Creator-only distribution draft API. Not mounted until DB migration is deployed."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.utils.deps import require_creator
from distribution.models import DistributionRelease, DistributionTrack
from distribution.release_workflow import Release, Track, submit_for_review

router = APIRouter(prefix="/dashboard/distribution/api", tags=["distribution"])

class TrackInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    audio_key: str = Field(min_length=1)
    writers: list[str] = Field(min_length=1)
    explicit: bool = False
    isrc: str | None = None

class ReleaseInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    artist: str = Field(min_length=1, max_length=255)
    release_type: str
    artwork_key: str
    release_date: date
    rights_confirmed: bool = False
    tracks: list[TrackInput] = Field(default_factory=list)

def _owner(user: User) -> str:
    profile = getattr(user, "profile", None)
    if profile is None or getattr(profile, "id", None) is None:
        raise HTTPException(status_code=403, detail="Creator profile required")
    return str(profile.id)

@router.get("/releases")
def list_releases(db: Session = Depends(get_db), user: User = Depends(require_creator)):
    owner = _owner(user)
    rows = db.scalars(select(DistributionRelease).where(DistributionRelease.creator_id == owner).order_by(DistributionRelease.id.desc())).all()
    return [{"id": r.id, "title": r.title, "artist": r.artist, "status": r.status} for r in rows]

@router.post("/releases", status_code=201)
def create_release(payload: ReleaseInput, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    owner = _owner(user)
    if payload.release_type not in ("single", "ep", "album"):
        raise HTTPException(status_code=422, detail="Invalid release type")
    # Audio/artwork references must be validated against private storage ownership before enabling this route.
    raise HTTPException(status_code=503, detail="Distribution uploads awaiting private-storage ownership verification")

@router.post("/releases/{release_id}/submit")
def submit_release(release_id: int, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    owner = _owner(user)
    row = db.scalar(select(DistributionRelease).where(DistributionRelease.id == release_id, DistributionRelease.creator_id == owner))
    if row is None:
        raise HTTPException(status_code=404, detail="Release not found")
    draft = Release(owner, row.title, row.artist, row.release_type, row.artwork_key,
                    row.release_date, row.rights_confirmed,
                    [Track(t.title, t.audio_key, [w.strip() for w in t.writers.split(",") if w.strip()], t.explicit, t.isrc) for t in row.tracks])
    try:
        draft.status = type(draft.status)(row.status)
        submit_for_review(draft, owner)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row.status = draft.status.value
    db.commit()
    return {"id": row.id, "status": row.status}
