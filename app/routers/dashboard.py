from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
import re
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest, WithdrawalStatus
from app.models.music import Album, AlbumTrack, SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.utils.deps import require_creator

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


def ctx(request: Request, current_user, **extra):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }
    data.update(extra)
    return data


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def safe_filename(filename: str) -> str:
    filename = os.path.basename(filename or "")
    filename = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename)
    return filename or "upload"


def make_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or f"track-{uuid.uuid4().hex[:8]}"


def unique_track_slug(db: Session, title: str) -> str:
    base = make_slug(title)
    slug = base
    number = 2

    while db.query(Track).filter(Track.slug == slug).first():
        slug = f"{base}-{number}"
        number += 1

    return slug


def ensure_media_dirs():
    root = Path(settings.MEDIA_ROOT)

    audio_dir = root / "audio"
    preview_dir = root / "previews"
    cover_dir = root / "covers"

    audio_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    cover_dir.mkdir(parents=True, exist_ok=True)

    return audio_dir, preview_dir, cover_dir


async def save_upload(upload: UploadFile, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)

    await upload.close()
    return str(destination)


def extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


# ----------------------------------------------------------------------
# STATS
# ----------------------------------------------------------------------

def get_stats(db: Session, profile_id: str):
    completed_orders = (
        db.query(Order)
        .join(Track, Order.track_id == Track.id)
        .filter(
            Track.creator_profile_id == profile_id,
            Order.status == OrderStatus.COMPLETED,
        )
        .order_by(Order.completed_at.desc())
        .all()
    )

    total_sales = len(completed_orders)

    gross = sum(
        (Decimal(str(o.gross_amount or 0)) for o in completed_orders),
        Decimal("0"),
    )

    commission = sum(
        (Decimal(str(o.commission_amount or 0)) for o in completed_orders),
        Decimal("0"),
    )

    net = sum(
        (Decimal(str(o.net_amount or 0)) for o in completed_orders),
        Decimal("0"),
    )

    paid_withdrawals = (
        db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0))
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id,
            WithdrawalRequest.status == WithdrawalStatus.PAID,
        )
        .scalar()
    )

    pending_withdrawals = (
        db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0))
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id,
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

    paid_withdrawals = Decimal(str(paid_withdrawals or 0))
    pending_withdrawals = Decimal(str(pending_withdrawals or 0))

    available_balance = net - paid_withdrawals - pending_withdrawals

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


# ----------------------------------------------------------------------
# DASHBOARD
# ----------------------------------------------------------------------

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    page: int = 1,
    q: str = "",
):
    profile = user.profile

    if not profile:
        return RedirectResponse(
            url="/?error=Creator profile not found.",
            status_code=303,
        )

    stats = get_stats(db, profile.id)

    track_count = (
        db.query(Track)
        .filter(Track.creator_profile_id == profile.id)
        .count()
    )

    album_count = (
        db.query(Album)
        .filter(Album.creator_profile_id == profile.id)
        .count()
    )

    # --------------------------------------------------------------
    # Dashboard track browser / search / pagination
    # --------------------------------------------------------------

    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    if page < 1:
        page = 1

    track_per_page = 12

    tracks_query = (
        db.query(Track)
        .filter(Track.creator_profile_id == profile.id)
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
        (track_total + track_per_page - 1) // track_per_page,
    )

    if page > track_total_pages:
        page = track_total_pages

    track_offset = (page - 1) * track_per_page

    tracks = (
        tracks_query
        .order_by(Track.created_at.desc())
        .offset(track_offset)
        .limit(track_per_page)
        .all()
    )

    # Keep all pagination/search values available to the existing
    # dashboard template.
    track_page = page
    track_search = q
    track_total_count = track_total
    track_start = track_offset + 1 if track_total else 0
    track_end = min(track_offset + len(tracks), track_total)

    youtube_url = (
        f"https://www.youtube.com/channel/"
        f"{settings.YOUTUBE_CHANNEL_ID}"
    )

    store_url = f"/profile/{profile.slug}"

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        ctx(
            request,
            user,
            profile=profile,
            stats=stats,
            track_count=track_count,
            album_count=album_count,
            tracks=tracks,

            # Existing dashboard pagination/search variables
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


# ----------------------------------------------------------------------
# UPLOAD PAGE
# ----------------------------------------------------------------------

@router.get("/dashboard/upload")
def upload_page(
    request: Request,
    user: User = Depends(require_creator),
):
    return templates.TemplateResponse(
        request,
        "upload_track.html",
        ctx(request, user),
    )


# ----------------------------------------------------------------------
# UPLOAD TRACKS
# ----------------------------------------------------------------------

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
    cover_files: List[Optional[UploadFile]] = File(default=[]),
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
                error="Each track must have an audio file.",
            ),
            status_code=400,
        )

    audio_dir, _, cover_dir = ensure_media_dirs()

    created_tracks = []

    try:
        for index in range(count):
            title = titles[index].strip()

            if not title:
                raise ValueError(
                    f"Track {index + 1}: title is required."
                )

            audio = audio_files[index]

            if not audio or not audio.filename:
                raise ValueError(
                    f"Track {index + 1}: audio file is required."
                )

            audio_ext = extension(audio.filename)

            if audio_ext not in {
                ".mp3",
                ".wav",
                ".m4a",
                ".flac",
            }:
                raise ValueError(
                    f"Track {index + 1}: unsupported audio format."
                )

            try:
                price_value = Decimal(
                    prices[index].strip()
                )
            except (InvalidOperation, ValueError):
                raise ValueError(
                    f"Track {index + 1}: invalid price."
                )

            if price_value < 0:
                raise ValueError(
                    f"Track {index + 1}: price cannot be negative."
                )

            sales_value = (
                sales_models[index].strip().lower()
                if index < len(sales_models)
                else "non_exclusive"
            )

            if sales_value not in {
                "exclusive",
                "non_exclusive",
            }:
                sales_value = "non_exclusive"

            bpm_value = None

            if index < len(bpms) and bpms[index].strip():
                try:
                    bpm_value = int(bpms[index].strip())
                except ValueError:
                    raise ValueError(
                        f"Track {index + 1}: BPM must be a number."
                    )

            track_id = str(uuid.uuid4())
            slug = unique_track_slug(db, title)

            audio_path = audio_dir / f"{track_id}{audio_ext}"

            await save_upload(
                audio,
                audio_path,
            )

            cover_path = None

            cover = (
                cover_files[index]
                if index < len(cover_files)
                else None
            )

            if cover and cover.filename:
                cover_ext = extension(cover.filename)

                if cover_ext not in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                }:
                    raise ValueError(
                        f"Track {index + 1}: unsupported cover-art format."
                    )

                cover_path_disk = (
                    cover_dir / f"{track_id}{cover_ext}"
                )

                await save_upload(
                    cover,
                    cover_path_disk,
                )

                cover_path = str(cover_path_disk)

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
                description=description or None,
                genre=genre or None,
                bpm=bpm_value,
                tags=tags or None,
                cover_art_path=cover_path,
                audio_file_path=str(audio_path),
                preview_file_path=None,
                price=price_value,
                sales_model=(
                    SalesModel.EXCLUSIVE
                    if sales_value == "exclusive"
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
                error=f"Upload failed: {str(exc)}",
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


# ----------------------------------------------------------------------
# ALBUMS
# ----------------------------------------------------------------------

@router.get("/dashboard/albums/new")
def new_album_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = user.profile

    if not profile:
        return RedirectResponse(
            url="/dashboard?error=Creator profile not found.",
            status_code=303,
        )

    tracks = (
        db.query(Track)
        .filter(Track.creator_profile_id == profile.id)
        .order_by(Track.created_at.desc())
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
            url="/dashboard?error=Creator profile not found.",
            status_code=303,
        )

    title = title.strip()

    existing_tracks = (
        db.query(Track)
        .filter(Track.creator_profile_id == profile.id)
        .order_by(Track.created_at.desc())
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
                error="Select at least one track for the album.",
            ),
            status_code=400,
        )

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id,
            Track.id.in_(track_ids),
        )
        .all()
    )

    if len(tracks) != len(set(track_ids)):
        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=existing_tracks,
                error="One or more selected tracks are invalid.",
            ),
            status_code=400,
        )

    base_slug = make_slug(title)
    slug = base_slug
    suffix = 2

    while db.query(Album).filter(Album.slug == slug).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    artwork_path = None

    try:
        if artwork and artwork.filename:
            artwork_ext = extension(artwork.filename)

            if artwork_ext not in {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            }:
                raise ValueError(
                    "Unsupported album artwork format."
                )

            _, _, cover_dir = ensure_media_dirs()

            artwork_disk_path = (
                cover_dir / f"{uuid.uuid4()}{artwork_ext}"
            )

            await save_upload(
                artwork,
                artwork_disk_path,
            )

            artwork_path = str(artwork_disk_path)

        album = Album(
            id=str(uuid.uuid4()),
            creator_profile_id=profile.id,
            title=title,
            slug=slug,
            description=description.strip() or None,
            genre=genre.strip() or None,
            artwork_path=artwork_path,
            is_published=True,
        )

        db.add(album)
        db.flush()

        track_map = {track.id: track for track in tracks}

        for position, track_id in enumerate(track_ids):
            track = track_map.get(track_id)

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
                error=f"Album creation failed: {str(exc)}",
            ),
            status_code=400,
        )

    return RedirectResponse(
        url=f"/album/{slug}",
        status_code=303,
    )


# ----------------------------------------------------------------------
# WITHDRAWAL
# ----------------------------------------------------------------------

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
            url="/dashboard?error=Creator profile not found.",
            status_code=303,
        )

    try:
        amount_value = Decimal(amount)
    except Exception:
        return RedirectResponse(
            url="/dashboard?error=Invalid withdrawal amount.",
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url="/dashboard?error=Amount must be greater than zero.",
            status_code=303,
        )

    stats = get_stats(db, profile.id)

    if amount_value > stats["available_balance"]:
        return RedirectResponse(
            url="/dashboard?error=Insufficient available balance.",
            status_code=303,
        )

    phone_number = phone_number.strip()

    if not phone_number:
        return RedirectResponse(
            url="/dashboard?error=M-Pesa phone number is required.",
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
        url="/dashboard?success=Withdrawal request submitted.",
        status_code=303,
    )
