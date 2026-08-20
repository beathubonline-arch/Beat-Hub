from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Track
from app.models.order import Order, OrderStatus, License
from app.models.user import User
from app.utils.deps import get_optional_user


router = APIRouter(tags=["music"])

templates = Jinja2Templates(directory="app/templates")


# ============================================================
# CLOUDFLARE R2
# ============================================================

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


def r2_key(path: Optional[str]) -> Optional[str]:
    if not path:
        return None

    value = str(path).strip()

    # Handle r2://bucket/key
    if value.startswith("r2://"):
        value = value[5:]
        parts = value.split("/", 1)

        if len(parts) == 2:
            value = parts[1]

    # Handle full R2 URL accidentally stored in database.
    if "://" in value:
        try:
            from urllib.parse import urlparse, unquote

            parsed = urlparse(value)
            value = unquote(parsed.path).lstrip("/")

            # Remove bucket prefix if present.
            bucket = getattr(settings, "R2_BUCKET_NAME", None)
            if bucket and value.startswith(bucket + "/"):
                value = value[len(bucket) + 1:]

        except Exception:
            pass

    value = value.lstrip("/")

    # Remove accidental "media/" prefix.
    if value.startswith("media/"):
        value = value[6:]

    return value or None


def r2_url(
    path: Optional[str],
    expires: int = 3600,
) -> Optional[str]:
    key = r2_key(path)

    if not key:
        return None

    # Prefer configured public URL.
    public_url = getattr(settings, "R2_PUBLIC_URL", None)

    if public_url:
        return (
            public_url.rstrip("/")
            + "/"
            + key
        )

    client = get_r2_client()

    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=expires,
    )


def r2_download_url(
    path: Optional[str],
    filename: Optional[str] = None,
) -> Optional[str]:
    key = r2_key(path)

    if not key:
        return None

    client = get_r2_client()

    params = {
        "Bucket": settings.R2_BUCKET_NAME,
        "Key": key,
    }

    if filename:
        params["ResponseContentDisposition"] = (
            f'attachment; filename="{filename}"'
        )

    return client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=getattr(
            settings,
            "R2_DOWNLOAD_URL_EXPIRES",
            900,
        ),
    )


# ============================================================
# TRACK DETAIL
# ============================================================

@router.get("/track/{track_ref}")
def track_detail(
    request: Request,
    track_ref: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    track = (
        db.query(Track)
        .filter(
            (Track.id == track_ref)
            | (Track.slug == track_ref)
        )
        .first()
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------
    # COVER ART
    # --------------------------------------------------------

    cover_art_url = None

    if track.cover_art_path:
        try:
            cover_art_url = r2_url(
                track.cover_art_path,
                expires=3600,
            )
        except Exception:
            cover_art_url = None

    # --------------------------------------------------------
    # PURCHASE CHECK
    # --------------------------------------------------------

    purchased = False

    if user:
        order = (
            db.query(Order)
            .filter(
                Order.track_id == track.id,
                Order.buyer_id == user.id,
                Order.status == OrderStatus.COMPLETED,
            )
            .order_by(Order.completed_at.desc())
            .first()
        )

        if order:
            purchased = True

        # Compatibility with installations where an older
        # Order model used user_id.
        if not purchased and hasattr(Order, "user_id"):
            order = (
                db.query(Order)
                .filter(
                    Order.track_id == track.id,
                    Order.user_id == user.id,
                    Order.status == OrderStatus.COMPLETED,
                )
                .order_by(Order.completed_at.desc())
                .first()
            )

            purchased = order is not None

        # License is the authoritative ownership record when
        # available.
        if not purchased:
            license_record = (
                db.query(License)
                .filter(
                    License.track_id == track.id,
                    License.buyer_id == user.id,
                )
                .first()
            )

            if license_record:
                purchased = True

    return templates.TemplateResponse(
        request,
        "track_detail.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "track": track,
            "purchased": purchased,
            "cover_art_url": cover_art_url,
        },
    )


# ============================================================
# DOWNLOAD PURCHASED TRACK
# ============================================================

@router.get("/download/track/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    if not user:
        raise HTTPException(
            status_code=401,
            detail="You must be logged in to download this track.",
        )

    # --------------------------------------------------------
    # FIND TRACK
    # --------------------------------------------------------

    track = (
        db.query(Track)
        .filter(
            (Track.id == track_ref)
            | (Track.slug == track_ref)
        )
        .first()
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------
    # VERIFY PURCHASE
    # --------------------------------------------------------

    order = (
        db.query(Order)
        .filter(
            Order.track_id == track.id,
            Order.buyer_id == user.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .order_by(Order.completed_at.desc())
        .first()
    )

    # Compatibility with older Order schema.
    if not order and hasattr(Order, "user_id"):
        order = (
            db.query(Order)
            .filter(
                Order.track_id == track.id,
                Order.user_id == user.id,
                Order.status == OrderStatus.COMPLETED,
            )
            .order_by(Order.completed_at.desc())
            .first()
        )

    # License ownership is also accepted.
    if not order:
        license_record = (
            db.query(License)
            .filter(
                License.track_id == track.id,
                License.buyer_id == user.id,
            )
            .first()
        )

        if not license_record:
            raise HTTPException(
                status_code=403,
                detail="You have not purchased this track.",
            )

    # --------------------------------------------------------
    # AUDIO FILE
    # --------------------------------------------------------

    if not track.audio_file_path:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    try:
        download_url = r2_download_url(
            track.audio_file_path,
            filename=f"{track.slug}.mp3",
        )

    except ClientError:
        raise HTTPException(
            status_code=503,
            detail="Unable to access the purchased audio file.",
        )

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Unable to access the purchased audio file.",
        )

    if not download_url:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    # --------------------------------------------------------
    # REDIRECT TO SIGNED R2 DOWNLOAD
    # --------------------------------------------------------

    return RedirectResponse(
        url=download_url,
        status_code=307,
    )
