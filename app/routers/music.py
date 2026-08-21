"""
BeatHub music and marketplace routes.

Public:
    /beats
    /hot-picks
    /sessions
    /track/{slug}
    /album/{slug}
    /profile/{slug}

Protected:
    /download/{track_ref}
    /download/track/{track_ref}
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Album, Track
from app.models.order import License, Order, OrderStatus
from app.models.user import User
from app.utils.deps import get_optional_user, require_user


router = APIRouter(tags=["music"])

templates = Jinja2Templates(directory="app/templates")


# ======================================================================
# SHARED TEMPLATE CONTEXT
# ======================================================================

def ctx(
    request: Request,
    current_user,
    **extra,
):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    data.update(extra)

    return data


# ======================================================================
# HELPERS
# ======================================================================

def clean_search(value: Optional[str]) -> str:
    return (value or "").strip()


def track_is_visible(track: Track) -> bool:
    return bool(
        track
        and track.is_published
    )


# ======================================================================
# BEAT MARKETPLACE
# ======================================================================

@router.get("/beats")
def browse_beats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
    q: Optional[str] = Query(
        default=None,
        max_length=100,
    ),
    genre: Optional[str] = Query(
        default=None,
        max_length=100,
    ),
    sort: str = Query(
        default="newest",
        max_length=30,
    ),
):
    """
    Main BeatHub discovery page.

    This is now the default landing page for buyer/artist accounts.

    Search:
        /beats?q=afro

    Genre:
        /beats?genre=Afrobeats

    Sorting:
        /beats?sort=newest
        /beats?sort=oldest
        /beats?sort=price_low
        /beats?sort=price_high
    """

    search = clean_search(q)
    selected_genre = clean_search(genre)

    query = (
        db.query(Track)
        .filter(
            Track.is_published == True  # noqa: E712
        )
    )

    # --------------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------------

    if search:
        search_pattern = f"%{search}%"

        query = query.filter(
            or_(
                Track.title.ilike(search_pattern),
                Track.genre.ilike(search_pattern),
                Track.tags.ilike(search_pattern),
                Track.description.ilike(search_pattern),
            )
        )

    # --------------------------------------------------------------
    # GENRE
    # --------------------------------------------------------------

    if selected_genre:
        query = query.filter(
            Track.genre.ilike(
                selected_genre
            )
        )

    # --------------------------------------------------------------
    # SORT
    # --------------------------------------------------------------

    if sort == "oldest":

        query = query.order_by(
            Track.created_at.asc()
        )

    elif sort == "price_low":

        query = query.order_by(
            Track.price.asc(),
            Track.created_at.desc(),
        )

    elif sort == "price_high":

        query = query.order_by(
            Track.price.desc(),
            Track.created_at.desc(),
        )

    else:

        sort = "newest"

        query = query.order_by(
            Track.created_at.desc()
        )

    tracks = query.limit(100).all()

    # --------------------------------------------------------------
    # FEATURED / NEWEST
    #
    # Only show these sections when the user isn't actively searching.
    # --------------------------------------------------------------

    featured_tracks = []

    if not search and not selected_genre:

        featured_tracks = (
            db.query(Track)
            .filter(
                Track.is_published == True  # noqa: E712
            )
            .order_by(
                Track.created_at.desc()
            )
            .limit(6)
            .all()
        )

    # --------------------------------------------------------------
    # AVAILABLE GENRES
    # --------------------------------------------------------------

    genre_rows = (
        db.query(Track.genre)
        .filter(
            Track.is_published == True,  # noqa: E712
            Track.genre.isnot(None),
            Track.genre != "",
        )
        .distinct()
        .order_by(
            Track.genre.asc()
        )
        .limit(30)
        .all()
    )

    genres = [
        row[0]
        for row in genre_rows
        if row[0]
    ]

    # --------------------------------------------------------------
    # EMPTY STATE
    # --------------------------------------------------------------

    has_results = bool(tracks)

    return templates.TemplateResponse(
        request,
        "browse.html",
        ctx(
            request,
            current_user,
            tracks=tracks,
            featured_tracks=featured_tracks,
            genres=genres,
            search=search,
            selected_genre=selected_genre,
            sort=sort,
            has_results=has_results,
            title="Find Your Sound",
        ),
    )


# ======================================================================
# HOT PICKS
# ======================================================================

@router.get("/hot-picks")
def hot_picks(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    tracks = (
        db.query(Track)
        .filter(
            Track.is_published == True  # noqa: E712
        )
        .order_by(
            Track.created_at.desc()
        )
        .limit(24)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "browse.html",
        ctx(
            request,
            current_user,
            tracks=tracks,
            featured_tracks=[],
            genres=[],
            search="",
            selected_genre="",
            sort="newest",
            has_results=bool(tracks),
            title="Hot Picks",
        ),
    )


# ======================================================================
# SESSIONS
# ======================================================================

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


# ======================================================================
# TRACK DETAIL
# ======================================================================

@router.get("/track/{slug}")
def track_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    track = (
        db.query(Track)
        .filter(
            Track.slug == slug
        )
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


# ======================================================================
# ALBUM
# ======================================================================

@router.get("/album/{slug}")
def album_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    album = (
        db.query(Album)
        .filter(
            Album.slug == slug
        )
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


# ======================================================================
# CREATOR PROFILE
# ======================================================================

@router.get("/profile/{slug}")
def profile_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    profile = (
        db.query(Profile)
        .filter(
            Profile.slug == slug
        )
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


# ======================================================================
# PURCHASE DOWNLOAD
# ======================================================================

@router.get("/download/track/{track_ref}")
@router.get("/download/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """
    Secure purchased-track download.

    Supports:
        /download/track/{track_id}
        /download/{track_id}
        /download/track/{slug}
        /download/{slug}

    IMPORTANT:
    This route only serves locally stored files.

    If the database contains an R2 URL such as:
        r2://bucket/audio/file.mp3

    this route deliberately does not pretend that it is a local file.
    R2-specific serving should be handled by the R2 storage layer.
    """

    # --------------------------------------------------------------
    # FIND TRACK BY ID
    # --------------------------------------------------------------

    track = (
        db.query(Track)
        .filter(
            Track.id == track_ref
        )
        .first()
    )

    # --------------------------------------------------------------
    # FALL BACK TO SLUG
    # --------------------------------------------------------------

    if not track:

        track = (
            db.query(Track)
            .filter(
                Track.slug == track_ref
            )
            .first()
        )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------------
    # VERIFY OWNERSHIP
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
    # AUDIO PATH
    # --------------------------------------------------------------

    if not track.audio_file_path:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    stored_path = Path(
        track.audio_file_path
    )

    # --------------------------------------------------------------
    # R2 REFERENCES ARE NOT LOCAL FILES
    # --------------------------------------------------------------

    stored_text = str(
        track.audio_file_path
    ).strip()

    if (
        stored_text.startswith("r2://")
        or stored_text.startswith("s3://")
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "This track is stored in cloud storage. "
                "The R2 download endpoint must be used."
            ),
        )

    # --------------------------------------------------------------
    # MEDIA ROOT
    # --------------------------------------------------------------

    media_root_value = getattr(
        settings,
        "MEDIA_ROOT",
        "media",
    )

    media_root = Path(
        media_root_value
    )

    if not media_root.is_absolute():
        media_root = (
            Path.cwd() / media_root
        )

    media_root = media_root.resolve()

    # --------------------------------------------------------------
    # EXACT STORED PATH
    # --------------------------------------------------------------

    if stored_path.is_absolute():

        audio_path = stored_path

    else:

        audio_path = (
            Path.cwd()
            / stored_path
        )

    audio_path = audio_path.resolve()

    # --------------------------------------------------------------
    # COMPATIBILITY PATHS
    # --------------------------------------------------------------

    if (
        not audio_path.exists()
        or not audio_path.is_file()
    ):

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
                candidate.relative_to(
                    media_root
                )

            except ValueError:
                continue

            if (
                candidate.exists()
                and candidate.is_file()
            ):
                found = candidate
                break

        if found is None:

            raise HTTPException(
                status_code=404,
                detail=(
                    "The purchased audio file "
                    "is missing from storage."
                ),
            )

        audio_path = found

    # --------------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------------

    try:

        audio_path.relative_to(
            media_root
        )

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
    # SAFE DOWNLOAD NAME
    # --------------------------------------------------------------

    safe_title = "".join(
        character
        for character in track.title
        if character.isalnum()
        or character in (
            " ",
            "-",
            "_",
        )
    ).strip()

    if not safe_title:
        safe_title = "BeatHub-Track"

    extension = (
        audio_path.suffix.lower()
    )

    download_name = (
        f"{safe_title}{extension}"
        if extension
        else safe_title
    )

    # --------------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------------

    return FileResponse(
        path=str(audio_path),
        filename=download_name,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{download_name}"'
            ),
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )
