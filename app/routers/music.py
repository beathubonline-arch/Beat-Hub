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

NON-EXCLUSIVE
    published + not hidden = available
    is_sold does NOT make it unavailable

EXCLUSIVE
    published + is_sold == False = available
    published + is_sold == True  = unavailable

Downloads:
    - Existing purchased licenses are verified.
    - Local media files remain supported.
    - R2 references are supported.
    - Existing route aliases remain supported.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
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
# TEMPLATE CONTEXT
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
# SEARCH HELPERS
# ======================================================================

def clean_search(value: Optional[str]) -> str:
    return (value or "").strip()


# ======================================================================
# SALES MODEL HELPERS
# ======================================================================

def sales_model_value(track: Track) -> str:
    """
    Safely return the track's sales model as a plain string.

    Supports SQLAlchemy Enum values such as:
        SalesModel.EXCLUSIVE
        SalesModel.NON_EXCLUSIVE
    """

    value = getattr(
        track,
        "sales_model",
        None,
    )

    value = getattr(
        value,
        "value",
        value,
    )

    return str(
        value or ""
    ).strip().lower()


def is_exclusive_track(track: Track) -> bool:
    return (
        sales_model_value(track)
        == SalesModel.EXCLUSIVE.value
    )


# ======================================================================
# TRACK AVAILABILITY
# ======================================================================

def track_is_available(track: Optional[Track]) -> bool:
    """
    Central marketplace availability rule.

    IMPORTANT:

    Do NOT use:
        track.is_available

    because Track does not have an is_available database column.

    NON-EXCLUSIVE:
        published = available

    EXCLUSIVE:
        published AND not sold = available
    """

    if not track:
        return False

    # Unpublished tracks must never be purchasable publicly.
    if not bool(
        getattr(
            track,
            "is_published",
            False,
        )
    ):
        return False

    # Non-exclusive tracks can be purchased repeatedly.
    if not is_exclusive_track(track):
        return True

    # Exclusive tracks become unavailable after sale.
    return not bool(
        getattr(
            track,
            "is_sold",
            False,
        )
    )


# ======================================================================
# PUBLIC AVAILABILITY LABEL
# ======================================================================

def availability_reason(track: Track) -> str:
    """
    Human-readable availability state for templates.
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

    if is_exclusive_track(track) and bool(
        getattr(
            track,
            "is_sold",
            False,
        )
    ):
        return "This exclusive track has already been sold."

    return "Available for purchase."


# ======================================================================
# FIND TRACK
# ======================================================================

def get_track(
    db: Session,
    slug: str,
) -> Optional[Track]:

    return (
        db.query(Track)
        .filter(
            Track.slug == slug
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
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
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
    # ADD AVAILABILITY INFORMATION
    # --------------------------------------------------------------

    available_track_ids = {
        track.id
        for track in tracks
        if track_is_available(track)
    }

    # --------------------------------------------------------------
    # FEATURED
    # --------------------------------------------------------------

    featured_tracks = []

    if (
        not search
        and not selected_genre
    ):

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
        db.query(
            Track.genre
        )
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
            available_track_ids=available_track_ids,
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
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
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
            available_track_ids={
                track.id
                for track in tracks
                if track_is_available(track)
            },
            title="Hot Picks",
        ),
    )


# ======================================================================
# SESSIONS
# ======================================================================

@router.get("/sessions")
def sessions_page(
    request: Request,
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
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
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):

    track = get_track(
        db,
        slug,
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found",
        )

    # --------------------------------------------------------------
    # AVAILABILITY
    # --------------------------------------------------------------

    available = track_is_available(
        track
    )

    # --------------------------------------------------------------
    # PURCHASE CHECK
    # --------------------------------------------------------------

    purchased = False

    if current_user:

        purchased = (
            db.query(License)
            .join(
                Order,
                License.order_id
                == Order.id,
            )
            .filter(
                License.buyer_id
                == current_user.id,
                License.track_id
                == track.id,
                Order.status
                == OrderStatus.COMPLETED,
            )
            .first()
            is not None
        )

    # --------------------------------------------------------------
    # OWNER CHECK
    # --------------------------------------------------------------

    is_owner = False

    profile = getattr(
        track,
        "creator_profile",
        None,
    )

    if profile and current_user:

        creator_user_id = getattr(
            profile,
            "user_id",
            None,
        )

        is_owner = (
            creator_user_id
            == current_user.id
        )

    return templates.TemplateResponse(
        request,
        "track_detail.html",
        ctx(
            request,
            current_user,
            track=track,

            # IMPORTANT:
            # The template must use these values,
            # NOT track.is_available.
            available=available,
            track_available=available,
            availability_reason=availability_reason(
                track
            ),

            purchased=purchased,
            is_owner=is_owner,
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
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
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
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
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
    user: User = Depends(
        require_user
    ),
):
    """
    Secure purchased-track download.

    Supports:
        /download/track/{track_id}
        /download/{track_id}
        /download/track/{slug}
        /download/{slug}

    Ownership must exist through a COMPLETED order.
    """

    # --------------------------------------------------------------
    # FIND BY ID
    # --------------------------------------------------------------

    track = (
        db.query(Track)
        .filter(
            Track.id == track_ref
        )
        .first()
    )

    # --------------------------------------------------------------
    # FALLBACK TO SLUG
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
    # VERIFY PURCHASE
    # --------------------------------------------------------------

    license_record = (
        db.query(License)
        .join(
            Order,
            License.order_id
            == Order.id,
        )
        .filter(
            License.buyer_id
            == user.id,
            License.track_id
            == track.id,
            Order.status
            == OrderStatus.COMPLETED,
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

    stored_text = str(
        track.audio_file_path
    ).strip()

    # --------------------------------------------------------------
    # R2 PUBLIC HTTPS URL
    # --------------------------------------------------------------

    if stored_text.startswith(
        "https://"
    ) or stored_text.startswith(
        "http://"
    ):

        return RedirectResponse(
            url=stored_text,
            status_code=307,
        )

    # --------------------------------------------------------------
    # R2 URI
    #
    # Example:
    # r2://beathub-r2/audio/file.mp3
    #
    # This cannot be served using FileResponse.
    # Try the application's R2 helper if one exists.
    # --------------------------------------------------------------

    if (
        stored_text.startswith("r2://")
        or stored_text.startswith("s3://")
    ):

        # Look for an existing application-level R2
        # signed URL helper without breaking installations
        # where that helper has not been imported.
        try:

            from app.services import storage

        except ImportError:

            storage = None

        if storage:

            for function_name in (
                "create_download_url",
                "generate_download_url",
                "get_download_url",
                "presigned_download_url",
            ):

                helper = getattr(
                    storage,
                    function_name,
                    None,
                )

                if helper:

                    try:

                        result = helper(
                            stored_text
                        )

                        if result:

                            return RedirectResponse(
                                url=str(result),
                                status_code=307,
                            )

                    except TypeError:

                        try:

                            result = helper(
                                stored_text,
                                expires_in=900,
                            )

                            if result:

                                return RedirectResponse(
                                    url=str(result),
                                    status_code=307,
                                )

                        except Exception:
                            pass

                    except Exception:
                        pass

        raise HTTPException(
            status_code=500,
            detail=(
                "This purchased track is stored in R2, "
                "but the R2 download signer is not configured."
            ),
        )

    # --------------------------------------------------------------
    # LOCAL FILE SUPPORT
    # --------------------------------------------------------------

    stored_path = Path(
        stored_text
    )

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
    # EXACT STORED PATH
    # --------------------------------------------------------------

    if stored_path.is_absolute():

        audio_path = (
            stored_path.resolve()
        )

    else:

        audio_path = (
            Path.cwd()
            / stored_path
        ).resolve()

    # --------------------------------------------------------------
    # COMPATIBILITY PATHS
    # --------------------------------------------------------------

    if (
        not audio_path.exists()
        or not audio_path.is_file()
    ):

        filename = (
            stored_path.name
        )

        candidates = [
            media_root / stored_path,
            media_root / "audio" / filename,
            (
                Path.cwd()
                / "media"
                / "audio"
                / filename
            ),
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
    # DOWNLOAD NAME
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

        safe_title = (
            "BeatHub-Track"
        )

    extension = (
        audio_path.suffix.lower()
    )

    download_name = (
        f"{safe_title}{extension}"
        if extension
        else safe_title
    )

    # --------------------------------------------------------------
    # FILE RESPONSE
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
