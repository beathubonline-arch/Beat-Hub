from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Album, Track
from app.models.order import License, Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User
from app.utils.deps import get_optional_user, require_user


router = APIRouter(tags=["music"])

templates = Jinja2Templates(directory="app/templates")


def ctx(request: Request, current_user, **extra):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    data.update(extra)

    return data


# ----------------------------------------------------------------------
# PUBLIC MUSIC
# ----------------------------------------------------------------------

@router.get("/beats")
def browse_beats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    tracks = (
        db.query(Track)
        .filter(Track.is_published == True)  # noqa: E712
        .order_by(Track.created_at.desc())
        .limit(60)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "browse.html",
        ctx(
            request,
            current_user,
            tracks=tracks,
        ),
    )


@router.get("/hot-picks")
def hot_picks(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    tracks = (
        db.query(Track)
        .filter(Track.is_published == True)  # noqa: E712
        .order_by(Track.created_at.desc())
        .limit(12)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "browse.html",
        ctx(
            request,
            current_user,
            tracks=tracks,
            title="Hot Picks",
        ),
    )


@router.get("/sessions")
def sessions_page(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "sessions.html",
        ctx(
            request,
            current_user,
        ),
    )


# ----------------------------------------------------------------------
# TRACK DETAIL
# ----------------------------------------------------------------------

@router.get("/track/{slug}")
def track_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    track = (
        db.query(Track)
        .filter(Track.slug == slug)
        .first()
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found",
        )

    purchased = False

    if current_user:
        purchased = (
            db.query(License)
            .join(
                Order,
                License.order_id == Order.id,
            )
            .filter(
                License.buyer_id == current_user.id,
                License.track_id == track.id,
                Order.status == OrderStatus.COMPLETED,
            )
            .first()
            is not None
        )

    return templates.TemplateResponse(
        request,
        "track_detail.html",
        ctx(
            request,
            current_user,
            track=track,
            purchased=purchased,
        ),
    )


# ----------------------------------------------------------------------
# ALBUM
# ----------------------------------------------------------------------

@router.get("/album/{slug}")
def album_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    album = (
        db.query(Album)
        .filter(Album.slug == slug)
        .first()
    )

    if not album:
        raise HTTPException(
            status_code=404,
            detail="Album not found",
        )

    return templates.TemplateResponse(
        request,
        "album_detail.html",
        ctx(
            request,
            current_user,
            album=album,
        ),
    )


# ----------------------------------------------------------------------
# CREATOR PROFILE
# ----------------------------------------------------------------------

@router.get("/profile/{slug}")
def profile_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    profile = (
        db.query(Profile)
        .filter(Profile.slug == slug)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Profile not found",
        )

    tracks = [
        track
        for track in profile.tracks
        if track.is_published
    ]

    albums = [
        album
        for album in profile.albums
        if album.is_published
    ]

    return templates.TemplateResponse(
        request,
        "profile_detail.html",
        ctx(
            request,
            current_user,
            profile=profile,
            tracks=tracks,
            albums=albums,
        ),
    )


# ----------------------------------------------------------------------
# PURCHASE DOWNLOAD
# ----------------------------------------------------------------------
#
# Supports BOTH:
#
#   /download/track/{track_id}
#   /download/{track_id}
#   /download/track/{track_slug}
#   /download/{track_slug}
#
# This allows the download button to work whether the template
# supplies the Track ID or the Track slug.
# ----------------------------------------------------------------------

@router.get("/download/track/{track_ref}")
@router.get("/download/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # --------------------------------------------------------------
    # Find track by ID OR slug.
    # --------------------------------------------------------------

    track = (
        db.query(Track)
        .filter(Track.id == track_ref)
        .first()
    )

    if not track:
        track = (
            db.query(Track)
            .filter(Track.slug == track_ref)
            .first()
        )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------------
    # Verify actual ownership.
    #
    # Download is allowed only when:
    # - this user owns the license
    # - the license belongs to this track
    # - the underlying order is completed
    # --------------------------------------------------------------

    license_record = (
        db.query(License)
        .join(
            Order,
            License.order_id == Order.id,
        )
        .filter(
            License.buyer_id == user.id,
            License.track_id == track.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .first()
    )

    if not license_record:
        raise HTTPException(
            status_code=403,
            detail="You do not own this track.",
        )

    # --------------------------------------------------------------
    # Validate stored audio path.
    # --------------------------------------------------------------

    if not track.audio_file_path:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    stored_path = Path(track.audio_file_path)

    media_root = Path(settings.MEDIA_ROOT)

    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root

    media_root = media_root.resolve()

    # First try the exact stored path.
    if stored_path.is_absolute():
        audio_path = stored_path
    else:
        audio_path = Path.cwd() / stored_path

    audio_path = audio_path.resolve()

    # --------------------------------------------------------------
    # Compatibility with paths stored in different formats:
    #
    # media/audio/file.mp3
    # audio/file.mp3
    # /.../media/audio/file.mp3
    # --------------------------------------------------------------

    if not audio_path.exists() or not audio_path.is_file():
        filename = stored_path.name

        candidates = [
            media_root / stored_path,
            media_root / "audio" / filename,
            Path.cwd() / "media" / "audio" / filename,
        ]

        found = None

        for candidate in candidates:
            candidate = candidate.resolve()

            try:
                candidate.relative_to(media_root)
            except ValueError:
                continue

            if candidate.exists() and candidate.is_file():
                found = candidate
                break

        if found is None:
            raise HTTPException(
                status_code=404,
                detail="The purchased audio file is missing from storage.",
            )

        audio_path = found

    # --------------------------------------------------------------
    # Security: never serve a file outside MEDIA_ROOT.
    # --------------------------------------------------------------

    try:
        audio_path.relative_to(media_root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Invalid audio file location.",
        )

    if not audio_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Audio file is unavailable.",
        )

    # --------------------------------------------------------------
    # Clean download filename.
    # --------------------------------------------------------------

    safe_title = "".join(
        character
        for character in track.title
        if character.isalnum()
        or character in (" ", "-", "_")
    ).strip()

    if not safe_title:
        safe_title = "BeatHub-Track"

    extension = audio_path.suffix.lower()

    download_name = (
        f"{safe_title}{extension}"
        if extension
        else safe_title
    )

    # --------------------------------------------------------------
    # Force browser download.
    # --------------------------------------------------------------

    return FileResponse(
        path=str(audio_path),
        filename=download_name,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{download_name}"'
            ),
            "Cache-Control": "private, no-store",
        },
    )
