from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

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


def slugify(value: str) -> str:
    value = value.strip().lower()
    cleaned = "".join(
        ch if ch.isalnum() else "-"
        for ch in value
    )
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or f"track-{uuid4().hex[:8]}"


def unique_track_slug(db: Session, title: str) -> str:
    base = slugify(title)
    slug = base
    counter = 2

    while db.query(Track).filter(Track.slug == slug).first():
        slug = f"{base}-{counter}"
        counter += 1

    return slug


def unique_album_slug(db: Session, title: str) -> str:
    base = slugify(title)
    slug = base
    counter = 2

    while db.query(Album).filter(Album.slug == slug).first():
        slug = f"{base}-{counter}"
        counter += 1

    return slug


async def save_upload(
    upload: UploadFile,
    directory: Path,
    prefix: str,
) -> str:
    """
    Stream the upload to disk in chunks instead of loading the whole
    music file into memory.
    """
    original_name = Path(upload.filename or "").name
    extension = Path(original_name).suffix.lower()

    filename = f"{prefix}_{uuid4().hex}{extension}"

    directory.mkdir(parents=True, exist_ok=True)

    destination = directory / filename

    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)

            if not chunk:
                break

            output.write(chunk)

    await upload.close()

    return str(destination)


# ----------------------------------------------------------------------
# MAIN DASHBOARD
# ----------------------------------------------------------------------

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
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

    youtube_url = (
        f"https://www.youtube.com/channel/"
        f"{settings.YOUTUBE_CHANNEL_ID}"
    )

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
            youtube_url=youtube_url,
            discord_url=settings.DISCORD_INVITE_URL,
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

    titles: list[str] = Form(...),
    descriptions: list[str] = Form(default=[]),
    genres: list[str] = Form(default=[]),
    bpms: list[str] = Form(default=[]),
    tags_list: list[str] = Form(default=[]),
    prices: list[str] = Form(...),
    sales_models: list[str] = Form(default=[]),

    audio_files: list[UploadFile] = File(...),
    cover_files: list[UploadFile | None] = File(default=[]),
):
    profile = user.profile

    if not profile:
        return RedirectResponse(
            url="/dashboard?error=Creator profile not found.",
            status_code=303,
        )

    if not audio_files:
        return RedirectResponse(
            url="/dashboard/upload?error=Please select at least one audio file.",
            status_code=303,
        )

    if len(titles) != len(audio_files):
        return RedirectResponse(
            url="/dashboard/upload?error=Each track must have an audio file.",
            status_code=303,
        )

    if len(prices) != len(audio_files):
        return RedirectResponse(
            url="/dashboard/upload?error=Each track must have a price.",
            status_code=303,
        )

    media_root = Path(settings.MEDIA_ROOT)

    audio_directory = media_root / "audio"
    cover_directory = media_root / "covers"

    try:
        for index, audio_file in enumerate(audio_files):

            title = (
                titles[index].strip()
                if index < len(titles)
                else ""
            )

            if not title:
                raise ValueError(
                    f"Track {index + 1}: title is required."
                )

            audio_name = (audio_file.filename or "").lower()

            allowed_audio = {
                ".mp3",
                ".wav",
                ".m4a",
                ".flac",
            }

            audio_extension = Path(audio_name).suffix

            if audio_extension not in allowed_audio:
                raise ValueError(
                    f"{title}: unsupported audio format."
                )

            try:
                price = Decimal(prices[index])
            except Exception:
                raise ValueError(
                    f"{title}: invalid price."
                )

            if price < 0:
                raise ValueError(
                    f"{title}: price cannot be negative."
                )

            sales_model_value = (
                sales_models[index]
                if index < len(sales_models)
                else "non_exclusive"
            )

            if sales_model_value not in {
                "exclusive",
                "non_exclusive",
            }:
                sales_model_value = "non_exclusive"

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

            bpm_value = None

            if index < len(bpms) and bpms[index].strip():
                try:
                    bpm_value = int(bpms[index])
                except ValueError:
                    raise ValueError(
                        f"{title}: BPM must be a number."
                    )

            audio_path = await save_upload(
                audio_file,
                audio_directory,
                "audio",
            )

            cover_path = None

            cover_file = (
                cover_files[index]
                if index < len(cover_files)
                else None
            )

            if cover_file and cover_file.filename:

                cover_extension = Path(
                    cover_file.filename
                ).suffix.lower()

                allowed_covers = {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                }

                if cover_extension not in allowed_covers:
                    raise ValueError(
                        f"{title}: unsupported cover image format."
                    )

                cover_path = await save_upload(
                    cover_file,
                    cover_directory,
                    "cover",
                )

            track = Track(
                creator_profile_id=profile.id,
                title=title,
                slug=unique_track_slug(db, title),
                description=description or None,
                genre=genre or None,
                bpm=bpm_value,
                tags=tags or None,
                cover_art_path=cover_path,
                audio_file_path=audio_path,
                preview_file_path=None,
                price=price,
                sales_model=SalesModel(sales_model_value),
                is_sold=False,
                is_published=True,
            )

            db.add(track)

        db.commit()

    except Exception as exc:
        db.rollback()

        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error=str(exc),
            ),
            status_code=400,
        )

    return RedirectResponse(
        url="/dashboard?success=Track(s) uploaded successfully.",
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
    track_ids: list[str] = Form(default=[]),
    artwork: UploadFile | None = File(default=None),
):
    profile = user.profile

    if not profile:
        return RedirectResponse(
            url="/dashboard?error=Creator profile not found.",
            status_code=303,
        )

    title = title.strip()

    if not title:
        return RedirectResponse(
            url="/dashboard/albums/new?error=Album title is required.",
            status_code=303,
        )

    try:
        artwork_path = None

        if artwork and artwork.filename:

            extension = Path(
                artwork.filename
            ).suffix.lower()

            if extension not in {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            }:
                raise ValueError(
                    "Unsupported artwork format."
                )

            artwork_path = await save_upload(
                artwork,
                Path(settings.MEDIA_ROOT) / "artwork",
                "album",
            )

        album = Album(
            creator_profile_id=profile.id,
            title=title,
            slug=unique_album_slug(db, title),
            description=description.strip() or None,
            genre=genre.strip() or None,
            artwork_path=artwork_path,
            release_date=None,
            is_published=True,
        )

        db.add(album)
        db.flush()

        if track_ids:

            tracks = (
                db.query(Track)
                .filter(
                    Track.id.in_(track_ids),
                    Track.creator_profile_id == profile.id,
                )
                .all()
            )

            track_map = {
                track.id: track
                for track in tracks
            }

            for position, track_id in enumerate(track_ids):

                track = track_map.get(track_id)

                if not track:
                    continue

                db.add(
                    AlbumTrack(
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
                tracks=(
                    db.query(Track)
                    .filter(
                        Track.creator_profile_id == profile.id
                    )
                    .order_by(Track.created_at.desc())
                    .all()
                ),
                error=str(exc),
            ),
            status_code=400,
        )

    return RedirectResponse(
        url="/dashboard?success=Album created successfully.",
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
