from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from typing import List, Optional

import boto3

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import (
    WithdrawalRequest,
    WithdrawalStatus,
)
from app.models.music import (
    Album,
    AlbumTrack,
    Track,
    SalesModel,
)
from app.models.order import (
    Order,
    OrderStatus,
)
from app.models.user import (
    User,
    UserRole,
)
from app.services.producer_ranking import (
    get_top_producers,
)
from app.utils.deps import (
    require_creator,
    require_user,
)


router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(
    directory="app/templates"
)


# ======================================================================
# R2
# ======================================================================

def get_r2_client():

    if not settings.r2_enabled:
        raise RuntimeError(
            "Cloudflare R2 is not configured."
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def upload_to_r2(
    upload: UploadFile,
    key: str,
    content_type: Optional[str] = None,
):

    client = get_r2_client()

    upload.file.seek(0)

    if content_type:
        client.upload_fileobj(
            upload.file,
            settings.R2_BUCKET_NAME,
            key,
            ExtraArgs={
                "ContentType": content_type
            },
        )
    else:
        client.upload_fileobj(
            upload.file,
            settings.R2_BUCKET_NAME,
            key,
        )


def upload_file_to_r2(
    file_path: str,
    key: str,
    content_type: Optional[str] = None,
):

    client = get_r2_client()

    with open(file_path, "rb") as file_obj:

        if content_type:
            client.upload_fileobj(
                file_obj,
                settings.R2_BUCKET_NAME,
                key,
                ExtraArgs={
                    "ContentType": content_type
                },
            )
        else:
            client.upload_fileobj(
                file_obj,
                settings.R2_BUCKET_NAME,
                key,
            )


def r2_key(
    path: Optional[str],
) -> Optional[str]:

    if not path:
        return None

    value = str(path).strip()

    if value.startswith("r2://"):

        parts = value[5:].split("/", 1)

        if len(parts) == 2:
            return parts[1]

    if (
        value.startswith("http://")
        or value.startswith("https://")
    ):
        return None

    return value.lstrip("/")


def r2_presigned_url(
    path: Optional[str],
    expires: Optional[int] = None,
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
        ExpiresIn=(
            expires
            or settings.R2_PUBLIC_URL_EXPIRES
        ),
    )


# ======================================================================
# AUDIO PREVIEW
# ======================================================================

def create_audio_preview(
    source_path: str,
    preview_path: str,
    duration: int = 30,
):

    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        raise RuntimeError(
            "FFmpeg is not installed on the server. "
            "Please install FFmpeg on Render."
        )

    command = [
        ffmpeg,
        "-y",
        "-i",
        source_path,
        "-t",
        str(duration),
        "-vn",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-map_metadata",
        "-1",
        preview_path,
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Could not create audio preview."
        )

    if not os.path.exists(preview_path):
        raise RuntimeError(
            "Audio preview was not created."
        )

    if os.path.getsize(preview_path) == 0:
        raise RuntimeError(
            "Audio preview is empty."
        )


def create_and_upload_preview(
    upload: UploadFile,
    preview_key: str,
):

    upload.file.seek(0)

    with tempfile.TemporaryDirectory() as temp_dir:

        source_ext = extension(
            upload.filename
        )

        if not source_ext:
            source_ext = ".audio"

        source_path = os.path.join(
            temp_dir,
            f"source{source_ext}",
        )

        preview_path = os.path.join(
            temp_dir,
            "preview.mp3",
        )

        with open(
            source_path,
            "wb",
        ) as output_file:

            shutil.copyfileobj(
                upload.file,
                output_file,
            )

        create_audio_preview(
            source_path,
            preview_path,
            duration=30,
        )

        upload_file_to_r2(
            preview_path,
            preview_key,
            "audio/mpeg",
        )


# ======================================================================
# TEMPLATE CONTEXT
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

def safe_filename(
    filename: str,
) -> str:

    filename = os.path.basename(
        filename or ""
    )

    filename = re.sub(
        r"[^a-zA-Z0-9._-]+",
        "_",
        filename,
    )

    return filename or "upload"


def make_slug(
    value: str,
) -> str:

    slug = re.sub(
        r"[^a-zA-Z0-9]+",
        "-",
        (value or "").strip(),
    ).strip("-").lower()

    return (
        slug
        or f"track-{uuid.uuid4().hex[:8]}"
    )


def unique_track_slug(
    db: Session,
    title: str,
) -> str:

    base = make_slug(title)

    slug = base
    number = 2

    while (
        db.query(Track)
        .filter(Track.slug == slug)
        .first()
    ):

        slug = f"{base}-{number}"
        number += 1

    return slug


def extension(
    filename: str,
) -> str:

    return os.path.splitext(
        filename or ""
    )[1].lower()


def build_absolute_url(
    request: Request,
    path: str,
) -> str:

    base = str(
        request.base_url
    ).rstrip("/")

    if (
        settings.is_production
        and base.startswith("http://")
    ):

        base = (
            "https://"
            + base[len("http://"):]
        )

    return (
        f"{base}/{path.lstrip('/')}"
    )


# ======================================================================
# CREATOR STATS
# ======================================================================

def get_stats(
    db: Session,
    profile_id: str,
):

    completed_orders = (
        db.query(Order)
        .join(
            Track,
            Order.track_id == Track.id,
        )
        .filter(
            Track.creator_profile_id == profile_id,
            Order.status == OrderStatus.COMPLETED,
        )
        .order_by(
            Order.completed_at.desc()
        )
        .all()
    )

    total_sales = len(
        completed_orders
    )

    gross = sum(
        (
            Decimal(
                str(
                    order.gross_amount or 0
                )
            )
            for order in completed_orders
        ),
        Decimal("0"),
    )

    commission = sum(
        (
            Decimal(
                str(
                    order.commission_amount or 0
                )
            )
            for order in completed_orders
        ),
        Decimal("0"),
    )

    net = sum(
        (
            Decimal(
                str(
                    order.net_amount or 0
                )
            )
            for order in completed_orders
        ),
        Decimal("0"),
    )

    paid_withdrawals = (
        db.query(
            func.coalesce(
                func.sum(
                    WithdrawalRequest.amount
                ),
                0,
            )
        )
        .filter(
            WithdrawalRequest.creator_profile_id
            == profile_id,
            WithdrawalRequest.status
            == WithdrawalStatus.PAID,
        )
        .scalar()
    )

    pending_withdrawals = (
        db.query(
            func.coalesce(
                func.sum(
                    WithdrawalRequest.amount
                ),
                0,
            )
        )
        .filter(
            WithdrawalRequest.creator_profile_id
            == profile_id,
            WithdrawalRequest.status.in_(
                [
                    WithdrawalStatus.PENDING,
                    WithdrawalStatus.APPROVED,
                    WithdrawalStatus.PROCESSING,
                ]
            ),
        )
        .scalar()
    )

    paid_withdrawals = Decimal(
        str(
            paid_withdrawals or 0
        )
    )

    pending_withdrawals = Decimal(
        str(
            pending_withdrawals or 0
        )
    )

    available_balance = (
        net
        - paid_withdrawals
        - pending_withdrawals
    )

    if available_balance < 0:
        available_balance = Decimal("0")

    return {
        "total_sales": total_sales,
        "gross_revenue": gross,
        "platform_commission": commission,
        "net_earnings": net,
        "available_balance": available_balance,
        "pending_withdrawal": pending_withdrawals,
        "recent_orders": completed_orders[:8],
    }


# ======================================================================
# CREATOR DASHBOARD
# ======================================================================

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    page: int = 1,
    q: str = "",
):

    if user.role == UserRole.BUYER:

        return artist_dashboard_content(
            request=request,
            db=db,
            user=user,
        )

    if user.role not in (
        UserRole.CREATOR,
        UserRole.ADMIN,
    ):

        return RedirectResponse(
            url="/account",
            status_code=303,
        )

    profile = user.profile

    if not profile:

        return RedirectResponse(
            url="/?error=Creator profile not found.",
            status_code=303,
        )

    stats = get_stats(
        db,
        profile.id,
    )

    # ----------------------------------------------------------
    # TOP PRODUCERS
    # ----------------------------------------------------------

    try:

        top_producers = get_top_producers(
            db,
            days=30,
            limit=5,
        )

    except Exception:

        # Ranking is a marketing enhancement.
        # A ranking failure must never break the
        # producer dashboard itself.
        top_producers = []

    track_count = (
        db.query(Track)
        .filter(
            Track.creator_profile_id
            == profile.id
        )
        .count()
    )

    album_count = (
        db.query(Album)
        .filter(
            Album.creator_profile_id
            == profile.id
        )
        .count()
    )

    try:
        page = int(page)

    except (
        TypeError,
        ValueError,
    ):

        page = 1

    if page < 1:
        page = 1

    track_per_page = 12

    tracks_query = (
        db.query(Track)
        .filter(
            Track.creator_profile_id
            == profile.id
        )
    )

    q = (q or "").strip()

    if q:

        search_term = f"%{q}%"

        tracks_query = tracks_query.filter(
            Track.title.ilike(search_term)
            | Track.genre.ilike(search_term)
            | Track.tags.ilike(search_term)
        )

    track_total = tracks_query.count()

    track_total_pages = max(
        1,
        (
            track_total
            + track_per_page
            - 1
        )
        // track_per_page,
    )

    if page > track_total_pages:
        page = track_total_pages

    track_offset = (
        (page - 1)
        * track_per_page
    )

    tracks = (
        tracks_query
        .order_by(
            Track.created_at.desc()
        )
        .offset(track_offset)
        .limit(track_per_page)
        .all()
    )

    for track in tracks:

        track.cover_art_url = None

        if track.cover_art_path:

            try:

                track.cover_art_url = (
                    r2_presigned_url(
                        track.cover_art_path
                    )
                )

            except Exception:

                track.cover_art_url = None

    track_page = page
    track_search = q
    track_total_count = track_total

    track_start = (
        track_offset + 1
        if track_total
        else 0
    )

    track_end = min(
        track_offset + len(tracks),
        track_total,
    )

    youtube_url = (
        "https://www.youtube.com/channel/"
        f"{settings.YOUTUBE_CHANNEL_ID}"
    )

    store_url = build_absolute_url(
        request,
        f"/profile/{profile.slug}",
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        ctx(
            request,
            user,
            profile=profile,
            stats=stats,
            top_producers=top_producers,
            track_count=track_count,
            album_count=album_count,
            tracks=tracks,
            track_page=track_page,
            track_total_pages=track_total_pages,
            track_total=track_total,
            track_total_count=track_total_count,
            track_per_page=track_per_page,
            track_search=track_search,
            track_start=track_start,
            track_end=track_end,
            q=q,
            youtube_url=youtube_url,
            discord_url=settings.DISCORD_INVITE_URL,
            store_url=store_url,
        ),
    )


# ======================================================================
# PUBLIC 30-SECOND PREVIEW
# ======================================================================

@router.get("/preview/track/{track_id}")
def preview_track(
    track_id: str,
    db: Session = Depends(get_db),
):

    track = (
        db.query(Track)
        .filter(
            Track.id == track_id,
            Track.is_published.is_(True),
        )
        .first()
    )

    if not track:

        return RedirectResponse(
            url="/",
            status_code=303,
        )

    if not track.preview_file_path:

        return RedirectResponse(
            url="/track/" + track.slug,
            status_code=303,
        )

    try:

        preview_url = r2_presigned_url(
            track.preview_file_path,
            expires=300,
        )

    except Exception:

        preview_url = None

    if not preview_url:

        return RedirectResponse(
            url="/track/" + track.slug,
            status_code=303,
        )

    return RedirectResponse(
        url=preview_url,
        status_code=307,
    )


# ======================================================================
# BUYER DASHBOARD CONTENT
# ======================================================================

def artist_dashboard_content(
    request: Request,
    db: Session,
    user: User,
):

    purchases = (
        db.query(Order)
        .filter(
            Order.buyer_id == user.id,
            Order.status == OrderStatus.COMPLETED,
        )
        .order_by(
            Order.completed_at.desc()
        )
        .all()
    )

    for purchase in purchases:

        purchase.download_url = None
        purchase.creator_name = None

        track = purchase.track

        if track:

            creator_profile = getattr(
                track,
                "creator_profile",
                None,
            )

            if creator_profile:

                purchase.creator_name = (
                    getattr(
                        creator_profile,
                        "stage_name",
                        None,
                    )
                    or "BeatHub Creator"
                )

            if track.audio_file_path:

                try:

                    purchase.download_url = (
                        r2_presigned_url(
                            track.audio_file_path,
                            expires=3600,
                        )
                    )

                except Exception:

                    purchase.download_url = None

    total_purchases = len(
        purchases
    )

    total_spent = sum(
        (
            Decimal(
                str(
                    purchase.gross_amount or 0
                )
            )
            for purchase in purchases
        ),
        Decimal("0"),
    )

    profile = getattr(
        user,
        "profile",
        None,
    )

    display_name = (
        getattr(
            profile,
            "stage_name",
            None,
        )
        if profile
        else None
    )

    if not display_name:

        display_name = (
            user.email.split("@")[0]
        )

    return templates.TemplateResponse(
        request,
        "artist_dashboard.html",
        ctx(
            request,
            user,
            profile=profile,
            display_name=display_name,
            purchases=purchases,
            total_purchases=total_purchases,
            total_spent=total_spent,
        ),
    )


# ======================================================================
# BUYER / ARTIST DASHBOARD
# ======================================================================

@router.get("/artist/dashboard")
@router.get("/artist/dashboard/")
def artist_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):

    if user.role in (
        UserRole.CREATOR,
        UserRole.ADMIN,
    ):

        return dashboard_home(
            request=request,
            db=db,
            user=user,
            page=1,
            q="",
        )

    if user.role == UserRole.BUYER:

        return artist_dashboard_content(
            request=request,
            db=db,
            user=user,
        )

    return RedirectResponse(
        url="/account",
        status_code=303,
    )


# ======================================================================
# UPLOAD PAGE
# ======================================================================

@router.get("/dashboard/upload")
def upload_page(
    request: Request,
    user: User = Depends(require_creator),
):

    return templates.TemplateResponse(
        request,
        "upload_track.html",
        ctx(
            request,
            user,
        ),
    )


# ======================================================================
# UPLOAD TRACKS
# ======================================================================

@router.post("/dashboard/upload")
async def upload_tracks(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    titles: List[str] = Form(...),
    descriptions: List[str] = Form(default=[]),
    genres: List[str] = Form(default=[]),
    bpms: List[str] = Form(default=[]),
    tags_list: List[str] = Form(default=[]),
    prices: List[str] = Form(...),
    sales_models: List[str] = Form(default=[]),
    audio_files: List[UploadFile] = File(...),
    cover_files: List[
        Optional[UploadFile]
    ] = File(default=[]),
):

    profile = user.profile

    if not profile:

        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error="Creator profile not found.",
            ),
            status_code=400,
        )

    if not settings.r2_enabled:

        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error=(
                    "Cloud storage is not configured. "
                    "Please configure Cloudflare R2."
                ),
            ),
            status_code=500,
        )

    count = len(titles)

    if count == 0:

        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error="Please add at least one track.",
            ),
            status_code=400,
        )

    if len(audio_files) != count:

        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error=(
                    "Each track must have an audio file."
                ),
            ),
            status_code=400,
        )

    created_tracks = []

    try:

        for index in range(count):

            title = titles[index].strip()

            if not title:

                raise ValueError(
                    f"Track {index + 1}: "
                    "title is required."
                )

            audio = audio_files[index]

            if not audio or not audio.filename:

                raise ValueError(
                    f"Track {index + 1}: "
                    "audio file is required."
                )

            audio_ext = extension(
                audio.filename
            )

            if audio_ext not in {
                ".mp3",
                ".wav",
                ".m4a",
                ".flac",
            }:

                raise ValueError(
                    f"Track {index + 1}: "
                    "unsupported audio format."
                )

            try:

                price_value = Decimal(
                    prices[index].strip()
                )

            except (
                InvalidOperation,
                ValueError,
            ):

                raise ValueError(
                    f"Track {index + 1}: "
                    "invalid price."
                )

            if price_value < 0:

                raise ValueError(
                    f"Track {index + 1}: "
                    "price cannot be negative."
                )

            sales_value = (
                sales_models[index]
                .strip()
                .lower()
                if index < len(sales_models)
                else "non_exclusive"
            )

            if sales_value not in {
                "exclusive",
                "non_exclusive",
            }:

                sales_value = "non_exclusive"

            bpm_value = None

            if (
                index < len(bpms)
                and bpms[index].strip()
            ):

                try:

                    bpm_value = int(
                        bpms[index].strip()
                    )

                except ValueError:

                    raise ValueError(
                        f"Track {index + 1}: "
                        "BPM must be a number."
                    )

            track_id = str(uuid.uuid4())

            slug = unique_track_slug(
                db,
                title,
            )

            audio_key = (
                f"audio/"
                f"{track_id}"
                f"{audio_ext}"
            )

            preview_key = (
                f"previews/"
                f"{track_id}.mp3"
            )

            create_and_upload_preview(
                audio,
                preview_key,
            )

            audio.file.seek(0)

            upload_to_r2(
                audio,
                audio_key,
                audio.content_type
                or "application/octet-stream",
            )

            cover_path = None

            cover = (
                cover_files[index]
                if index < len(cover_files)
                else None
            )

            if cover and cover.filename:

                cover_ext = extension(
                    cover.filename
                )

                if cover_ext not in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                }:

                    raise ValueError(
                        f"Track {index + 1}: "
                        "unsupported cover-art format."
                    )

                cover_key = (
                    f"covers/"
                    f"{track_id}"
                    f"{cover_ext}"
                )

                upload_to_r2(
                    cover,
                    cover_key,
                    cover.content_type
                    or "application/octet-stream",
                )

                cover_path = (
                    f"r2://"
                    f"{settings.R2_BUCKET_NAME}/"
                    f"{cover_key}"
                )

            description = (
                descriptions[index].strip()
                if index < len(descriptions)
                else ""
            )

            genre = (
                genres[index].strip()
                if index < len(genres)
                else ""
            )

            tags = (
                tags_list[index].strip()
                if index < len(tags_list)
                else ""
            )

            track = Track(
                id=track_id,
                creator_profile_id=profile.id,
                title=title,
                slug=slug,
                description=(
                    description or None
                ),
                genre=genre or None,
                bpm=bpm_value,
                tags=tags or None,
                cover_art_path=cover_path,
                audio_file_path=(
                    f"r2://"
                    f"{settings.R2_BUCKET_NAME}/"
                    f"{audio_key}"
                ),
                preview_file_path=(
                    f"r2://"
                    f"{settings.R2_BUCKET_NAME}/"
                    f"{preview_key}"
                ),
                price=price_value,
                sales_model=(
                    SalesModel.EXCLUSIVE
                    if sales_value
                    == "exclusive"
                    else SalesModel.NON_EXCLUSIVE
                ),
                is_sold=False,
                is_published=True,
            )

            db.add(track)
            created_tracks.append(track)

        db.commit()

    except Exception as exc:

        db.rollback()

        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error=(
                    f"Upload failed: {str(exc)}"
                ),
            ),
            status_code=400,
        )

    return RedirectResponse(
        url=(
            "/dashboard?"
            f"success={len(created_tracks)} "
            "track(s) uploaded successfully."
        ),
        status_code=303,
    )


# ======================================================================
# NEW ALBUM PAGE
# ======================================================================

@router.get("/dashboard/albums/new")
def new_album_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):

    profile = user.profile

    if not profile:

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Creator profile not found."
            ),
            status_code=303,
        )

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id
            == profile.id
        )
        .order_by(
            Track.created_at.desc()
        )
        .all()
    )

    return templates.TemplateResponse(
        request,
        "upload_album.html",
        ctx(
            request,
            user,
            tracks=tracks,
        ),
    )


# ======================================================================
# CREATE ALBUM
# ======================================================================

@router.post("/dashboard/albums/new")
async def create_album(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    artwork: Optional[UploadFile] = File(None),
    track_ids: List[str] = Form(default=[]),
):

    profile = user.profile

    if not profile:

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Creator profile not found."
            ),
            status_code=303,
        )

    if not settings.r2_enabled:

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Cloud storage is not configured."
            ),
            status_code=303,
        )

    title = title.strip()

    existing_tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id
            == profile.id
        )
        .order_by(
            Track.created_at.desc()
        )
        .all()
    )

    if not title:

        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=existing_tracks,
                error="Album title is required.",
            ),
            status_code=400,
        )

    if not track_ids:

        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=existing_tracks,
                error=(
                    "Select at least one track "
                    "for the album."
                ),
            ),
            status_code=400,
        )

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id
            == profile.id,
            Track.id.in_(track_ids),
        )
        .all()
    )

    if len(tracks) != len(
        set(track_ids)
    ):

        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=existing_tracks,
                error=(
                    "One or more selected "
                    "tracks are invalid."
                ),
            ),
            status_code=400,
        )

    base_slug = make_slug(title)

    slug = base_slug
    suffix = 2

    while (
        db.query(Album)
        .filter(Album.slug == slug)
        .first()
    ):

        slug = (
            f"{base_slug}-{suffix}"
        )

        suffix += 1

    artwork_path = None

    try:

        if artwork and artwork.filename:

            artwork_ext = extension(
                artwork.filename
            )

            if artwork_ext not in {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            }:

                raise ValueError(
                    "Unsupported album "
                    "artwork format."
                )

            artwork_key = (
                "covers/albums/"
                f"{uuid.uuid4()}"
                f"{artwork_ext}"
            )

            upload_to_r2(
                artwork,
                artwork_key,
                artwork.content_type
                or "application/octet-stream",
            )

            artwork_path = (
                f"r2://"
                f"{settings.R2_BUCKET_NAME}/"
                f"{artwork_key}"
            )

        album = Album(
            id=str(uuid.uuid4()),
            creator_profile_id=profile.id,
            title=title,
            slug=slug,
            description=(
                description.strip()
                or None
            ),
            genre=(
                genre.strip()
                or None
            ),
            artwork_path=artwork_path,
            is_published=True,
        )

        db.add(album)
        db.flush()

        track_map = {
            track.id: track
            for track in tracks
        }

        for position, track_id in enumerate(
            track_ids
        ):

            track = track_map.get(
                track_id
            )

            if track:

                db.add(
                    AlbumTrack(
                        id=str(uuid.uuid4()),
                        album_id=album.id,
                        track_id=track.id,
                        position=position,
                    )
                )

        db.commit()

    except Exception as exc:

        db.rollback()

        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=existing_tracks,
                error=(
                    "Album creation failed: "
                    f"{str(exc)}"
                ),
            ),
            status_code=400,
        )

    return RedirectResponse(
        url=f"/album/{slug}",
        status_code=303,
    )


# ======================================================================
# WITHDRAWAL
# ======================================================================

@router.post("/dashboard/withdraw")
def request_withdrawal(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    amount: str = Form(...),
    phone_number: str = Form(...),
):

    profile = user.profile

    if not profile:

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Creator profile not found."
            ),
            status_code=303,
        )

    try:

        amount_value = Decimal(
            amount
        )

    except Exception:

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Invalid withdrawal amount."
            ),
            status_code=303,
        )

    if amount_value <= 0:

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Amount must be greater than zero."
            ),
            status_code=303,
        )

    stats = get_stats(
        db,
        profile.id,
    )

    if (
        amount_value
        > stats["available_balance"]
    ):

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Insufficient available balance."
            ),
            status_code=303,
        )

    phone_number = (
        phone_number.strip()
    )

    if not phone_number:

        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=M-Pesa phone number is required."
            ),
            status_code=303,
        )

    withdrawal = WithdrawalRequest(
        creator_profile_id=profile.id,
        amount=amount_value,
        phone_number=phone_number,
        status=WithdrawalStatus.PENDING,
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url=(
            "/dashboard?"
            "success=Withdrawal request submitted."
        ),
        status_code=303,
    )
