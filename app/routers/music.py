import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Album, Track
from app.models.order import License, Order, OrderStatus
from app.models.profile import Profile
from app.models.user import User
from app.utils.deps import get_optional_user, require_user


router = APIRouter(tags=["music"])

templates = Jinja2Templates(
    directory="app/templates"
)


# ======================================================================
# CONTEXT
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
# R2 HELPERS
# ======================================================================

def get_setting_or_env(
    name: str,
    default=None,
):
    """
    Reads a setting from app.config.settings first,
    then falls back to the environment.
    """

    value = getattr(
        settings,
        name,
        None,
    )

    if value is not None:
        return value

    return os.getenv(
        name,
        default,
    )


def parse_r2_reference(
    stored_value: str,
):
    """
    Supports:

        r2://bucket/key.mp3
        s3://bucket/key.mp3

    Returns:

        bucket, key
    """

    value = (
        stored_value or ""
    ).strip()

    parsed = urlparse(
        value
    )

    if parsed.scheme not in {
        "r2",
        "s3",
    }:
        return None, None

    bucket = (
        parsed.netloc or ""
    ).strip()

    key = (
        parsed.path or ""
    ).lstrip("/")

    if not bucket or not key:
        return None, None

    return bucket, key


def is_r2_reference(
    stored_value: str,
) -> bool:

    value = (
        stored_value or ""
    ).strip().lower()

    return (
        value.startswith(
            "r2://"
        )
        or value.startswith(
            "s3://"
        )
    )


def get_r2_bucket_from_settings():
    return (
        get_setting_or_env(
            "R2_BUCKET_NAME"
        )
        or get_setting_or_env(
            "R2_BUCKET"
        )
        or get_setting_or_env(
            "R2_BUCKET_NAME"
        )
        or "beathub-r2"
    )


def get_r2_endpoint():
    """
    Cloudflare R2 S3 endpoint.

    Preferred Render variable:

        R2_ENDPOINT

    Example shape:

        https://<account-id>.r2.cloudflarestorage.com
    """

    endpoint = (
        get_setting_or_env(
            "R2_ENDPOINT"
        )
        or get_setting_or_env(
            "S3_ENDPOINT_URL"
        )
    )

    if endpoint:
        return str(
            endpoint
        ).rstrip("/")

    account_id = (
        get_setting_or_env(
            "R2_ACCOUNT_ID"
        )
    )

    if account_id:
        return (
            "https://"
            f"{account_id}"
            ".r2.cloudflarestorage.com"
        )

    return None


def create_r2_presigned_url(
    stored_value: str,
    expires_seconds: int = 300,
):
    """
    Creates a short-lived private download URL.

    Uses boto3 only when an R2 object is actually being downloaded,
    so local-media functionality is unaffected.
    """

    bucket, key = parse_r2_reference(
        stored_value
    )

    if not bucket or not key:
        bucket = get_r2_bucket_from_settings()

        key = (
            stored_value or ""
        ).strip().lstrip("/")

    endpoint = get_r2_endpoint()

    access_key = (
        get_setting_or_env(
            "R2_ACCESS_KEY_ID"
        )
        or get_setting_or_env(
            "AWS_ACCESS_KEY_ID"
        )
    )

    secret_key = (
        get_setting_or_env(
            "R2_SECRET_ACCESS_KEY"
        )
        or get_setting_or_env(
            "AWS_SECRET_ACCESS_KEY"
        )
    )

    if not endpoint:
        raise RuntimeError(
            "R2_ENDPOINT is not configured."
        )

    if not access_key:
        raise RuntimeError(
            "R2_ACCESS_KEY_ID is not configured."
        )

    if not secret_key:
        raise RuntimeError(
            "R2_SECRET_ACCESS_KEY is not configured."
        )

    try:

        import boto3

    except ImportError:

        raise RuntimeError(
            "boto3 is required for R2 downloads. "
            "Add boto3 to requirements.txt and redeploy."
        )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )

    response = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
        },
        ExpiresIn=expires_seconds,
    )

    return response


# ======================================================================
# SAFE DOWNLOAD NAME
# ======================================================================

def safe_download_name(
    track: Track,
    extension: str = ".mp3",
):
    title = (
        getattr(
            track,
            "title",
            None,
        )
        or "BeatHub-Track"
    )

    safe_title = "".join(
        character
        for character in title
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
        extension or ".mp3"
    ).strip()

    if not extension.startswith("."):
        extension = (
            "." + extension
        )

    return (
        f"{safe_title}"
        f"{extension.lower()}"
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

    The buyer MUST have a completed License.

    R2 files are returned through a short-lived presigned URL.
    Local files continue using FileResponse.
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
            detail=(
                "You do not own this track."
            ),
        )

    # --------------------------------------------------------------
    # AUDIO PATH
    # --------------------------------------------------------------

    stored_value = (
        getattr(
            track,
            "audio_file_path",
            None,
        )
        or ""
    ).strip()

    if not stored_value:
        raise HTTPException(
            status_code=404,
            detail=(
                "Audio file is not available."
            ),
        )

    # ==============================================================
    # R2 / CLOUD STORAGE
    # ==============================================================

    if is_r2_reference(
        stored_value
    ):

        try:

            extension = (
                Path(
                    urlparse(
                        stored_value
                    ).path
                ).suffix
                or ".mp3"
            )

            download_url = (
                create_r2_presigned_url(
                    stored_value,
                    expires_seconds=300,
                )
            )

        except Exception as exc:

            print(
                "[BeatHub] R2 download error:",
                repr(exc),
            )

            raise HTTPException(
                status_code=500,
                detail=(
                    "The purchased beat is stored "
                    "in cloud storage, but BeatHub "
                    "could not create the download link."
                ),
            )

        # ----------------------------------------------------------
        # Browser follows the secure temporary R2 URL.
        # ----------------------------------------------------------

        return RedirectResponse(
            url=download_url,
            status_code=307,
            headers={
                "Cache-Control": (
                    "private, no-store"
                ),
            },
        )

    # ==============================================================
    # LOCAL FILE STORAGE
    # ==============================================================

    stored_path = Path(
        stored_value
    )

    media_root_value = (
        get_setting_or_env(
            "MEDIA_ROOT",
            "media",
        )
    )

    media_root = Path(
        str(media_root_value)
    )

    if not media_root.is_absolute():

        media_root = (
            Path.cwd()
            / media_root
        )

    media_root = (
        media_root.resolve()
    )

    # --------------------------------------------------------------
    # FIRST TRY EXACT DATABASE PATH
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
    # SECURITY: NEVER SERVE OUTSIDE MEDIA_ROOT
    # --------------------------------------------------------------

    try:

        audio_path.relative_to(
            media_root
        )

    except ValueError:

        raise HTTPException(
            status_code=403,
            detail=(
                "Invalid audio file location."
            ),
        )

    if not audio_path.is_file():

        raise HTTPException(
            status_code=404,
            detail=(
                "Audio file is unavailable."
            ),
        )

    # --------------------------------------------------------------
    # DOWNLOAD NAME
    # --------------------------------------------------------------

    download_name = (
        safe_download_name(
            track,
            audio_path.suffix,
        )
    )

    # --------------------------------------------------------------
    # LOCAL DOWNLOAD
    # --------------------------------------------------------------

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
                f'attachment; '
                f'filename="{download_name}"'
            ),
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )
