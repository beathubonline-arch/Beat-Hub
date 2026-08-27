from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, AlbumContentType, AlbumTrack, Track, TrackContentType
from app.models.user import User
from app.utils.deps import require_creator
from app.utils.text import unique_slug

router = APIRouter(tags=["albums"])
templates = Jinja2Templates(directory="app/templates")


def _ctx(request, user, tracks, error=None, **extra):
    return {
        "request": request,
        "current_user": user,
        "tracks": tracks,
        "error": error,
        "current_year": datetime.utcnow().year,
        **extra,
    }


def _allowed_tracks(db: Session, profile_id, content_type: str):
    return (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile_id,
            Track.content_type == content_type,
        )
        .order_by(Track.created_at.desc())
        .all()
    )


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
    return templates.TemplateResponse(
        request,
        "upload_album.html",
        _ctx(request, user, tracks, album_type="album"),
    )


@router.post("/dashboard/albums/new")
@router.post("/dashboard/album/new")
def create_album(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    content_type: str = Form(...),
    track_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    title = title.strip()
    content_type = (content_type or "").strip().lower()

    if content_type not in {
        AlbumContentType.ALBUM.value,
        AlbumContentType.BEAT_COLLECTION.value,
    }:
        tracks = (
            db.query(Track)
            .filter(Track.creator_profile_id == user.profile.id)
            .order_by(Track.created_at.desc())
            .all()
        )
        return templates.TemplateResponse(
            request,
            "upload_album.html",
            _ctx(request, user, tracks, "Choose Album / EP or Beat Collection first.", album_type=content_type),
            status_code=400,
        )

    if not title:
        tracks = (
            db.query(Track)
            .filter(Track.creator_profile_id == user.profile.id)
            .order_by(Track.created_at.desc())
            .all()
        )
        return templates.TemplateResponse(
            request,
            "upload_album.html",
            _ctx(request, user, tracks, "Album title is required.", album_type=content_type),
            status_code=400,
        )

    expected_track_type = (
        TrackContentType.TRACK.value
        if content_type == AlbumContentType.ALBUM.value
        else TrackContentType.BEAT.value
    )

    allowed = {
        str(t.id): t
        for t in _allowed_tracks(db, user.profile.id, expected_track_type)
    }
    selected_ids = list(dict.fromkeys(str(i) for i in track_ids))
    selected = [allowed[i] for i in selected_ids if i in allowed]

    if not selected:
        tracks = (
            db.query(Track)
            .filter(Track.creator_profile_id == user.profile.id)
            .order_by(Track.created_at.desc())
            .all()
        )
        return templates.TemplateResponse(
            request,
            "upload_album.html",
            _ctx(
                request,
                user,
                tracks,
                "Select at least one compatible track for this project.",
                album_type=content_type,
            ),
            status_code=400,
        )

    album = Album(
        creator_profile_id=user.profile.id,
        title=title,
        slug=unique_slug(db, Album, title, "album"),
        description=description.strip() or None,
        genre=genre.strip() or None,
        content_type=content_type,
        is_published=True,
    )
    db.add(album)
    db.flush()

    for position, track in enumerate(selected, start=1):
        db.add(AlbumTrack(album_id=album.id, track_id=track.id, position=position))

    db.commit()
    return RedirectResponse(url=f"/album/{album.slug}", status_code=303)


@router.get("/album/{slug}")
def album_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    album = (
        db.query(Album)
        .filter(Album.slug == slug, Album.is_published.is_(True))
        .first()
    )
    if not album:
        raise HTTPException(status_code=404, detail="Album not found.")

    return templates.TemplateResponse(
        request,
        "album_detail.html",
        {
            "request": request,
            "current_user": None,
            "user": None,
            "current_year": datetime.utcnow().year,
            "album": album,
        },
    )
