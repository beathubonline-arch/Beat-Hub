from datetime import datetime
from typing import Optional
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
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


# ----------------------------------------------------------------------
# R2
# ----------------------------------------------------------------------

def get_r2_client():
    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail="Cloud storage is not configured.",
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def r2_object_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    value = str(value).strip()

    if not value:
        return None

    if value.startswith("r2://"):
        parts = value[5:].split("/", 1)

        if len(parts) == 2:
            return parts[1]

    return value.lstrip("/")


def r2_presigned_url(
    value: Optional[str],
    expires: Optional[int] = None,
) -> Optional[str]:
    key = r2_object_key(value)

    if not key:
        return None

    if settings.R2_PUBLIC_URL:
        return (
            settings.R2_PUBLIC_URL.rstrip("/")
            + "/"
            + quote(key)
        )

    client = get_r2_client()

    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=expires or settings.R2_PUBLIC_URL_EXPIRES,
    )


def ctx(request: Request, current_user, **extra):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    data.update(extra)

    return data


# ----------------------------------------------------------------------
# PUBLIC MUSIC
# ----------------------------------------------------------------------

@router.get("/beats")
def browse_beats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    tracks = (
        db.query(Track)
        .filter(Track.is_published == True)
        .order_by(Track.created_at.desc())
        .limit(60)
        .all()
    )

    for track in tracks:
        if track.cover_art_path:
            try:
                track.cover_art_path = r2_presigned_url(
                    track.cover_art_path
                )
            except Exception:
                pass

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
    current_user: Optional[User] = Depends(get_optional_user),
):
    tracks = (
        db.query(Track)
        .filter(Track.is_published == True)
        .order_by(Track.created_at.desc())
        .limit(12)
        .all()
    )

    for track in tracks:
        if track.cover_art_path:
            try:
                track.cover_art_path = r2_presigned_url(
                    track.cover_art_path
                )
            except Exception:
                pass

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
    current_user: Optional[User] = Depends(get_optional_user),
):
    return templates.TemplateResponse(
        request,
        "sessions.html",
        ctx(request, current_user),
    )


# ----------------------------------------------------------------------
# TRACK DETAIL
# ----------------------------------------------------------------------

@router.get("/track/{slug}")
def track_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    track = (
        db.query(Track)
        .filter(Track.slug == slug)
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
                License.buyer_id == current_user.id,
                License.track_id == track.id,
                Order.status == OrderStatus.COMPLETED,
            )
            .first()
            is not None
        )

    if track.cover_art_path:
        try:
            track.cover_art_path = r2_presigned_url(
                track.cover_art_path
            )
        except Exception:
            pass

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
    current_user: Optional[User] = Depends(get_optional_user),
):
    album = (
        db.query(Album)
        .filter(Album.slug == slug)
        .first()
    )

    if not album:
        raise HTTPException(
            status_code=404,
            detail="Album not found.",
        )

    if album.artwork_path:
        try:
            album.artwork_path = r2_presigned_url(
                album.artwork_path
            )
        except Exception:
            pass

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
    current_user: Optional[User] = Depends(get_optional_user),
):
    profile = (
        db.query(Profile)
        .filter(Profile.slug == slug)
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
        if track.cover_art_path:
            try:
                track.cover_art_path = r2_presigned_url(
                    track.cover_art_path
                )
            except Exception:
                pass

    for album in albums:
        if album.artwork_path:
            try:
                album.artwork_path = r2_presigned_url(
                    album.artwork_path
                )
            except Exception:
                pass

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

@router.get("/download/track/{track_ref}")
@router.get("/download/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # --------------------------------------------------------------
    # Find by UUID or slug.
    # --------------------------------------------------------------

    track = (
        db.query(Track)
        .filter(Track.id == track_ref)
        .first()
    )

    if not track:
        track = (
            db.query(Track)
            .filter(Track.slug == track_ref)
            .first()
        )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------------
    # Verify ownership.
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
    # R2 configuration.
    # --------------------------------------------------------------

    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail="Cloud storage is not configured.",
        )

    key = r2_object_key(track.audio_file_path)

    if not key:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    client = get_r2_client()

    # --------------------------------------------------------------
    # Confirm the object actually exists.
    # --------------------------------------------------------------

    try:
        metadata = client.head_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
        )
    except ClientError:
        raise HTTPException(
            status_code=404,
            detail="The purchased audio file is missing from storage.",
        )

    # --------------------------------------------------------------
    # Generate temporary private download URL.
    # --------------------------------------------------------------

    download_url = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": key,
            "ResponseContentType": (
                metadata.get(
                    "ContentType",
                    "application/octet-stream",
                )
            ),
        },
        ExpiresIn=settings.R2_DOWNLOAD_URL_EXPIRES,
    )

    # --------------------------------------------------------------
    # Browser downloads directly from R2.
    # --------------------------------------------------------------

    return RedirectResponse(
        url=download_url,
        status_code=307,
    )
