"""
BeatHub music and marketplace routes.

PUBLIC:
    /beats
    /hot-picks
    /sessions
    /track/{slug}
    /album/{slug}
    /profile/{slug}

BUYER:
    /purchases

PROTECTED DOWNLOADS:
    /download/{track_ref}
    /download/track/{track_ref}

STORAGE SUPPORT:
    - Local filesystem
    - r2://bucket/key
    - s3://bucket/key
    - Full HTTPS R2/public URLs

IMPORTANT:
    Payment completion and ownership are controlled by License + Order.
    A buyer can only download a track after:
        License.buyer_id == current user
        License.track_id == requested track
        Order.status == COMPLETED
"""
 
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError

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
# GENERAL HELPERS
# ======================================================================

def clean_search(value: Optional[str]) -> str:
    return (value or "").strip()


def track_is_visible(track: Track) -> bool:
    return bool(
        track
        and track.is_published
    )


def safe_filename(value: str) -> str:
    """
    Creates a safe download filename.
    """

    value = value or "BeatHub-Track"

    value = "".join(
        character
        for character in value
        if character.isalnum()
        or character in (
            " ",
            "-",
            "_",
        )
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    ).strip()

    return value or "BeatHub-Track"


# ======================================================================
# R2 CONFIGURATION
# ======================================================================

def _setting(name: str, default=None):
    """
    Read a setting safely from app.config.settings first,
    then fall back to the environment.

    This keeps this router compatible with different BeatHub
    configuration versions.
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


def r2_account_id() -> Optional[str]:
    return (
        _setting("R2_ACCOUNT_ID")
        or os.getenv("CLOUDFLARE_ACCOUNT_ID")
    )


def r2_access_key() -> Optional[str]:
    return (
        _setting("R2_ACCESS_KEY_ID")
        or os.getenv("AWS_ACCESS_KEY_ID")
    )


def r2_secret_key() -> Optional[str]:
    return (
        _setting("R2_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SECRET_ACCESS_KEY")
    )


def r2_default_bucket() -> Optional[str]:
    return (
        _setting("R2_BUCKET_NAME")
        or _setting("R2_BUCKET")
        or os.getenv("R2_BUCKET_NAME")
        or os.getenv("R2_BUCKET")
    )


def r2_endpoint_url() -> Optional[str]:
    configured = (
        _setting("R2_ENDPOINT_URL")
        or os.getenv("R2_ENDPOINT_URL")
    )

    if configured:
        return str(configured).rstrip("/")

    account_id = r2_account_id()

    if account_id:
        return (
            f"https://{account_id}.r2.cloudflarestorage.com"
        )

    return None


# ======================================================================
# STORAGE REFERENCE PARSING
# ======================================================================

def parse_r2_reference(
    stored_value: str,
):
    """
    Parse:

        r2://bucket/key
        s3://bucket/key

    Returns:

        {
            "bucket": "...",
            "key": "..."
        }

    or None if the value isn't an R2/S3 URI.
    """

    value = (
        stored_value or ""
    ).strip()

    if not value:
        return None

    lower = value.lower()

    if not (
        lower.startswith("r2://")
        or lower.startswith("s3://")
    ):
        return None

    parsed = urlparse(
        value
    )

    bucket = (
        parsed.netloc
        or ""
    ).strip()

    key = (
        parsed.path or ""
    ).lstrip("/")

    if not bucket or not key:
        return None

    return {
        "bucket": bucket,
        "key": key,
    }


# ======================================================================
# FULL PUBLIC URL DETECTION
# ======================================================================

def is_http_url(
    value: str,
) -> bool:
    value = (
        value or ""
    ).strip()

    return (
        value.startswith("https://")
        or value.startswith("http://")
    )


# ======================================================================
# R2 CLIENT
# ======================================================================

def create_r2_client():
    """
    Creates an S3-compatible boto3 client for Cloudflare R2.
    """

    endpoint = r2_endpoint_url()
    access_key = r2_access_key()
    secret_key = r2_secret_key()

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
            "R2 secret key is not configured."
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


# ======================================================================
# R2 PRESIGNED DOWNLOAD
# ======================================================================

def create_r2_download_url(
    bucket: str,
    key: str,
    download_name: str,
) -> str:
    """
    Creates a temporary signed download URL.

    The buyer never receives the R2 credentials.
    """

    client = create_r2_client()

    return client.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": (
                f'attachment; filename="{download_name}"'
            ),
        },
        ExpiresIn=15 * 60,
    )


# ======================================================================
# R2 URL FALLBACK
# ======================================================================

def create_r2_url_from_default_bucket(
    stored_value: str,
    download_name: str,
):
    """
    Supports configurations where the database stores only:

        audio/file.mp3

    while R2_BUCKET_NAME contains the bucket.
    """

    bucket = r2_default_bucket()

    if not bucket:
        return None

    key = (
        stored_value or ""
    ).strip().lstrip("/")

    if not key:
        return None

    try:
        return create_r2_download_url(
            bucket=bucket,
            key=key,
            download_name=download_name,
        )

    except (
        RuntimeError,
        BotoCoreError,
        ClientError,
    ):
        return None


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

    has_results = bool(
        tracks
    )

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
                License.order_id == Order.id,
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
# BUYER PURCHASES
# ======================================================================

@router.get("/purchases")
def purchases_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """
    Buyer's library.

    Only COMPLETED orders with a valid License are displayed as
    purchased music.

    Pending/failed/rejected payment attempts are not treated as ownership.
    """

    licenses = (
        db.query(License)
        .join(
            Order,
            License.order_id == Order.id,
        )
        .filter(
            License.buyer_id == user.id,
            Order.buyer_id == user.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .order_by(
            License.granted_at.desc()
        )
        .all()
    )

    purchases = []

    for license_record in licenses:

        track = None

        if license_record.track_id:

            track = (
                db.query(Track)
                .filter(
                    Track.id
                    == license_record.track_id
                )
                .first()
            )

        purchases.append(
            {
                "license": license_record,
                "order": license_record.order,
                "track": track,
            }
        )

    return templates.TemplateResponse(
        request,
        "purchases.html",
        ctx(
            request,
            user,
            purchases=purchases,
            has_purchases=bool(
                purchases
            ),
            title="My Purchases",
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

    Supported references:

        /download/track/{track_id}
        /download/{track_id}

        /download/track/{slug}
        /download/{slug}

    Storage supported:

        local filesystem
        r2://bucket/key
        s3://bucket/key
        HTTPS public R2 URL

    IMPORTANT:

    Ownership is checked BEFORE any storage URL is returned.
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
    # VERIFY LICENSE / OWNERSHIP
    # ==================================================================

    license_record = (
        db.query(License)
        .join(
            Order,
            License.order_id == Order.id,
        )
        .filter(
            License.buyer_id == user.id,
            License.track_id == track.id,
            Order.buyer_id == user.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .first()
    )

    if not license_record:
        raise HTTPException(
            status_code=403,
            detail="You do not own this track.",
        )

    # ==================================================================
    # AUDIO PATH
    # ==================================================================

    stored_text = (
        str(
            track.audio_file_path
            or ""
        )
        .strip()
    )

    if not stored_text:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    safe_title_value = safe_filename(
        track.title
    )

    # ==================================================================
    # R2://bucket/key
    # ==================================================================

    r2_reference = parse_r2_reference(
        stored_text
    )

    if r2_reference:

        try:

            download_name = (
                f"{safe_title_value}"
                f"{Path(r2_reference['key']).suffix}"
            )

            signed_url = (
                create_r2_download_url(
                    bucket=r2_reference[
                        "bucket"
                    ],
                    key=r2_reference[
                        "key"
                    ],
                    download_name=download_name,
                )
            )

            return RedirectResponse(
                url=signed_url,
                status_code=302,
            )

        except (
            RuntimeError,
            BotoCoreError,
            ClientError,
        ) as exc:

            print(
                "[BeatHub] R2 download error:",
                repr(exc),
            )

            raise HTTPException(
                status_code=503,
                detail=(
                    "The purchased audio is stored "
                    "in cloud storage, but the download "
                    "service is temporarily unavailable."
                ),
            )

    # ==================================================================
    # FULL PUBLIC HTTPS URL
    # ==================================================================

    if is_http_url(
        stored_text
    ):

        parsed = urlparse(
            stored_text
        )

        extension = (
            Path(
                parsed.path
            ).suffix.lower()
        )

        download_name = (
            f"{safe_title_value}"
            f"{extension}"
            if extension
            else safe_title_value
        )

        return RedirectResponse(
            url=stored_text,
            status_code=302,
            headers={
                "Cache-Control": (
                    "private, no-store"
                ),
            },
        )

    # ==================================================================
    # OPTIONAL R2 DEFAULT-BUCKET MODE
    # ==================================================================
    #
    # This supports an existing database where audio_file_path is:
    #
    #     audio/my-beat.mp3
    #
    # and the actual bucket is stored in:
    #
    #     R2_BUCKET_NAME
    #
    # It only activates when the default R2 configuration exists.
    # ==================================================================

    default_bucket = r2_default_bucket()

    if default_bucket:

        # Do not blindly treat every local path as R2.
        #
        # First check whether the local file actually exists.
        #
        local_candidate = Path(
            stored_text
        )

        if not local_candidate.is_absolute():

            local_candidate = (
                Path.cwd()
                / local_candidate
            )

        if not (
            local_candidate.exists()
            and local_candidate.is_file()
        ):

            try:

                extension = (
                    Path(
                        stored_text
                    ).suffix.lower()
                )

                download_name = (
                    f"{safe_title_value}"
                    f"{extension}"
                    if extension
                    else safe_title_value
                )

                signed_url = (
                    create_r2_download_url(
                        bucket=default_bucket,
                        key=stored_text.lstrip(
                            "/"
                        ),
                        download_name=download_name,
                    )
                )

                return RedirectResponse(
                    url=signed_url,
                    status_code=302,
                )

            except (
                RuntimeError,
                BotoCoreError,
                ClientError,
            ):
                # Continue to local-file compatibility
                # below rather than breaking old local storage.
                pass

    # ==================================================================
    # LOCAL FILE COMPATIBILITY
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
    # COMPATIBILITY SEARCH
    # ==================================================================

    if not (
        audio_path.exists()
        and audio_path.is_file()
    ):

        filename = (
            stored_path.name
        )

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
    # LOCAL STORAGE SECURITY
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
    # SAFE LOCAL DOWNLOAD NAME
    # ==================================================================

    extension = (
        audio_path.suffix.lower()
    )

    download_name = (
        f"{safe_title_value}"
        f"{extension}"
        if extension
        else safe_title_value
    )

    # ==================================================================
    # SERVE LOCAL FILE
    # ==================================================================

    return FileResponse(
        path=str(
            audio_path
        ),
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
