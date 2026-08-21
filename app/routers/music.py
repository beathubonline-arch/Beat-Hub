"""
BeatHub music routes.

Supports:
- Public beat browsing
- Hot picks
- Sessions
- Track details
- Album details
- Creator profiles
- Secure purchased-track downloads
- Cloudflare R2 private-object downloads
- Local filesystem compatibility for older tracks

R2 audio paths are expected in formats such as:

    r2://beathub-r2/audio/file.mp3

or:

    r2://beathub-r2/tracks/file.mp3
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
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

templates = Jinja2Templates(
    directory="app/templates"
)


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
# R2 CLIENT
# ======================================================================

def get_r2_client():
    """
    Create a Cloudflare R2 S3-compatible client.

    R2 credentials are loaded from app.config.settings.
    """

    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cloudflare R2 storage is not configured. "
                "Please check the R2 environment variables."
            ),
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


# ======================================================================
# R2 PATH PARSER
# ======================================================================

def parse_r2_path(
    stored_path: str,
):
    """
    Convert:

        r2://bucket/key/file.mp3

    into:

        bucket
        key

    Only the configured BeatHub R2 bucket is allowed.
    """

    value = (
        str(stored_path or "")
        .strip()
    )

    if not value.lower().startswith(
        "r2://"
    ):
        return None, None

    remainder = value[5:]

    if "/" not in remainder:
        return None, None

    bucket, key = remainder.split(
        "/",
        1,
    )

    bucket = bucket.strip()
    key = key.strip().lstrip("/")

    if not bucket or not key:
        return None, None

    configured_bucket = (
        str(settings.R2_BUCKET_NAME or "")
        .strip()
    )

    if not configured_bucket:
        return None, None

    # Security:
    # Never allow a database value to make us access
    # an arbitrary R2 bucket.
    if bucket != configured_bucket:
        raise HTTPException(
            status_code=403,
            detail="Invalid R2 storage bucket.",
        )

    return bucket, key


# ======================================================================
# R2 DOWNLOAD
# ======================================================================

def create_r2_download_url(
    stored_path: str,
    download_name: str,
):
    """
    Generate a temporary signed URL for a private R2 object.
    """

    bucket, key = parse_r2_path(
        stored_path
    )

    if not bucket or not key:
        raise HTTPException(
            status_code=404,
            detail="Invalid R2 audio path.",
        )

    client = get_r2_client()

    extension = (
        Path(key)
        .suffix
        .lower()
    )

    media_type = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(
        extension,
        "application/octet-stream",
    )

    # Quote filename safely for Content-Disposition.
    safe_filename = (
        download_name
        .replace(
            "\\",
            "_",
        )
        .replace(
            '"',
            "_",
        )
        .replace(
            "\r",
            "_",
        )
        .replace(
            "\n",
            "_",
        )
    )

    try:
        # --------------------------------------------------------------
        # Confirm object exists before giving buyer a URL.
        # --------------------------------------------------------------

        client.head_object(
            Bucket=bucket,
            Key=key,
        )

        # --------------------------------------------------------------
        # Generate short-lived private download URL.
        # --------------------------------------------------------------

        signed_url = client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": (
                    f'attachment; filename="{safe_filename}"'
                ),
                "ResponseContentType": media_type,
            },
            ExpiresIn=int(
                settings.R2_DOWNLOAD_URL_EXPIRES
            ),
        )

        return signed_url

    except ClientError as exc:
        error_code = (
            exc.response
            .get("Error", {})
            .get("Code", "")
        )

        if error_code in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            raise HTTPException(
                status_code=404,
                detail=(
                    "The purchased audio file "
                    "could not be found in R2."
                ),
            )

        raise HTTPException(
            status_code=503,
            detail=(
                "BeatHub could not access "
                "the R2 audio file."
            ),
        )

    except BotoCoreError:
        raise HTTPException(
            status_code=503,
            detail=(
                "BeatHub could not connect "
                "to Cloudflare R2."
            ),
        )


# ======================================================================
# LOCAL FILE DOWNLOAD
# ======================================================================

def find_local_audio_file(
    stored_path: str,
):
    """
    Compatibility fallback for older tracks that were stored
    on the local filesystem instead of R2.

    This intentionally does not use settings.MEDIA_ROOT because
    MEDIA_ROOT is not part of the current BeatHub configuration.
    """

    value = (
        str(stored_path or "")
        .strip()
    )

    if not value:
        return None

    stored = Path(value)

    # --------------------------------------------------------------
    # Absolute local path.
    # --------------------------------------------------------------

    if stored.is_absolute():
        candidate = stored.resolve()

        if (
            candidate.exists()
            and candidate.is_file()
        ):
            return candidate

        return None

    project_root = Path.cwd().resolve()

    candidates = [
        project_root / stored,
        project_root / "media" / stored,
        project_root / "media" / "audio" / stored.name,
        project_root / "uploads" / stored,
        project_root / "uploads" / "audio" / stored.name,
    ]

    for candidate in candidates:

        try:
            resolved = candidate.resolve()
        except Exception:
            continue

        # Never allow ../ traversal outside project root.
        try:
            resolved.relative_to(
                project_root
            )
        except ValueError:
            continue

        if (
            resolved.exists()
            and resolved.is_file()
        ):
            return resolved

    return None


# ======================================================================
# DOWNLOAD FILENAME
# ======================================================================

def build_download_filename(
    track: Track,
    stored_path: str,
):
    """
    Build a safe filename while preserving the original
    audio extension where possible.
    """

    title = str(
        getattr(
            track,
            "title",
            "",
        )
        or ""
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
        Path(
            str(stored_path or "")
        )
        .suffix
        .lower()
    )

    if extension not in {
        ".mp3",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac",
    }:
        extension = ".mp3"

    return (
        f"{safe_title}{extension}"
    )


# ======================================================================
# PUBLIC MUSIC
# ======================================================================

@router.get("/beats")
def browse_beats(
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
            detail="Profile not found.",
        )

    tracks = [
        track
        for track in (
            getattr(
                profile,
                "tracks",
                None,
            )
            or []
        )
        if getattr(
            track,
            "is_published",
            False,
        )
    ]

    albums = [
        album
        for album in (
            getattr(
                profile,
                "albums",
                None,
            )
            or []
        )
        if getattr(
            album,
            "is_published",
            False,
        )
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
# SECURE PURCHASE DOWNLOAD
# ======================================================================
#
# Supports:
#
#   /download/track/{track_id}
#   /download/{track_id}
#   /download/track/{track_slug}
#   /download/{track_slug}
#
# Buyer must have:
#
#   License
#       ↓
#   correct buyer
#       ↓
#   correct track
#       ↓
#   completed order
#
# R2 files:
#
#   r2://bucket/key.mp3
#
# are returned through a temporary signed URL.
#
# Local legacy files are still supported.
# ======================================================================

@router.get("/download/track/{track_ref}")
@router.get("/download/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
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

    stored_path = (
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

    if not stored_path:
        raise HTTPException(
            status_code=404,
            detail="This track has no audio file.",
        )

    # --------------------------------------------------------------
    # DOWNLOAD NAME
    # --------------------------------------------------------------

    download_name = (
        build_download_filename(
            track,
            stored_path,
        )
    )

    # --------------------------------------------------------------
    # R2 DOWNLOAD
    # --------------------------------------------------------------

    if stored_path.lower().startswith(
        "r2://"
    ):

        signed_url = (
            create_r2_download_url(
                stored_path,
                download_name,
            )
        )

        return RedirectResponse(
            url=signed_url,
            status_code=307,
        )

    # --------------------------------------------------------------
    # HTTPS STORAGE URL
    #
    # Supports an existing public/custom-domain
    # storage URL if one was stored.
    # --------------------------------------------------------------

    if stored_path.startswith(
        "https://"
    ) or stored_path.startswith(
        "http://"
    ):

        return RedirectResponse(
            url=stored_path,
            status_code=307,
        )

    # --------------------------------------------------------------
    # LOCAL FILE COMPATIBILITY
    # --------------------------------------------------------------

    audio_path = (
        find_local_audio_file(
            stored_path
        )
    )

    if audio_path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The purchased audio file "
                "is unavailable."
            ),
        )

    # --------------------------------------------------------------
    # MIME TYPE
    # --------------------------------------------------------------

    extension = (
        audio_path.suffix.lower()
    )

    media_type = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(
        extension,
        "application/octet-stream",
    )

    # --------------------------------------------------------------
    # LOCAL DOWNLOAD
    # --------------------------------------------------------------

    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=download_name,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{download_name}"'
            ),
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )
