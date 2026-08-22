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

Purchased audio can be stored:
    - locally
    - r2://bucket/key
    - s3://bucket/key

R2/S3 objects are returned through a short-lived signed URL.
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
from app.models.music import Album, Track
from app.models.order import (
    ExclusiveOwnershipLock,
    License,
    Order,
    OrderStatus,
)
from app.models.profile import Profile
from app.models.user import User
from app.utils.deps import get_optional_user, require_user


router = APIRouter(
    tags=["music"],
)

templates = Jinja2Templates(
    directory="app/templates"
)


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

def clean_search(
    value: Optional[str],
) -> str:
    return (
        value or ""
    ).strip()


def track_is_visible(
    track: Track,
) -> bool:
    return bool(
        track
        and track.is_published
    )


def is_exclusive_track(
    track: Track,
) -> bool:
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

    return (
        str(value or "")
        .strip()
        .lower()
        == "exclusive"
    )


def track_is_available(
    track: Track,
) -> bool:
    """
    Buyer-facing availability.

    NON-EXCLUSIVE:
        published = available

    EXCLUSIVE:
        published + not sold = available
    """

    if not track:
        return False

    if not bool(
        getattr(
            track,
            "is_published",
            False,
        )
    ):
        return False

    if is_exclusive_track(track):
        return not bool(
            getattr(
                track,
                "is_sold",
                False,
            )
        )

    return True


def get_r2_setting(
    *names,
):
    """
    Safely obtain an R2 setting from BeatHub settings.

    Supports multiple naming conventions so this does not break
    an existing deployment merely because the settings object uses
    slightly different names.
    """

    for name in names:

        value = getattr(
            settings,
            name,
            None,
        )

        if value is not None:
            value = str(
                value
            ).strip()

            if value:
                return value

    return None


def parse_r2_reference(
    value: str,
):
    """
    Parse:

        r2://bucket/key/file.mp3

    or:

        s3://bucket/key/file.mp3

    Returns:

        bucket, key

    """

    value = (
        value or ""
    ).strip()

    if value.startswith(
        "r2://"
    ):
        remainder = value[5:]

    elif value.startswith(
        "s3://"
    ):
        remainder = value[5:]

    else:
        return None, None

    remainder = remainder.lstrip("/")

    if "/" not in remainder:
        return (
            remainder,
            "",
        )

    bucket, key = remainder.split(
        "/",
        1,
    )

    return (
        bucket,
        key,
    )


def create_r2_download_url(
    stored_reference: str,
    expires: int = 900,
) -> Optional[str]:
    """
    Create a temporary signed URL for an R2 object.

    Preferred implementation:
        boto3 / botocore

    This uses the standard S3-compatible R2 API.

    Environment/settings supported:

        R2_ENDPOINT
        R2_ENDPOINT_URL
        CLOUDFLARE_R2_ENDPOINT

        R2_ACCESS_KEY_ID
        AWS_ACCESS_KEY_ID

        R2_SECRET_ACCESS_KEY
        AWS_SECRET_ACCESS_KEY

        R2_BUCKET
        R2_BUCKET_NAME
        AWS_S3_BUCKET

    The database value itself may also contain the bucket.
    """

    bucket_from_path, object_key = (
        parse_r2_reference(
            stored_reference
        )
    )

    if not object_key:
        return None

    bucket = (
        bucket_from_path
        or get_r2_setting(
            "R2_BUCKET",
            "R2_BUCKET_NAME",
            "AWS_S3_BUCKET",
        )
    )

    endpoint = get_r2_setting(
        "R2_ENDPOINT",
        "R2_ENDPOINT_URL",
        "CLOUDFLARE_R2_ENDPOINT",
    )

    access_key = get_r2_setting(
        "R2_ACCESS_KEY_ID",
        "AWS_ACCESS_KEY_ID",
    )

    secret_key = get_r2_setting(
        "R2_SECRET_ACCESS_KEY",
        "AWS_SECRET_ACCESS_KEY",
    )

    if not bucket:
        raise RuntimeError(
            "R2 bucket is not configured."
        )

    if not endpoint:
        raise RuntimeError(
            "R2 endpoint is not configured."
        )

    if not access_key:
        raise RuntimeError(
            "R2 access key is not configured."
        )

    if not secret_key:
        raise RuntimeError(
            "R2 secret access key is not configured."
        )

    try:
        import boto3
        from botocore.client import Config

    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for R2 downloads. "
            "Add boto3 to requirements.txt."
        ) from exc

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4"
        ),
    )

    filename = Path(
        object_key
    ).name

    response = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": object_key,
            "ResponseContentDisposition": (
                f'attachment; filename="{filename}"'
            ),
        },
        ExpiresIn=expires,
        HttpMethod="GET",
    )

    return response


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
    selected_genre = clean_search(
        genre
    )

    query = (
        db.query(Track)
        .filter(
            Track.is_published == True  # noqa: E712
        )
    )

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

    if selected_genre:

        query = query.filter(
            Track.genre.ilike(
                selected_genre
            )
        )

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

    available = (
        track_is_available(
            track
        )
        or purchased
    )

    return templates.TemplateResponse(
        request,
        "track_detail.html",
        ctx(
            request,
            current_user,
            track=track,
            purchased=purchased,
            available=available,
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

    Ownership is checked BEFORE any storage URL is generated.
    """

    # ==================================================================
    # FIND TRACK BY ID
    # ==================================================================

    track = (
        db.query(Track)
        .filter(
            Track.id == track_ref
        )
        .first()
    )

    # ==================================================================
    # FALL BACK TO SLUG
    # ==================================================================

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

    # ==================================================================
    # VERIFY OWNERSHIP
    # ==================================================================

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

    # ==================================================================
    # AUDIO REFERENCE
    # ==================================================================

    stored_text = str(
        track.audio_file_path
        or ""
    ).strip()

    if not stored_text:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    # ==================================================================
    # R2 / S3
    # ==================================================================

    if (
        stored_text.startswith(
            "r2://"
        )
        or stored_text.startswith(
            "s3://"
        )
    ):

        try:

            signed_url = (
                create_r2_download_url(
                    stored_text,
                    expires=900,
                )
            )

        except Exception as exc:

            # Do not expose credentials or internal storage details.
            raise HTTPException(
                status_code=500,
                detail=(
                    "The purchased audio is stored in cloud storage, "
                    "but the download service could not create a "
                    "secure download link."
                ),
            ) from exc

        if not signed_url:

            raise HTTPException(
                status_code=404,
                detail=(
                    "The purchased audio file "
                    "could not be located."
                ),
            )

        return RedirectResponse(
            url=signed_url,
            status_code=307,
        )

    # ==================================================================
    # LOCAL STORAGE COMPATIBILITY
    # ==================================================================

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

    media_root = (
        media_root
        .resolve()
    )

    # ==================================================================
    # EXACT PATH
    # ==================================================================

    if stored_path.is_absolute():

        audio_path = (
            stored_path.resolve()
        )

    else:

        audio_path = (
            Path.cwd()
            / stored_path
        ).resolve()

    # ==================================================================
    # COMPATIBILITY PATHS
    # ==================================================================

    if (
        not audio_path.exists()
        or not audio_path.is_file()
    ):

        filename = (
            stored_path.name
        )

        candidates = [
            media_root
            / stored_path,

            media_root
            / "audio"
            / filename,

            Path.cwd()
            / "media"
            / "audio"
            / filename,
        ]

        found = None

        for candidate in candidates:

            candidate = (
                candidate
                .resolve()
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

    # ==================================================================
    # SECURITY
    # ==================================================================

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

    # ==================================================================
    # SAFE DOWNLOAD NAME
    # ==================================================================

    title = (
        track.title
        or "BeatHub-Track"
    )

    safe_title = "".join(
        character
        for character in title
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

    # ==================================================================
    # LOCAL DOWNLOAD
    # ==================================================================

    return FileResponse(
        path=str(
            audio_path
        ),
        filename=download_name,
        media_type=(
            "application/octet-stream"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{download_name}"'
            ),
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )
