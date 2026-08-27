from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, AlbumTrack, Track
from app.models.user import User
from app.utils.deps import require_creator
from app.utils.text import unique_slug

router = APIRouter(tags=["albums"])
templates = Jinja2Templates(directory="app/templates")


def _ctx(request, user, tracks, error=None):
    return {
        "request": request,
        "current_user": user,
        "tracks": tracks,
        "error": error,
        "current_year": datetime.utcnow().year,
    }


@router.get("/dashboard/albums/new")
@router.get("/dashboard/album/new")
def create_album_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    tracks = (
        db.query(Track)
        .filter(Track.creator_profile_id == user.profile.id)
        .order_by(Track.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(request, "create_album.html", _ctx(request, user, tracks))


@router.post("/dashboard/albums/new")
@router.post("/dashboard/album/new")
def create_album(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    track_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    title = title.strip()
    if not title:
        tracks = db.query(Track).filter(Track.creator_profile_id == user.profile.id).all()
        return templates.TemplateResponse(request, "create_album.html", _ctx(request, user, tracks, "Album title is required."), status_code=400)

    allowed = {
        str(t.id): t
        for t in db.query(Track).filter(Track.creator_profile_id == user.profile.id).all()
    }
    selected = [allowed[i] for i in track_ids if i in allowed]

    album = Album(
        creator_profile_id=user.profile.id,
        title=title,
        slug=unique_slug(db, Album, title, "album"),
        description=description.strip() or None,
        genre=genre.strip() or None,
        is_published=False,
    )
    db.add(album)
    db.flush()

    for position, track in enumerate(selected, start=1):
        db.add(AlbumTrack(album_id=album.id, track_id=track.id, position=position))

    db.commit()
    return RedirectResponse(url="/dashboard?success=Album created successfully.", status_code=303)
