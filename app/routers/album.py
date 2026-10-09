from datetime import datetime
from pathlib import Path
import re
import zipfile
import zipstream
from fastapi.responses import StreamingResponse
from app.config import settings
from app.services.storage import _parse_r2_path, _r2_client, r2_object_head

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import Album, AlbumContentType, AlbumTrack, Track, TrackContentType
from app.models.user import User
from app.utils.deps import require_creator, get_optional_user, require_user
from app.models.order import License, Order, OrderStatus
from app.services.storage import media_url, r2_presigned_url
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
    user: User | None = Depends(get_optional_user),
):
    album = (
        db.query(Album)
        .filter(Album.slug == slug, Album.is_published.is_(True))
        .first()
    )
    # Legacy promotional URLs must survive an album being given a different slug.
    if not album and slug == "time-itatell":
        album = (
            db.query(Album)
            .filter(Album.title.ilike("TIME ITATELL"), Album.is_published.is_(True))
            .first()
        )
    if not album:
        raise HTTPException(status_code=404, detail="Album not found.")

    album.artwork_url = r2_presigned_url(album.artwork_path) if album.artwork_path else None
    purchased = bool(user and db.query(License).join(Order, License.order_id == Order.id).filter(License.buyer_id == user.id, License.album_id == album.id, Order.status == OrderStatus.COMPLETED).first())
    return templates.TemplateResponse(
        request,
        "album_detail.html",
        {
            "request": request,
            "current_user": None,
            "user": None,
            "current_year": datetime.utcnow().year,
            "album": album,
            "purchased": purchased,
            "viewer": user,
        },
    )

@router.get("/album/{slug}/download/{track_id}")
def download_album_track(
    slug: str,
    track_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    album = db.query(Album).filter(Album.slug == slug).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found.")
    member = db.query(AlbumTrack).filter(AlbumTrack.album_id == album.id, AlbumTrack.track_id == track_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Track not in album.")
    owned = db.query(License).join(Order, License.order_id == Order.id).filter(
        License.buyer_id == user.id, License.album_id == album.id, Order.status == OrderStatus.COMPLETED
    ).first()
    if not owned:
        raise HTTPException(status_code=403, detail="Purchase this album to download its tracks.")
    url = media_url(member.track.audio_file_path, expires=300)
    if not url:
        raise HTTPException(status_code=404, detail="Track audio unavailable.")
    return RedirectResponse(url, status_code=303)

def _album_zip_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return (cleaned or "BeatHub-Album")[:90]


@router.get("/album/{slug}/download.zip")
def download_album_zip(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    album = db.query(Album).filter(Album.slug == slug, Album.is_published.is_(True)).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found.")
    owned = db.query(License).join(Order, License.order_id == Order.id).filter(
        License.buyer_id == user.id, License.album_id == album.id,
        Order.buyer_id == user.id, Order.status == OrderStatus.COMPLETED
    ).first()
    if not owned:
        raise HTTPException(status_code=403, detail="Complete your purchase to download this album.")
    members = list(album.album_tracks)
    if not members or (slug == "time-itatell" and len(members) != 12):
        raise HTTPException(status_code=409, detail="The album tracklist is incomplete.")
    client = _r2_client() if any(str(at.track.audio_file_path).startswith("r2://") for at in members) else None
    sources = []
    media_root = Path(settings.MEDIA_ROOT).resolve()
    for index, at in enumerate(members, 1):
        path = str(at.track.audio_file_path or "")
        bucket, key = _parse_r2_path(path)
        if bucket is not None:
            if not bucket or not key:
                raise HTTPException(status_code=503, detail="An album track is unavailable.")
            try:
                client.head_object(Bucket=bucket, Key=key)
            except Exception:
                raise HTTPException(status_code=503, detail="An album track is unavailable.")
            source = ("r2", bucket, key)
        else:
            relative = path.replace("\\", "/").lstrip("/")
            if relative.startswith("media/"):
                relative = relative[6:]
            full_path = (media_root / relative).resolve()
            if not full_path.is_relative_to(media_root) or not full_path.is_file():
                raise HTTPException(status_code=503, detail="An album track is unavailable.")
            source = ("local", full_path, None)
        extension = Path(key if bucket is not None else path).suffix.lower()
        if extension not in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".mpeg"}:
            extension = ".wav"
        title = _album_zip_filename(at.track.title.rsplit(".", 1)[0])
        filename = f"{index:02d} - {title}{extension}"
        sources.append((source, filename))

    def chunks(source):
        kind, first, second = source
        if kind == "local":
            with open(first, "rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
        else:
            response = client.get_object(Bucket=first, Key=second)
            body = response["Body"]
            try:
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        yield chunk
            finally:
                body.close()

    def stream_archive():
        archive = zipstream.ZipStream(compress_type=zipfile.ZIP_STORED)
        for source, filename in sources:
            archive.add(chunks(source), arcname=filename)
        yield from archive

    filename = _album_zip_filename(album.title) + ".zip"
    return StreamingResponse(
        stream_archive(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
