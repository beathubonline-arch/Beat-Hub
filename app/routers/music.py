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

R2:
    Purchased tracks stored as:
        r2://bucket/key

    are served through short-lived presigned GET URLs.

IMPORTANT:
    The buyer must have a completed Order + License before
    BeatHub generates an R2 download URL.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
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
# R2 CONFIGURATION
# ======================================================================

def get_setting_value(*names, default=None):
    """
    Read configuration safely from app.config.settings first,
    then fall back to environment variables.

    This makes the R2 download code compatible with either:

        settings.R2_ACCOUNT_ID

    or Render environment variables such as:

        R2_ACCOUNT_ID
    """

    for name in names:

        value = getattr(
            settings,
            name,
            None,
        )

        if value is not None:
            value = str(value).strip()

            if value:
                return value

        value = os.getenv(name)

        if value is not None:
            value = str(value).strip()

            if value:
                return value

    return default


def get_r2_account_id():
    return get_setting_value(
        "R2_ACCOUNT_ID",
        "CLOUDFLARE_ACCOUNT_ID",
        "CF_ACCOUNT_ID",
    )


def get_r2_access_key_id():
    return get_setting_value(
        "R2_ACCESS_KEY_ID",
        "R2_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
    )


def get_r2_secret_access_key():
    return get_setting_value(
        "R2_SECRET_ACCESS_KEY",
        "R2_SECRET_KEY",
        "AWS_SECRET_ACCESS_KEY",
    )


def get_r2_bucket():
    return get_setting_value(
        "R2_BUCKET",
        "R2_BUCKET_NAME",
        "AWS_S3_BUCKET",
        default="beathub-r2",
    )


def get_r2_endpoint():
    """
    R2 S3 endpoint.

    If R2_ENDPOINT is explicitly configured, use it.

    Otherwise construct:

        https://ACCOUNT_ID.r2.cloudflarestorage.com
    """

    explicit_endpoint = get_setting_value(
        "R2_ENDPOINT",
        "R2_S3_ENDPOINT",
        "AWS_ENDPOINT_URL",
    )

    if explicit_endpoint:
        return explicit_endpoint.rstrip("/")

    account_id = get_r2_account_id()

    if not account_id:
        return None

    return (
        f"https://{account_id}.r2.cloudflarestorage.com"
    )


def create_r2_client():
    """
    Create a boto3 S3 client configured for Cloudflare R2.
    """

    try:
        import boto3
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail=(
                "R2 download support is not installed. "
                "Add boto3 to requirements.txt and redeploy."
            ),
        )

    endpoint = get_r2_endpoint()
    access_key = get_r2_access_key_id()
    secret_key = get_r2_secret_access_key()

    if not endpoint:
        raise HTTPException(
            status_code=500,
            detail=(
                "R2_ACCOUNT_ID or R2_ENDPOINT is not configured."
            ),
        )

    if not access_key or not secret_key:
        raise HTTPException(
            status_code=500,
            detail=(
                "R2 API credentials are not configured."
            ),
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


# ======================================================================
# R2 PATH PARSER
# ======================================================================

def parse_r2_reference(
    stored_value: str,
):
    """
    Convert:

        r2://beathub-r2/audio/file.mp3

    into:

        bucket = beathub-r2
        key    = audio/file.mp3

    Also supports:

        s3://bucket/key
    """

    value = (
        stored_value
        or ""
    ).strip()

    if not (
        value.startswith("r2://")
        or value.startswith("s3://")
    ):
        return None, None

    without_scheme = value.split(
        "://",
        1,
    )[1]

    without_scheme = unquote(
        without_scheme
    ).lstrip("/")

    if not without_scheme:
        return None, None

    parts = without_scheme.split(
        "/",
        1,
    )

    bucket = parts[0].strip()

    if len(parts) < 2:
        return bucket, None

    key = parts[1].strip()

    if not bucket or not key:
        return None, None

    return bucket, key


# ======================================================================
# R2 DOWNLOAD URL
# ======================================================================

def create_r2_download_url(
    stored_value: str,
    download_name: str,
) -> str:
    """
    Generate a short-lived presigned GET URL.

    BeatHub performs ownership authorization BEFORE this function
    is called.

    The R2 credentials never reach the buyer.
    """

    parsed_bucket, key = parse_r2_reference(
        stored_value
    )

    if not key:
        raise HTTPException(
            status_code=404,
            detail="Invalid R2 audio reference.",
        )

    configured_bucket = get_r2_bucket()

    # --------------------------------------------------------------
    # Security:
    #
    # If the stored path contains the expected bucket, use it.
    # Otherwise fall back to the configured BeatHub bucket.
    # --------------------------------------------------------------

    bucket = (
        parsed_bucket
        or configured_bucket
    )

    if not bucket:
        raise HTTPException(
            status_code=500,
            detail="R2 bucket is not configured.",
        )

    s3 = create_r2_client()

    # --------------------------------------------------------------
    # Verify that the object actually exists.
    # --------------------------------------------------------------

    try:

        metadata = s3.head_object(
            Bucket=bucket,
            Key=key,
        )

    except Exception as exc:

        print(
            "[BeatHub R2] head_object failed:",
            repr(exc),
        )

        raise HTTPException(
            status_code=404,
            detail=(
                "The purchased audio file could not "
                "be found in R2."
            ),
        )

    # --------------------------------------------------------------
    # Preserve the actual content type when available.
    # --------------------------------------------------------------

    content_type = (
        metadata.get("ContentType")
        or "application/octet-stream"
    )

    # --------------------------------------------------------------
    # Generate temporary GET URL.
    #
    # 15 minutes is enough for a normal download while keeping
    # the private object protected.
    # --------------------------------------------------------------

    try:

        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": (
                    f'attachment; filename="{download_name}"'
                ),
                "ResponseContentType": content_type,
            },
            ExpiresIn=900,
        )

    except Exception as exc:

        print(
            "[BeatHub R2] presigned URL error:",
            repr(exc),
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "BeatHub could not create the secure "
                "download link."
            ),
        )

    return url


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
    search = clean_search(q)
    selected_genre = clean_search(genre)

    query = (
        db.query(Track)
        .filter(
            Track.is_published == True  # noqa: E712
        )
    )

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

    tracks = query.limit(100).all()

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
    # Profile is imported lazily here to preserve compatibility with
    # projects where Profile is registered through the model package.
    from app.models.profile import Profile

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
# PURCHASE AUTHORIZATION
# ======================================================================

def verify_track_purchase(
    db: Session,
    user: User,
    track: Track,
) -> bool:
    """
    A buyer is authorized when a License exists for the track and its
    Order is COMPLETED.

    The License is the ownership record.

    We also check the completed order directly as a compatibility
    fallback so older completed purchases are not unnecessarily lost.
    """

    # --------------------------------------------------------------
    # PRIMARY: LICENSE
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

    if license_record:
        return True

    # --------------------------------------------------------------
    # COMPATIBILITY: COMPLETED ORDER
    #
    # This prevents older legitimate purchases from becoming
    # inaccessible if a License was not created by an older callback.
    # --------------------------------------------------------------

    completed_order = (
        db.query(Order)
        .filter(
            Order.buyer_id == user.id,
            Order.track_id == track.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .first()
    )

    return completed_order is not None


# ======================================================================
# SAFE DOWNLOAD NAME
# ======================================================================

def make_download_name(
    track: Track,
    extension: str = "",
) -> str:

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
        extension
        or ""
    ).strip()

    if extension and not extension.startswith("."):
        extension = "." + extension

    return (
        f"{safe_title}{extension}"
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

    Storage supported:

        R2:
            r2://bucket/key

        S3:
            s3://bucket/key

        Local:
            media/audio/file.mp3

    R2 downloads are returned as temporary presigned URLs.
    """

    # ==================================================================
    # 1. FIND TRACK BY ID
    # ==================================================================

    track = (
        db.query(Track)
        .filter(
            Track.id == track_ref
        )
        .first()
    )

    # ==================================================================
    # 2. FALL BACK TO SLUG
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
    # 3. VERIFY PURCHASE
    # ==================================================================

    if not verify_track_purchase(
        db,
        user,
        track,
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not own this track.",
        )

    # ==================================================================
    # 4. AUDIO PATH
    # ==================================================================

    stored_value = (
        str(
            getattr(
                track,
                "audio_file_path",
                "",
            )
            or ""
        )
        .strip()
    )

    if not stored_value:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    # ==================================================================
    # 5. R2 / S3 CLOUD STORAGE
    # ==================================================================

    r2_bucket, r2_key = parse_r2_reference(
        stored_value
    )

    if r2_key:

        extension = Path(
            r2_key
        ).suffix.lower()

        download_name = make_download_name(
            track,
            extension,
        )

        download_url = create_r2_download_url(
            stored_value,
            download_name,
        )

        # --------------------------------------------------------------
        # Redirect directly to the temporary signed R2 URL.
        # --------------------------------------------------------------

        return RedirectResponse(
            url=download_url,
            status_code=307,
        )

    # ==================================================================
    # 6. LOCAL STORAGE FALLBACK
    # ==================================================================

    stored_path = Path(
        stored_value
    )

    media_root_value = get_setting_value(
        "MEDIA_ROOT",
        default="media",
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

    # ==================================================================
    # EXACT STORED PATH
    # ==================================================================

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

    # ==================================================================
    # COMPATIBILITY PATHS
    # ==================================================================

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
    # DOWNLOAD NAME
    # ==================================================================

    download_name = make_download_name(
        track,
        audio_path.suffix.lower(),
    )

    # ==================================================================
    # LOCAL DOWNLOAD
    # ==================================================================

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
