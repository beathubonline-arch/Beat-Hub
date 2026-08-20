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
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.utils.deps import get_current_user


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
            detail="Cloudflare R2 storage is not configured.",
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

    if value.startswith("r2://"):
        parts = value[5:].split("/", 1)

        if len(parts) == 2:
            return parts[1]

    if value.startswith("http://") or value.startswith("https://"):
        return None

    return value.lstrip("/")


def r2_url(
    path: Optional[str],
    expires: int,
) -> Optional[str]:
    key = r2_key(path)

    if not key:
        return None

    if settings.R2_PUBLIC_URL:
        return (
            settings.R2_PUBLIC_URL.rstrip("/")
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


def r2_object_exists(path: Optional[str]) -> bool:
    key = r2_key(path)

    if not key:
        return False

    client = get_r2_client()

    try:
        client.head_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
        )
        return True

    except ClientError:
        return False


# ----------------------------------------------------------------------
# TRACK PAGE
# ----------------------------------------------------------------------

@router.get("/track/{track_ref}")
def track_page(
    request: Request,
    track_ref: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_current_user
    ),
):
    track = (
        db.query(Track)
        .filter(Track.slug == track_ref)
        .first()
    )

    if not track:
        track = (
            db.query(Track)
            .filter(Track.id == track_ref)
            .first()
        )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    purchased = False

    if current_user:
        try:
            purchased = (
                db.query(Order)
                .filter(
                    Order.track_id == track.id,
                    Order.status
                    == OrderStatus.COMPLETED,
                )
                .filter(
                    Order.user_id
                    == current_user.id
                )
                .first()
                is not None
            )
        except Exception:
            purchased = False

    cover_art_url = None

    if track.cover_art_path:
        try:
            cover_art_url = r2_url(
                track.cover_art_path,
                settings.R2_PUBLIC_URL_EXPIRES,
            )
        except Exception:
            cover_art_url = None

    return templates.TemplateResponse(
        request,
        "track.html",
        {
            "request": request,
            "current_user": current_user,
            "current_year": 2026,
            "track": track,
            "purchased": purchased,
            "cover_art_url": cover_art_url,
        },
    )


# ----------------------------------------------------------------------
# DOWNLOAD PURCHASED TRACK
# ----------------------------------------------------------------------

@router.get("/download/track/{track_ref}")
def download_track(
    request: Request,
    track_ref: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(
        get_current_user
    ),
):
    if not current_user:
        return RedirectResponse(
            url="/login?next="
            f"/download/track/{track_ref}",
            status_code=303,
        )

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
    # PURCHASE VERIFICATION
    # --------------------------------------------------------------

    try:
        order = (
            db.query(Order)
            .filter(
                Order.track_id == track.id,
                Order.user_id == current_user.id,
                Order.status == OrderStatus.COMPLETED,
            )
            .order_by(
                Order.completed_at.desc()
            )
            .first()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to verify purchase."
            ),
        ) from exc

    if not order:
        raise HTTPException(
            status_code=403,
            detail=(
                "This track has not been purchased "
                "by the current account."
            ),
        )

    # --------------------------------------------------------------
    # R2 CHECK
    # --------------------------------------------------------------

    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail="Cloudflare R2 is not configured.",
        )

    key = r2_key(
        track.audio_file_path
    )

    if not key:
        raise HTTPException(
            status_code=404,
            detail="The purchased audio file is missing.",
        )

    try:
        exists = r2_object_exists(
            track.audio_file_path
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Unable to connect to Cloudflare R2."
            ),
        ) from exc

    if not exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "The audio file does not exist "
                "in Cloudflare R2."
            ),
        )

    # --------------------------------------------------------------
    # SIGNED DOWNLOAD URL
    # --------------------------------------------------------------

    try:
        download_url = r2_url(
            track.audio_file_path,
            settings.R2_DOWNLOAD_URL_EXPIRES,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Unable to create the secure "
                "download link."
            ),
        ) from exc

    if not download_url:
        raise HTTPException(
            status_code=404,
            detail="Download file is unavailable.",
        )

    return RedirectResponse(
        url=download_url,
        status_code=307,
    )
