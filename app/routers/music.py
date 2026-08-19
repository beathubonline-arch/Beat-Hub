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


# ==============================================================
# PUBLIC MUSIC
# ==============================================================

@router.get("/beats")
def browse_beats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    tracks = (
        db.query(Track)
        .filter(Track.is_published.is_(True))
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
        .filter(Track.is_published.is_(True))
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
        ctx(request, current_user),
    )


# ==============================================================
# TRACK DETAIL
# ==============================================================

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
            detail="Track not found.",
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


# ==============================================================
# ALBUM
# ==============================================================

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
            detail="Album not found.",
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


# ==============================================================
# CREATOR PROFILE
# ==============================================================

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
            detail="Profile not found.",
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


# ==============================================================
# PURCHASE DOWNLOAD
# ==============================================================

def _find_track(
    track_ref: str,
    db: Session,
) -> Optional[Track]:
    """
    Accept either:
        - Track UUID
        - Track slug
    """

    track = (
        db.query(Track)
        .filter(Track.id == track_ref)
        .first()
    )

    if track:
        return track

    return (
        db.query(Track)
        .filter(Track.slug == track_ref)
        .first()
    )


def _find_audio_file(track: Track) -> Optional[Path]:
    """
    Resolve the stored audio path safely.

    Supports paths stored as:
        media/audio/file.mp3
        audio/file.mp3
        /absolute/path/media/audio/file.mp3
        /opt/render/project/src/media/audio/file.mp3
    """

    if not track.audio_file_path:
        return None

    stored = Path(str(track.audio_file_path))

    media_root = Path(str(settings.MEDIA_ROOT))

    if not media_root.is_absolute():
        media_root = Path.cwd() / media_root

    media_root = media_root.resolve()

    candidates = []

    # ----------------------------------------------------------
    # Exact stored path
    # ----------------------------------------------------------

    if stored.is_absolute():
        candidates.append(stored)
    else:
        candidates.append(Path.cwd() / stored)
        candidates.append(media_root / stored)

    # ----------------------------------------------------------
    # If only filename/path was stored, look inside audio/.
    # ----------------------------------------------------------

    filename = stored.name

    candidates.extend(
        [
            media_root / "audio" / filename,
            Path.cwd() / "media" / "audio" / filename,
            Path("/opt/render/project/src") / "media" / "audio" / filename,
        ]
    )

    # ----------------------------------------------------------
    # Also handle "media/audio/filename.mp3" correctly.
    # ----------------------------------------------------------

    stored_parts = stored.parts

    if "media" in stored_parts:
        try:
            media_index = stored_parts.index("media")
            relative_after_media = Path(
                *stored_parts[media_index + 1:]
            )

            candidates.append(
                media_root / relative_after_media
            )
        except (ValueError, IndexError):
            pass

    # ----------------------------------------------------------
    # Remove duplicates while preserving order.
    # ----------------------------------------------------------

    checked = set()

    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue

        key = str(candidate)

        if key in checked:
            continue

        checked.add(key)

        if not candidate.exists():
            continue

        if not candidate.is_file():
            continue

        # Security: never serve anything outside MEDIA_ROOT.
        try:
            candidate.relative_to(media_root)
        except ValueError:
            continue

        return candidate

    return None


def _download_track(
    track_ref: str,
    db: Session,
    user: User,
):
    # ----------------------------------------------------------
    # Find purchased track by UUID OR slug.
    # ----------------------------------------------------------

    track = _find_track(track_ref, db)

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Purchased track was not found.",
        )

    # ----------------------------------------------------------
    # Verify ownership.
    #
    # The buyer must have:
    #   1. A License for this track
    #   2. A COMPLETED Order
    #
    # No payment confirmation = no download.
    # ----------------------------------------------------------

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

    # ----------------------------------------------------------
    # Locate the actual master audio file.
    # ----------------------------------------------------------

    audio_path = _find_audio_file(track)

    if audio_path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Your purchase is confirmed, but the audio file "
                "cannot be found in storage."
            ),
        )

    # ----------------------------------------------------------
    # Clean filename for buyer.
    # ----------------------------------------------------------

    safe_title = "".join(
        character
        for character in track.title
        if character.isalnum()
        or character in (" ", "-", "_")
    ).strip()

    if not safe_title:
        safe_title = "BeatHub-Track"

    file_extension = audio_path.suffix.lower()

    download_name = (
        f"{safe_title}{file_extension}"
        if file_extension
        else safe_title
    )

    # ----------------------------------------------------------
    # Force download.
    # ----------------------------------------------------------

    return FileResponse(
        path=str(audio_path),
        filename=download_name,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{download_name}"'
            ),
            "Cache-Control": "private, no-store, no-cache",
            "Pragma": "no-cache",
        },
    )


# --------------------------------------------------------------
# PRIMARY DOWNLOAD ROUTES
# --------------------------------------------------------------

@router.get("/download/track/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return _download_track(
        track_ref=track_ref,
        db=db,
        user=user,
    )


@router.get("/download/{track_ref}")
def download_track_short(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return _download_track(
        track_ref=track_ref,
        db=db,
        user=user,
    )


# --------------------------------------------------------------
# ADDITIONAL COMPATIBILITY ROUTES
#
# These support older dashboard/checkout templates that may have
# generated one of these URLs.
# --------------------------------------------------------------

@router.get("/tracks/{track_ref}/download")
def download_track_legacy(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return _download_track(
        track_ref=track_ref,
        db=db,
        user=user,
    )


@router.get("/track/{track_ref}/download")
def download_track_legacy_two(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return _download_track(
        track_ref=track_ref,
        db=db,
        user=user,
    )
