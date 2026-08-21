from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

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

templates = Jinja2Templates(directory="app/templates")


# ======================================================================
# COMMON TEMPLATE CONTEXT
# ======================================================================

def ctx(
    request: Request,
    current_user: Optional[User],
    **extra,
):
    context = {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": datetime.utcnow().year,
    }

    context.update(extra)

    return context


# ======================================================================
# STORAGE HELPERS
# ======================================================================

def _is_r2_path(value: Optional[str]) -> bool:
    if not value:
        return False

    return str(value).strip().lower().startswith("r2://")


def _r2_object_key(value: str) -> str:
    """
    Convert:

        r2://beathub/audio/file.mp3

    into:

        audio/file.mp3
    """

    value = unquote(str(value).strip())

    if value.startswith("r2://"):
        value = value[5:]

        parts = value.split("/", 1)

        if len(parts) == 2:
            return parts[1]

        return ""

    return value.lstrip("/")


def _r2_client():
    """
    Create a Cloudflare R2 S3-compatible client.

    R2 uses the AWS S3 API, so boto3 is used here.
    """

    if not settings.r2_enabled:
        raise HTTPException(
            status_code=503,
            detail="Cloudflare R2 storage is not configured.",
        )

    try:
        import boto3
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="R2 storage dependency is not installed.",
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def _r2_download_url(
    object_key: str,
    filename: str,
    expires: int,
) -> str:
    """
    Generate a temporary signed R2 download URL.
    """

    if not object_key:
        raise HTTPException(
            status_code=404,
            detail="Stored audio object path is invalid.",
        )

    client = _r2_client()

    try:
        return client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": settings.R2_BUCKET_NAME,
                "Key": object_key,
                "ResponseContentDisposition": (
                    f'attachment; filename="{filename}"'
                ),
                "ResponseContentType": "application/octet-stream",
            },
            ExpiresIn=max(
                60,
                int(settings.R2_DOWNLOAD_URL_EXPIRES),
            ),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to create secure download link: {exc}",
        )


def _local_audio_path(stored_path: str) -> Optional[Path]:
    """
    Compatibility fallback for tracks uploaded before R2 storage
    was enabled.

    No MEDIA_ROOT setting is required.

    Supported examples:

        media/audio/file.mp3
        audio/file.mp3
        /.../media/audio/file.mp3
    """

    if not stored_path:
        return None

    path_value = str(stored_path).strip()

    if _is_r2_path(path_value):
        return None

    stored = Path(path_value)

    project_root = Path.cwd().resolve()

    if stored.is_absolute():
        candidates = [
            stored,
        ]
    else:
        candidates = [
            project_root / stored,
            project_root / "media" / stored,
            project_root / "media" / "audio" / stored.name,
        ]

    for candidate in candidates:
        try:
            resolved = candidate.resolve()

            # Never serve files outside the application directory.
            resolved.relative_to(project_root)

        except (ValueError, OSError):
            continue

        if resolved.exists() and resolved.is_file():
            return resolved

    return None


# ======================================================================
# PUBLIC MUSIC
# ======================================================================

@router.get("/beats")
def browse_beats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    tracks = (
        db.query(Track)
        .filter(Track.is_published.is_(True))
        .order_by(Track.created_at.desc())
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
            title="Beats",
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
        .filter(Track.is_published.is_(True))
        .order_by(Track.created_at.desc())
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
# ALBUM DETAIL
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
        .filter(Album.slug == slug)
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
        for track in (getattr(profile, "tracks", None) or [])
        if getattr(track, "is_published", True)
    ]

    public_tracks = []

    for track in tracks:
        sales_model = getattr(
            track,
            "sales_model",
            None,
        )

        sales_model_value = getattr(
            sales_model,
            "value",
            str(sales_model)
            if sales_model is not None
            else "",
        )

        if (
            str(sales_model_value).lower() == "exclusive"
            and getattr(track, "is_sold", False)
        ):
            continue

        public_tracks.append(track)

    albums = [
        album
        for album in (getattr(profile, "albums", None) or [])
        if getattr(album, "is_published", True)
    ]

    creator = getattr(
        profile,
        "user",
        None,
    )

    return templates.TemplateResponse(
        request,
        "profile_detail.html",
        ctx(
            request,
            current_user,
            profile=profile,
            creator=creator,
            tracks=public_tracks,
            albums=albums,
        ),
    )


# ======================================================================
# PUBLIC STORE COMPATIBILITY
# ======================================================================

@router.get("/store/{slug}")
def store_detail(
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
            detail="Creator store not found.",
        )

    tracks = []

    for track in (getattr(profile, "tracks", None) or []):
        if not getattr(track, "is_published", True):
            continue

        sales_model = getattr(
            track,
            "sales_model",
            None,
        )

        sales_model_value = getattr(
            sales_model,
            "value",
            str(sales_model)
            if sales_model is not None
            else "",
        )

        if (
            str(sales_model_value).lower() == "exclusive"
            and getattr(track, "is_sold", False)
        ):
            continue

        tracks.append(track)

    albums = [
        album
        for album in (getattr(profile, "albums", None) or [])
        if getattr(album, "is_published", True)
    ]

    creator = getattr(
        profile,
        "user",
        None,
    )

    return templates.TemplateResponse(
        request,
        "profile_detail.html",
        ctx(
            request,
            current_user,
            profile=profile,
            creator=creator,
            tracks=tracks,
            albums=albums,
        ),
    )


# ======================================================================
# PURCHASE DOWNLOAD
# ======================================================================
#
# Supports:
#
#   /download/track/{track_id}
#   /download/{track_id}
#   /download/track/{track_slug}
#   /download/{track_slug}
#
# R2:
#
#   r2://bucket/audio/file.mp3
#
# Local compatibility:
#
#   media/audio/file.mp3
#   audio/file.mp3
#
# The buyer must have:
#
#   - a License
#   - matching track
#   - matching buyer
#   - COMPLETED order
#
# ======================================================================

@router.get("/download/track/{track_ref}")
@router.get("/download/{track_ref}")
def download_track(
    track_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # ------------------------------------------------------------------
    # FIND TRACK BY ID
    # ------------------------------------------------------------------

    track = (
        db.query(Track)
        .filter(Track.id == track_ref)
        .first()
    )

    # ------------------------------------------------------------------
    # FIND TRACK BY SLUG
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # VERIFY OWNERSHIP
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # AUDIO PATH
    # ------------------------------------------------------------------

    stored_path = getattr(
        track,
        "audio_file_path",
        None,
    )

    if not stored_path:
        raise HTTPException(
            status_code=404,
            detail="Audio file is not available.",
        )

    # ------------------------------------------------------------------
    # SAFE DOWNLOAD NAME
    # ------------------------------------------------------------------

    title = getattr(
        track,
        "title",
        None,
    ) or "BeatHub-Track"

    safe_title = "".join(
        character
        for character in title
        if character.isalnum()
        or character in (" ", "-", "_", ".")
    ).strip()

    if not safe_title:
        safe_title = "BeatHub-Track"

    # ------------------------------------------------------------------
    # CLOUDFLARE R2
    # ------------------------------------------------------------------

    if _is_r2_path(stored_path):
        object_key = _r2_object_key(
            stored_path
        )

        if not object_key:
            raise HTTPException(
                status_code=404,
                detail="The stored R2 audio path is invalid.",
            )

        extension = Path(object_key).suffix.lower()

        download_name = (
            f"{safe_title}{extension}"
            if extension
            else safe_title
        )

        signed_url = _r2_download_url(
            object_key=object_key,
            filename=download_name,
            expires=settings.R2_DOWNLOAD_URL_EXPIRES,
        )

        return RedirectResponse(
            url=signed_url,
            status_code=307,
            headers={
                "Cache-Control": "private, no-store",
            },
        )

    # ------------------------------------------------------------------
    # LOCAL STORAGE COMPATIBILITY
    # ------------------------------------------------------------------

    audio_path = _local_audio_path(
        stored_path
    )

    if audio_path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The purchased audio file is unavailable. "
                "The file may have been moved or removed from storage."
            ),
        )

    extension = audio_path.suffix.lower()

    download_name = (
        f"{safe_title}{extension}"
        if extension
        else safe_title
    )

    # ------------------------------------------------------------------
    # LOCAL DOWNLOAD
    # ------------------------------------------------------------------

    return FileResponse(
        path=str(audio_path),
        media_type="application/octet-stream",
        filename=download_name,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{download_name}"'
            ),
            "Cache-Control": "private, no-store",
        },
    )
