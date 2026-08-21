from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.models.music import Album, AlbumTrack, SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.storage import (
    ALLOWED_AUDIO_EXT,
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    save_upload,
)
from app.utils.deps import require_creator
from app.utils.text import unique_slug


router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory="app/templates")


# ============================================================
# COMMON TEMPLATE CONTEXT
# ============================================================

def ctx(request: Request, current_user: User, **extra):
    """
    Common context for dashboard-related templates.

    Keeping all common values here prevents Jinja templates from
    crashing when a variable is not supplied by a route.
    """

    base = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,

        "profile": getattr(current_user, "profile", None),

        "stats": {},

        "available_balance": Decimal("0"),
        "pending_withdrawal": Decimal("0"),

        "total_sales": 0,
        "gross_revenue": Decimal("0"),
        "platform_commission": Decimal("0"),
        "net_earnings": Decimal("0"),

        "withdrawal_requests": [],
        "recent_orders": [],

        "track_count": 0,
        "album_count": 0,

        "tracks": [],
        "albums": [],

        "store_url": None,
        "youtube_url": None,
        "discord_url": None,

        "track_page": 1,
        "track_total_pages": 1,
        "track_total": 0,
        "track_total_count": 0,
        "track_per_page": 12,
        "track_search": "",
        "track_start": 0,
        "track_end": 0,
        "q": "",
    }

    base.update(extra)

    return base


# ============================================================
# DECIMAL HELPER
# ============================================================

def _decimal(value) -> Decimal:
    """
    Safely convert database numeric values to Decimal.
    """

    if value is None:
        return Decimal("0")

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


# ============================================================
# CREATOR STATISTICS
# ============================================================

def _creator_stats(db: Session, profile_id: str) -> dict:
    """
    Calculate creator earnings from completed orders.

    Uses the Order fields used by BeatHub:

        gross_amount
        commission_amount
        net_amount
    """

    orders = (
        db.query(Order)
        .join(Track, Order.track_id == Track.id)
        .filter(
            Track.creator_profile_id == profile_id,
            Order.status == OrderStatus.COMPLETED,
        )
        .all()
    )

    gross = sum(
        (_decimal(order.gross_amount) for order in orders),
        Decimal("0"),
    )

    commission = sum(
        (_decimal(order.commission_amount) for order in orders),
        Decimal("0"),
    )

    net = sum(
        (_decimal(order.net_amount) for order in orders),
        Decimal("0"),
    )

    withdrawn = (
        db.query(
            func.coalesce(
                func.sum(WithdrawalRequest.amount),
                0,
            )
        )
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id,
            WithdrawalRequest.status.in_(
                [
                    "approved",
                    "processing",
                    "paid",
                ]
            ),
        )
        .scalar()
    )

    pending_withdrawal = (
        db.query(
            func.coalesce(
                func.sum(WithdrawalRequest.amount),
                0,
            )
        )
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id,
            WithdrawalRequest.status == "pending",
        )
        .scalar()
    )

    withdrawn_decimal = _decimal(withdrawn)
    pending_decimal = _decimal(pending_withdrawal)

    available_balance = (
        net
        - withdrawn_decimal
        - pending_decimal
    )

    if available_balance < Decimal("0"):
        available_balance = Decimal("0")

    recent_orders = sorted(
        orders,
        key=lambda order: (
            order.completed_at
            or order.created_at
            or datetime.min
        ),
        reverse=True,
    )[:8]

    return {
        "total_sales": len(orders),
        "gross_revenue": gross,
        "platform_commission": commission,
        "net_earnings": net,
        "available_balance": available_balance,
        "pending_withdrawal": pending_decimal,
        "recent_orders": recent_orders,
    }


# ============================================================
# WITHDRAWAL HISTORY
# ============================================================

def _withdrawal_history(
    db: Session,
    profile_id: str,
):
    return (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id
        )
        .order_by(
            WithdrawalRequest.created_at.desc()
        )
        .all()
    )


# ============================================================
# CREATOR TRACKS
# ============================================================

def _creator_tracks(
    db: Session,
    profile_id: str,
):
    return (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile_id
        )
        .order_by(
            Track.created_at.desc()
        )
        .all()
    )


# ============================================================
# CREATOR ALBUMS
# ============================================================

def _creator_albums(
    db: Session,
    profile_id: str,
):
    return (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile_id
        )
        .order_by(
            Album.created_at.desc()
        )
        .all()
    )


# ============================================================
# DASHBOARD CONTEXT
# ============================================================

def _dashboard_context(
    request: Request,
    db: Session,
    user: User,
):
    """
    Build the complete creator dashboard context.

    This is deliberately centralized so /dashboard,
    /dashboard/withdraw and other dashboard pages use the
    same data structure.
    """

    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    tracks = _creator_tracks(
        db,
        profile.id,
    )

    albums = _creator_albums(
        db,
        profile.id,
    )

    withdrawal_requests = _withdrawal_history(
        db,
        profile.id,
    )

    track_count = len(tracks)
    album_count = len(albums)

    # --------------------------------------------------------
    # PUBLIC STORE
    # --------------------------------------------------------

    store_url = None

    profile_slug = getattr(
        profile,
        "slug",
        None,
    )

    if profile_slug:
        store_url = f"/store/{profile_slug}"

    # --------------------------------------------------------
    # YOUTUBE
    # --------------------------------------------------------

    youtube_url = None

    youtube_channel_id = getattr(
        settings,
        "YOUTUBE_CHANNEL_ID",
        None,
    )

    if youtube_channel_id:
        youtube_url = (
            "https://www.youtube.com/channel/"
            f"{youtube_channel_id}"
        )

    # --------------------------------------------------------
    # DISCORD
    # --------------------------------------------------------

    discord_url = getattr(
        settings,
        "DISCORD_INVITE_URL",
        None,
    )

    # --------------------------------------------------------
    # TRACK PAGINATION COMPATIBILITY
    # --------------------------------------------------------

    track_per_page = 12
    track_total = track_count

    track_total_pages = max(
        1,
        (track_total + track_per_page - 1)
        // track_per_page,
    )

    track_page = 1

    track_start = (
        1
        if track_total
        else 0
    )

    track_end = min(
        track_total,
        track_per_page,
    )

    return ctx(
        request,
        user,

        profile=profile,

        stats=stats,

        total_sales=stats["total_sales"],
        gross_revenue=stats["gross_revenue"],
        platform_commission=stats["platform_commission"],
        net_earnings=stats["net_earnings"],
        available_balance=stats["available_balance"],
        pending_withdrawal=stats["pending_withdrawal"],
        recent_orders=stats["recent_orders"],

        track_count=track_count,
        album_count=album_count,

        tracks=tracks,
        albums=albums,

        withdrawal_requests=withdrawal_requests,

        store_url=store_url,
        youtube_url=youtube_url,
        discord_url=discord_url,

        track_page=track_page,
        track_total_pages=track_total_pages,
        track_total=track_total,
        track_total_count=track_total,
        track_per_page=track_per_page,
        track_search="",
        track_start=track_start,
        track_end=track_end,
        q="",
    )


# ============================================================
# CREATOR DASHBOARD
# ============================================================

@router.get("/dashboard")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    context = _dashboard_context(
        request,
        db,
        user,
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context,
    )


# ============================================================
# WITHDRAWAL PAGE
# ============================================================

@router.get("/dashboard/withdraw")
def withdrawal_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    context = _dashboard_context(
        request,
        db,
        user,
    )

    return templates.TemplateResponse(
        request,
        "withdraw.html",
        context,
    )


# ============================================================
# UPLOAD TRACK PAGE
# ============================================================

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


# ============================================================
# UPLOAD TRACK
# ============================================================

@router.post("/dashboard/upload")
async def upload_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),

    titles: List[str] = Form(...),
    descriptions: List[str] = Form(...),
    genres: List[str] = Form(...),
    bpms: List[str] = Form(...),
    tags_list: List[str] = Form(...),
    prices: List[str] = Form(...),
    sales_models: List[str] = Form(...),

    audio_files: List[UploadFile] = File(...),
    cover_files: Optional[List[UploadFile]] = File(None),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    def error(message: str):
        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error=message,
            ),
            status_code=400,
        )

    if not titles or not audio_files:
        return error(
            "At least one track with an audio file is required."
        )

    if len(titles) != len(audio_files):
        return error(
            "Track details and audio files do not match."
        )

    created = []

    try:
        for i, raw_title in enumerate(titles):
            title = (raw_title or "").strip()

            if not title:
                return error(
                    "Every track needs a title."
                )

            # ------------------------------------------------
            # BPM
            # ------------------------------------------------

            bpm_raw = (
                bpms[i].strip()
                if i < len(bpms)
                else ""
            )

            bpm_value = None

            if bpm_raw:
                if not bpm_raw.isdigit():
                    return error(
                        f"BPM for '{title}' must be a whole number."
                    )

                bpm_value = int(bpm_raw)

                if bpm_value < 1 or bpm_value > 999:
                    return error(
                        f"BPM for '{title}' must be between 1 and 999."
                    )

            # ------------------------------------------------
            # PRICE
            # ------------------------------------------------

            price_raw = (
                prices[i].strip()
                if i < len(prices)
                else "0"
            )

            try:
                price_value = Decimal(price_raw)
            except (
                InvalidOperation,
                ValueError,
                TypeError,
            ):
                return error(
                    f"Price for '{title}' is invalid."
                )

            if price_value < 0:
                return error(
                    f"Price for '{title}' cannot be negative."
                )

            # ------------------------------------------------
            # SALES MODEL
            # ------------------------------------------------

            model_raw = (
                sales_models[i]
                if i < len(sales_models)
                else "non_exclusive"
            )

            if model_raw == "exclusive":
                sales_model = SalesModel.EXCLUSIVE
            else:
                sales_model = SalesModel.NON_EXCLUSIVE

            # ------------------------------------------------
            # AUDIO
            # ------------------------------------------------

            audio_path = await save_upload(
                audio_files[i],
                "audio",
                ALLOWED_AUDIO_EXT,
            )

            # ------------------------------------------------
            # COVER
            # ------------------------------------------------

            cover_path = None

            if (
                cover_files
                and i < len(cover_files)
                and cover_files[i] is not None
                and cover_files[i].filename
            ):
                cover_path = await save_upload(
                    cover_files[i],
                    "covers",
                    ALLOWED_IMAGE_EXT,
                )

            # ------------------------------------------------
            # SLUG
            # ------------------------------------------------

            slug = unique_slug(
                db,
                Track,
                title,
                "track",
            )

            # ------------------------------------------------
            # TRACK
            # ------------------------------------------------

            track = Track(
                creator_profile_id=profile.id,
                title=title,
                slug=slug,
                description=(
                    descriptions[i].strip()
                    if i < len(descriptions)
                    else None
                ) or None,
                genre=(
                    genres[i].strip()
                    if i < len(genres)
                    else None
                ) or None,
                bpm=bpm_value,
                tags=(
                    tags_list[i].strip()
                    if i < len(tags_list)
                    else None
                ) or None,
                audio_file_path=audio_path,
                cover_art_path=cover_path,
                price=price_value,
                sales_model=sales_model,
            )

            db.add(track)
            created.append(track)

        db.commit()

    except UploadValidationError as exc:
        db.rollback()

        return error(str(exc))

    except Exception:
        db.rollback()
        raise

    success_message = (
        f"{len(created)} track(s) uploaded successfully."
    )

    return RedirectResponse(
        url=(
            "/dashboard?success="
            + quote(success_message)
        ),
        status_code=303,
    )


# ============================================================
# CREATE ALBUM PAGE
# ============================================================

@router.get("/dashboard/albums/new")
def new_album_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    tracks = _creator_tracks(
        db,
        profile.id,
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


# ============================================================
# CREATE ALBUM
# ============================================================

@router.post("/dashboard/albums/new")
async def new_album_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),

    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    artwork: Optional[UploadFile] = File(None),
    track_ids: List[str] = Form([]),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    def error(message: str):
        tracks = _creator_tracks(
            db,
            profile.id,
        )

        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=tracks,
                error=message,
            ),
            status_code=400,
        )

    title = (title or "").strip()

    if not title:
        return error(
            "Album title is required."
        )

    if not track_ids:
        return error(
            "Select at least one track for this album."
        )

    # --------------------------------------------------------
    # Validate tracks BEFORE creating album
    # --------------------------------------------------------

    valid_tracks = (
        db.query(Track)
        .filter(
            Track.id.in_(track_ids),
            Track.creator_profile_id == profile.id,
        )
        .all()
    )

    if not valid_tracks:
        return error(
            "None of the selected tracks belong to your account."
        )

    track_map = {
        str(track.id): track
        for track in valid_tracks
    }

    ordered_track_ids = [
        track_id
        for track_id in track_ids
        if str(track_id) in track_map
    ]

    if not ordered_track_ids:
        return error(
            "The selected tracks are invalid."
        )

    # --------------------------------------------------------
    # Artwork
    # --------------------------------------------------------

    artwork_path = None

    try:
        if artwork and artwork.filename:
            artwork_path = await save_upload(
                artwork,
                "artwork",
                ALLOWED_IMAGE_EXT,
            )

        # ----------------------------------------------------
        # Album
        # ----------------------------------------------------

        slug = unique_slug(
            db,
            Album,
            title,
            "album",
        )

        album = Album(
            creator_profile_id=profile.id,
            title=title,
            slug=slug,
            description=description.strip() or None,
            genre=genre.strip() or None,
            artwork_path=artwork_path,
        )

        db.add(album)
        db.flush()

        # ----------------------------------------------------
        # Album tracks
        # ----------------------------------------------------

        for position, track_id in enumerate(
            ordered_track_ids
        ):
            db.add(
                AlbumTrack(
                    album_id=album.id,
                    track_id=track_map[str(track_id)].id,
                    position=position,
                )
            )

        db.commit()

    except UploadValidationError as exc:
        db.rollback()
        return error(str(exc))

    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url=(
            f"/album/{album.slug}"
            "?success=Album%20created."
        ),
        status_code=303,
    )


# ============================================================
# WITHDRAWAL REQUEST
# ============================================================

@router.post("/dashboard/withdraw")
def request_withdrawal(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),

    amount: str = Form(...),
    phone_number: str = Form(...),
):
    """
    Creates a pending withdrawal request.

    This route does not automatically send B2C money.
    """

    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    # --------------------------------------------------------
    # Amount
    # --------------------------------------------------------

    try:
        amount_value = Decimal(
            str(amount).strip()
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Invalid%20withdrawal%20amount."
            ),
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Withdrawal%20amount%20must%20be%20positive."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Available balance
    # --------------------------------------------------------

    if amount_value > stats["available_balance"]:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Withdrawal%20exceeds%20your%20available%20balance."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Phone
    # --------------------------------------------------------

    phone_number = (
        phone_number or ""
    ).strip()

    if not phone_number:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=M-Pesa%20phone%20number%20is%20required."
            ),
            status_code=303,
        )

    normalized_phone = (
        phone_number
        .replace(" ", "")
        .replace("-", "")
    )

    if normalized_phone.startswith("+"):
        normalized_phone = normalized_phone[1:]

    valid_phone = False

    if (
        len(normalized_phone) == 10
        and normalized_phone.isdigit()
        and normalized_phone.startswith(
            ("07", "01")
        )
    ):
        valid_phone = True

    elif (
        len(normalized_phone) == 12
        and normalized_phone.isdigit()
        and normalized_phone.startswith("254")
        and normalized_phone[3:5] in (
            "07",
            "01",
        )
    ):
        valid_phone = True

    if not valid_phone:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Enter%20a%20valid%20Kenyan%20M-Pesa%20phone%20number."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Duplicate pending withdrawal protection
    # --------------------------------------------------------

    existing_pending = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.creator_profile_id == profile.id,
            WithdrawalRequest.status == "pending",
            WithdrawalRequest.amount == amount_value,
            WithdrawalRequest.phone_number == phone_number,
        )
        .first()
    )

    if existing_pending:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "success=Your%20withdrawal%20request%20is%20already%20pending."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Create withdrawal
    # --------------------------------------------------------

    withdrawal = WithdrawalRequest(
        creator_profile_id=profile.id,
        amount=amount_value,
        phone_number=phone_number,
        status="pending",
    )

    try:
        db.add(withdrawal)
        db.commit()

    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url=(
            "/dashboard/withdraw?"
            "success=Withdrawal%20request%20submitted."
        ),
        status_code=303,
    )
