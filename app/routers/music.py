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

Availability rules:
    NON-EXCLUSIVE:
        published = available
        is_sold does NOT make it unavailable

    EXCLUSIVE:
        published AND is_sold == False = available
        published AND is_sold == True  = unavailable
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
from app.models.music import Album, SalesModel, Track
from app.models.order import License, Order, OrderStatus
from app.models.user import User
from app.models.profile import Profile
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
# AVAILABILITY
# ======================================================================

def track_sales_model_value(track: Track) -> str:
    """
    Return the normalized sales-model value.

    Supports both:
        SalesModel.NON_EXCLUSIVE
    and:
        "non_exclusive"
    """

    sales_model = getattr(
        track,
        "sales_model",
        None,
    )

    value = getattr(
        sales_model,
        "value",
        sales_model,
    )

    return str(
        value or ""
    ).strip().lower()


def track_is_available(track: Track) -> bool:
    """
    Single authoritative availability rule.

    NON-EXCLUSIVE:
        Published beats remain purchasable repeatedly.
        is_sold is intentionally ignored.

    EXCLUSIVE:
        Published + not sold = available.
        Published + sold = unavailable.

    Unknown/invalid sales models are treated as unavailable
    rather than accidentally exposing a broken purchase path.
    """

    if not track:
        return False

    # --------------------------------------------------------------
    # Must be published first.
    # --------------------------------------------------------------

    if not bool(
        getattr(
            track,
            "is_published",
            False,
        )
    ):
        return False

    sales_model = track_sales_model_value(track)

    # --------------------------------------------------------------
    # NON-EXCLUSIVE
    #
    # A non-exclusive beat can be purchased by multiple artists.
    # Therefore is_sold must NOT block purchasing.
    # --------------------------------------------------------------

    if sales_model == SalesModel.NON_EXCLUSIVE.value:
        return True

    # --------------------------------------------------------------
    # EXCLUSIVE
    #
    # Exclusive can only be sold once.
    # --------------------------------------------------------------

    if sales_model == SalesModel.EXCLUSIVE.value:
        return not bool(
            getattr(
                track,
                "is_sold",
                False,
            )
        )

    # --------------------------------------------------------------
    # Unknown value.
    # --------------------------------------------------------------

    return False


def availability_reason(track: Track) -> str:
    """
    Human-readable reason used by templates/messages.
    """

    if not track:
        return "Track not found."

    if not bool(
        getattr(
            track,
            "is_published",
            False,
        )
    ):
        return "This track is not currently published."

    sales_model = track_sales_model_value(track)

    if (
        sales_model == SalesModel.EXCLUSIVE.value
        and bool(
            getattr(
                track,
                "is_sold",
                False,
            )
        )
    ):
        return "This exclusive track has already been sold."

    if sales_model not in (
        SalesModel.NON_EXCLUSIVE.value,
        SalesModel.EXCLUSIVE.value,
    ):
        return "This track has an invalid sales model."

    return ""


# ======================================================================
# HELPERS
# ======================================================================

def clean_search(value: Optional[str]) -> str:
    return (
        value or ""
    ).strip()


def track_is_visible(track: Track) -> bool:
    return bool(
        track
        and getattr(
            track,
            "is_published",
            False,
        )
    )


def find_track(
    db: Session,
    track_ref: str,
) -> Optional[Track]:

    track = (
        db.query(Track)
        .filter(
            Track.id == track_ref
        )
        .first()
    )

    if track:
        return track

    return (
        db.query(Track)
        .filter(
            Track.slug == track_ref
        )
        .first()
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

        search_pattern = (
            f"%{search}%"
        )

        query = query.filter(
            or_(
                Track.title.ilike(
                    search_pattern
                ),
                Track.genre.ilike(
                    search_pattern
                ),
                Track.tags.ilike(
                    search_pattern
                ),
                Track.description.ilike(
                    search_pattern
                ),
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

    tracks = (
        query
        .limit(100)
        .all()
    )

    # --------------------------------------------------------------
    # FEATURED
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
    # GENRES
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
    # ADD AVAILABILITY INFORMATION FOR TEMPLATE
    #
    # We do not mutate the database objects.
    # The template can use track_is_available logic through
    # the supplied availability map.
    # --------------------------------------------------------------

    availability = {
        track.id: track_is_available(track)
        for track in tracks
    }

    featured_availability = {
        track.id: track_is_available(track)
        for track in featured_tracks
    }

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
            has_results=bool(tracks),
            availability=availability,
            featured_availability=featured_availability,
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

    availability = {
        track.id: track_is_available(track)
        for track in tracks
    }

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
            availability=availability,
            featured_availability={},
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

    available = track_is_available(track)

    reason = availability_reason(track)

    return templates.TemplateResponse(
        request,
        "track_detail.html",
        ctx(
            request,
            current_user,
            track=track,
            purchased=purchased,
            available=available,
            is_available=available,
            availability_reason=reason,
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

    track_availability = {
        track.id: track_is_available(track)
        for track in tracks
    }

    return templates.TemplateResponse(
        request,
        "profile_detail.html",
        ctx(
            request,
            current_user,
            profile=profile,
            tracks=tracks,
            albums=albums,
            track_availability=track_availability,
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

    Ownership is always checked before the file is served.

    Local files:
        Served directly with FileResponse.

    R2 / S3 references:
        Detected explicitly so an R2 path is never incorrectly
        treated as a local filesystem path.
    """

    # --------------------------------------------------------------
    # FIND TRACK
    # --------------------------------------------------------------

    track = find_track(
        db,
        track_ref,
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

    stored_text = str(
        getattr(
            track,
            "audio_file_path",
            "",
        ) or ""
    ).strip()

    if not stored_text:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    # --------------------------------------------------------------
    # R2 / S3 REFERENCES
    # --------------------------------------------------------------

    if (
        stored_text.startswith("r2://")
        or stored_text.startswith("s3://")
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "This purchased track is stored in cloud "
                "storage. Configure the R2 download handler "
                "for this storage reference."
            ),
        )

    # --------------------------------------------------------------
    # LOCAL MEDIA ROOT
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
            Path.cwd()
            / media_root
        )

    media_root = media_root.resolve()

    # --------------------------------------------------------------
    # STORED LOCAL PATH
    # --------------------------------------------------------------

    stored_path = Path(
        stored_text
    )

    if stored_path.is_absolute():

        audio_path = (
            stored_path
            .resolve()
        )

    else:

        audio_path = (
            Path.cwd()
            / stored_path
        ).resolve()

    # --------------------------------------------------------------
    # COMPATIBILITY FALLBACKS
    # --------------------------------------------------------------

    if (
        not audio_path.exists()
        or not audio_path.is_file()
    ):

        filename = stored_path.name

        candidates = [
            media_root / stored_path,
            media_root / "audio" / filename,
            Path.cwd()
            / "media"
            / "audio"
            / filename,
        ]

        found = None

        for candidate in candidates:

            candidate = (
                candidate.resolve()
            )

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
        for character in str(
            track.title or ""
        )
        if (
            character.isalnum()
            or character in (
                " ",
                "-",
                "_",
            )
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
            "Cache-Control": "private, no-store",
        },
    )
