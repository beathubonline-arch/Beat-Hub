from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, Track
from app.models.profile import Profile
from app.models.user import User
from app.utils.deps import get_optional_user

router = APIRouter(tags=["music"])
templates = Jinja2Templates(directory="app/templates")


def ctx(request: Request, current_user, **extra):
    base = {"request": request, "current_user": current_user, "current_year": datetime.utcnow().year}
    base.update(extra)
    return base


@router.get("/beats")
def browse_beats(request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    tracks = db.query(Track).filter(Track.is_published == True).order_by(Track.created_at.desc()).limit(60).all()  # noqa: E712
    return templates.TemplateResponse(request, "browse.html", ctx(request, current_user, tracks=tracks))


@router.get("/hot-picks")
def hot_picks(request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    tracks = (
        db.query(Track)
        .filter(Track.is_published == True)  # noqa: E712
        .order_by(Track.created_at.desc())
        .limit(12)
        .all()
    )
    return templates.TemplateResponse(request, "browse.html", ctx(request, current_user, tracks=tracks, title="Hot Picks"))


@router.get("/sessions")
def sessions_page(request: Request, current_user: Optional[User] = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "sessions.html", ctx(request, current_user))


@router.get("/track/{slug}")
def track_detail(slug: str, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    track = db.query(Track).filter(Track.slug == slug).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    return templates.TemplateResponse(request, "track_detail.html", ctx(request, current_user, track=track))


@router.get("/album/{slug}")
def album_detail(slug: str, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    album = db.query(Album).filter(Album.slug == slug).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    return templates.TemplateResponse(request, "album_detail.html", ctx(request, current_user, album=album))


@router.get("/profile/{slug}")
def profile_detail(slug: str, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    profile = db.query(Profile).filter(Profile.slug == slug).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    tracks = [t for t in profile.tracks if t.is_published]
    albums = [a for a in profile.albums if a.is_published]
    return templates.TemplateResponse(request, "profile_detail.html", ctx(request, current_user, profile=profile, tracks=tracks, albums=albums))
