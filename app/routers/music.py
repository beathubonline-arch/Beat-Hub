"""
BeatHub music, marketplace, purchases and secure downloads.

Public:
    /beats
    /hot-picks
    /sessions
    /track/{slug}
    /album/{slug}
    /profile/{slug}

Protected:
    /purchases
    /download/{track_ref}
    /download/track/{track_ref}

Storage:
    - R2 / S3 style r2://bucket/key
    - s3://bucket/key
    - full HTTPS object URLs
    - existing local media files

Security:
    - Download requires authenticated user.
    - Download requires a License belonging to that user.
    - License must belong to a COMPLETED Order.
    - R2 objects are never exposed directly from the database.
    - R2 downloads use short-lived presigned URLs.
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
# CONTEXT
# ======================================================================

def ctx(
    request: Request,
    current_user=None,
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
        and getattr(track, "is_published", False)
    )


def sales_model_value(track: Track) -> str:
    value = getattr(
        getattr(track, "sales_model", None),
        "value",
        getattr(track, "sales_model", ""),
    )

    return str(value or "").strip().lower()


def track_is_available(track: Track) -> bool:
    """
    Non-exclusive:
        published = available

    Exclusive:
        published AND not sold = available
    """

    if not track_is_visible(track):
        return False

    if (
        sales_model_value(track)
        == "exclusive"
    ):
        return not bool(
            getattr(track, "is_sold", False)
        )

    return True


def safe_download_name(track: Track) -> str:
    title = str(
        getattr(track, "title", "")
        or "BeatHub-Track"
    )

    title = "".join(
        character
        for character in title
        if character.isalnum()
        or character in (
            " ",
            "-",
            "_",
        )
    ).strip()

    if not title:
        title = "BeatHub-Track"

    return title


# ======================================================================
# R2 CONFIGURATION
# ======================================================================

def env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name)

        if value:
            value = value.strip()

            if value:
                return value

    return ""


def r2_endpoint() -> str:
    return env_first(
        "R2_ENDPOINT",
        "R2_ENDPOINT_URL",
        "AWS_S3_ENDPOINT",
        "S3_ENDPOINT_URL",
    )


def r2_access_key() -> str:
    return env_first(
        "R2_ACCESS_KEY_ID",
        "AWS_ACCESS_KEY_ID",
        "S3_ACCESS_KEY_ID",
    )


def r2_secret_key() -> str:
    return env_first(
        "R2_SECRET_ACCESS_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "S3_SECRET_ACCESS_KEY",
    )


def r2_default_bucket() -> str:
    return env_first(
        "R2_BUCKET_NAME",
        "R2_BUCKET",
        "AWS_S3_BUCKET",
        "S3_BUCKET",
    )


def r2_presign_seconds() -> int:
    raw = env_first(
        "R2_DOWNLOAD_EXPIRES",
        "R2_PRESIGNED_URL_EXPIRES",
    )

    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 300

    return max(
        60,
        min(value, 900),
    )


def is_r2_reference(value: str) -> bool:
    value = str(value or "").strip().lower()

    return (
        value.startswith("r2://")
        or value.startswith("s3://")
    )


def is_http_reference(value: str) -> bool:
    value = str(value or "").strip().lower()

    return (
        value.startswith("https://")
        or value.startswith("http://")
    )


def parse_object_reference(
    stored_value: str,
):
    """
    Returns:

        ("r2", bucket, key)

    for:
        r2://bucket/key
        s3://bucket/key

    Returns:

        ("url", None, url)

    for:
        https://...

    Returns:

        ("local", None, stored_value)

    for local filesystem references.
    """

    value = str(
        stored_value or ""
    ).strip()

    if not value:
        return (
            "local",
            None,
            value,
        )

    if (
        value.startswith("r2://")
        or value.startswith("s3://")
    ):
        parsed = urlparse(value)

        bucket = parsed.netloc.strip()
        key = parsed.path.lstrip("/")

        if not bucket or not key:
            raise HTTPException(
                status_code=404,
                detail="Invalid cloud storage reference.",
            )

        return (
            "r2",
            bucket,
            key,
        )

    if is_http_reference(value):
        return (
            "url",
            None,
            value,
        )

    return (
        "local",
        None,
        value,
    )


def create_r2_client():
    endpoint = r2_endpoint()
    access_key = r2_access_key()
    secret_key = r2_secret_key()

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

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def create_presigned_download(
    stored_value: str,
    download_name: str,
) -> str:
    """
    Creates a short-lived private download URL.

    For r2://bucket/key:
        signs that exact bucket/key.

    For s3://bucket/key:
        signs that exact bucket/key.

    For a full HTTP URL:
        returns the URL as-is.

    Local files are not handled here.
    """

    kind, bucket, value = (
        parse_object_reference(
            stored_value
        )
    )

    if kind == "url":
        return value

    if kind != "r2":
        raise RuntimeError(
            "Storage reference is not an R2 object."
        )

    if not bucket:
        bucket = r2_default_bucket()

    if not bucket:
        raise RuntimeError(
            "R2 bucket is missing."
        )

    key = value

    client = create_r2_client()

    try:
        return client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": (
                    f'attachment; filename="{download_name}"'
                ),
            },
            ExpiresIn=r2_presign_seconds(),
        )

    except (
        BotoCoreError,
        ClientError,
    ) as exc:
        raise RuntimeError(
            f"Could not create R2 download URL: {exc}"
        ) from exc


# ======================================================================
# OWNERSHIP
# ======================================================================

def get_completed_license(
    db: Session,
    user_id: str,
    track_id: str,
):
    """
    Authoritative ownership check.

    A buyer owns a track only when:
        License.buyer_id == user_id
        License.track_id == track_id
        Order.status == COMPLETED
    """

    return (
        db.query(License)
        .join(
            Order,
            License.order_id == Order.id,
        )
        .filter(
            License.buyer_id == user_id,
            License.track_id == track_id,
            Order.status == OrderStatus.COMPLETED,
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
        pattern = f"%{search}%"

        query = query.filter(
            or_(
                Track.title.ilike(pattern),
                Track.genre.ilike(pattern),
                Track.tags.ilike(pattern),
                Track.description.ilike(pattern),
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
            detail="Track not found.",
        )

    purchased = False

    if current_user:
        purchased = (
            get_completed_license(
                db,
                current_user.id,
                track.id,
            )
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
            available=track_is_available(
                track
            ),
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


# ======================================================================
# PROFILE
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


# ======================================================================
# PURCHASES
# ======================================================================

@router.get("/purchases")
def purchases_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """
    Buyer's permanent purchase library.

    Only completed purchases are shown.
    """

    licenses = (
        db.query(License)
        .join(
            Order,
            License.order_id == Order.id,
        )
        .filter(
            License.buyer_id == user.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .order_by(
            License.granted_at.desc()
        )
        .all()
    )

    purchases = []

    for license_record in licenses:

        order = license_record.order
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

        if not track and order:
            track = order.track

        if not track:
            continue

        purchases.append(
            {
                "license": license_record,
                "order": order,
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
        ),
    )


# ======================================================================
# SECURE DOWNLOAD
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

    R2:
        r2://bucket/key
        s3://bucket/key
        HTTPS object URL

    Local:
        existing MEDIA_ROOT/media files
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
    # OWNERSHIP
    # --------------------------------------------------------------

    license_record = (
        get_completed_license(
            db,
            user.id,
            track.id,
        )
    )

    if not license_record:
        raise HTTPException(
            status_code=403,
            detail="You do not own this track.",
        )

    # --------------------------------------------------------------
    # AUDIO REFERENCE
    # --------------------------------------------------------------

    stored_value = str(
        getattr(
            track,
            "audio_file_path",
            "",
        )
        or ""
    ).strip()

    if not stored_value:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    download_title = safe_download_name(
        track
    )

    # --------------------------------------------------------------
    # R2 / S3 / CLOUD URL
    # --------------------------------------------------------------

    if (
        is_r2_reference(stored_value)
        or is_http_reference(stored_value)
    ):
        try:
            download_url = (
                create_presigned_download(
                    stored_value,
                    download_title,
                )
            )

        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "The purchased audio is stored "
                    "in cloud storage, but a secure "
                    "download link could not be created."
                ),
            ) from exc

        return RedirectResponse(
            url=download_url,
            status_code=307,
            headers={
                "Cache-Control": (
                    "private, no-store"
                )
            },
        )

    # --------------------------------------------------------------
    # LOCAL FILE COMPATIBILITY
    # --------------------------------------------------------------

    stored_path = Path(
        stored_value
    )

    media_root_value = env_first(
        "MEDIA_ROOT"
    )

    if not media_root_value:
        media_root_value = str(
            getattr(
                settings,
                "MEDIA_ROOT",
                "media",
            )
            or "media"
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
    # COMPATIBILITY LOCATIONS
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
    # LOCAL PATH SECURITY
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

    extension = (
        audio_path.suffix.lower()
    )

    download_name = (
        f"{download_title}{extension}"
        if extension
        else download_title
    )

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
            "Pragma": "no-cache",
        },
    )
