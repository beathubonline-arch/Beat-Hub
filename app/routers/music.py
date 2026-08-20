from datetime import datetime
from typing import Optional
from urllib.parse import quote

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Album, Track
from app.models.order import (
    License,
    Order,
    OrderStatus,
)
from app.models.profile import Profile
from app.models.user import User
from app.utils.deps import (
    get_optional_user,
    require_user,
)


router = APIRouter(
    tags=["music"]
)

templates = Jinja2Templates(
    directory="app/templates"
)


# ----------------------------------------------------------------------
# CONTEXT
# ----------------------------------------------------------------------

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


# ----------------------------------------------------------------------
# R2
# ----------------------------------------------------------------------

def get_r2_client():
    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cloud storage is not configured."
            ),
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=(
            settings.R2_ACCESS_KEY_ID
        ),
        aws_secret_access_key=(
            settings.R2_SECRET_ACCESS_KEY
        ),
        region_name="auto",
        config=BotoConfig(
            signature_version="s3v4"
        ),
    )


def r2_object_key(
    value: Optional[str],
) -> Optional[str]:
    """
    Convert database storage values into
    an R2 object key.

    Examples:

        r2://beathub/audio/file.mp3
        -> audio/file.mp3

        audio/file.mp3
        -> audio/file.mp3
    """

    if not value:
        return None

    value = str(value).strip()

    if not value:
        return None

    if value.startswith("r2://"):

        parts = value[5:].split(
            "/",
            1,
        )

        if len(parts) == 2:
            return parts[1]

        return None

    return value.lstrip("/")


def r2_presigned_url(
    value: Optional[str],
    expires: Optional[int] = None,
    response_content_type: Optional[str] = None,
    response_content_disposition: Optional[str] = None,
) -> Optional[str]:

    key = r2_object_key(value)

    if not key:
        return None

    # If the bucket has a configured public/custom URL,
    # use it directly.
    #
    # For private buckets, use a presigned S3 GET URL.
    if (
        settings.R2_PUBLIC_URL
        and not response_content_disposition
    ):
        return (
            settings.R2_PUBLIC_URL.rstrip("/")
            + "/"
            + quote(
                key,
                safe="/",
            )
        )

    client = get_r2_client()

    params = {
        "Bucket": settings.R2_BUCKET_NAME,
        "Key": key,
    }

    if response_content_type:
        params[
            "ResponseContentType"
        ] = response_content_type

    if response_content_disposition:
        params[
            "ResponseContentDisposition"
        ] = response_content_disposition

    return client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=(
            expires
            or settings.R2_PUBLIC_URL_EXPIRES
        ),
    )


# ----------------------------------------------------------------------
# PUBLIC MUSIC
# ----------------------------------------------------------------------

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
            Track.is_published == True
        )
        .order_by(
            Track.created_at.desc()
        )
        .limit(60)
        .all()
    )

    for track in tracks:

        track.cover_art_url = None

        if track.cover_art_path:
            try:
                track.cover_art_url = (
                    r2_presigned_url(
                        track.cover_art_path,
                        settings.R2_PUBLIC_URL_EXPIRES,
                    )
                )
            except Exception:
                track.cover_art_url = None

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
    current_user: Optional[User] = Depends(
        get_optional_user
    ),
):
    tracks = (
        db.query(Track)
        .filter(
            Track.is_published == True
        )
        .order_by(
            Track.created_at.desc()
        )
        .limit(12)
        .all()
    )

    for track in tracks:

        track.cover_art_url = None

        if track.cover_art_path:
            try:
                track.cover_art_url = (
                    r2_presigned_url(
                        track.cover_art_path,
                        settings.R2_PUBLIC_URL_EXPIRES,
                    )
                )
            except Exception:
                track.cover_art_url = None

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


# ----------------------------------------------------------------------
# TRACK DETAIL
# ----------------------------------------------------------------------

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

    track.cover_art_url = None

    if track.cover_art_path:

        try:
            track.cover_art_url = (
                r2_presigned_url(
                    track.cover_art_path,
                    settings.R2_PUBLIC_URL_EXPIRES,
                )
            )
        except Exception:
            track.cover_art_url = None

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


# ----------------------------------------------------------------------
# ALBUM
# ----------------------------------------------------------------------

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

    album.artwork_url = None

    if album.artwork_path:

        try:
            album.artwork_url = (
                r2_presigned_url(
                    album.artwork_path,
                    settings.R2_PUBLIC_URL_EXPIRES,
                )
            )
        except Exception:
            album.artwork_url = None

    return templates.TemplateResponse(
        request,
        "album_detail.html",
        ctx(
            request,
            current_user,
            album=album,
        ),
    )


# ----------------------------------------------------------------------
# CREATOR PROFILE
# ----------------------------------------------------------------------

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

    for track in tracks:

        track.cover_art_url = None

        if track.cover_art_path:

            try:
                track.cover_art_url = (
                    r2_presigned_url(
                        track.cover_art_path,
                        settings.R2_PUBLIC_URL_EXPIRES,
                    )
                )
            except Exception:
                track.cover_art_url = None

    for album in albums:

        album.artwork_url = None

        if album.artwork_path:

            try:
                album.artwork_url = (
                    r2_presigned_url(
                        album.artwork_path,
                        settings.R2_PUBLIC_URL_EXPIRES,
                    )
                )
            except Exception:
                album.artwork_url = None

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


# ----------------------------------------------------------------------
# PURCHASE DOWNLOAD
# ----------------------------------------------------------------------

@router.get(
    "/download/track/{track_ref}"
)
@router.get(
    "/download/{track_ref}"
)
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # --------------------------------------------------------------
    # Find track by UUID OR slug.
    # --------------------------------------------------------------

    track = (
        db.query(Track)
        .filter(
            Track.id == track_ref
        )
        .first()
    )

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
    # Verify actual ownership.
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
    # R2 must be configured.
    # --------------------------------------------------------------

    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cloud storage is not configured."
            ),
        )

    # --------------------------------------------------------------
    # Convert stored database path into R2 key.
    #
    # Example:
    #
    # r2://beathub/audio/
    # e4bc432a-....mp3
    #
    # becomes:
    #
    # audio/e4bc432a-....mp3
    # --------------------------------------------------------------

    key = r2_object_key(
        track.audio_file_path
    )

    if not key:
        raise HTTPException(
            status_code=404,
            detail=(
                "Audio file is not available."
            ),
        )

    client = get_r2_client()

    # --------------------------------------------------------------
    # Confirm object exists in R2.
    # --------------------------------------------------------------

    try:

        metadata = client.head_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
        )

    except ClientError as exc:

        error_code = (
            exc.response
            .get("Error", {})
            .get("Code")
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
                    "is missing from R2 storage."
                ),
            )

        raise HTTPException(
            status_code=503,
            detail=(
                "R2 could not be reached while "
                "preparing your download."
            ),
        )

    # --------------------------------------------------------------
    # Build safe download filename.
    # --------------------------------------------------------------

    safe_title = "".join(
        character
        for character in (
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

    original_name = key.rsplit(
        "/",
        1,
    )[-1]

    extension = ""

    if "." in original_name:
        extension = (
            "."
            + original_name.rsplit(
                ".",
                1,
            )[-1].lower()
        )

    download_name = (
        f"{safe_title}{extension}"
    )

    # --------------------------------------------------------------
    # Content type.
    # --------------------------------------------------------------

    content_type = (
        metadata.get(
            "ContentType"
        )
        or "application/octet-stream"
    )

    # --------------------------------------------------------------
    # Force download from R2.
    #
    # The browser receives the signed URL and
    # downloads the object directly from R2.
    # --------------------------------------------------------------

    download_url = (
        client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": (
                    settings.R2_BUCKET_NAME
                ),
                "Key": key,
                "ResponseContentType": (
                    content_type
                ),
                "ResponseContentDisposition": (
                    "attachment; "
                    f'filename="{download_name}"'
                ),
            },
            ExpiresIn=(
                settings.R2_DOWNLOAD_URL_EXPIRES
            ),
        )
    )

    return RedirectResponse(
        url=download_url,
        status_code=307,
        headers={
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )
