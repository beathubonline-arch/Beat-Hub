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

def ctx(
    request: Request,
    current_user: User,
    **extra,
):
    """
    Shared template context.

    Provides compatibility values used by the producer dashboard,
    withdrawal page, upload pages and older dashboard templates.
    """

    base = {
        "request": request,
        "current_user": current_user,
        "user": current_user,
        "current_year": datetime.utcnow().year,

        # Financial defaults
        "available_balance": Decimal("0"),
        "pending_withdrawal": Decimal("0"),
        "total_sales": 0,
        "gross_revenue": Decimal("0"),
        "platform_commission": Decimal("0"),
        "net_earnings": Decimal("0"),

        # Catalog defaults
        "track_count": 0,
        "album_count": 0,
        "tracks": [],
        "albums": [],

        # Orders / withdrawals
        "recent_orders": [],
        "withdrawal_requests": [],

        # Pagination compatibility
        "track_page": 1,
        "track_total_pages": 1,
        "track_total": 0,
        "track_total_count": 0,
        "track_per_page": 12,
        "track_search": "",
        "track_start": 0,
        "track_end": 0,
        "q": "",

        # Social/store
        "youtube_url": None,
        "youtube_channel_id": None,
        "discord_url": None,
        "store_url": None,

        # Messages
        "error": None,
        "success": None,
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

def _creator_stats(
    db: Session,
    profile_id: str,
) -> dict:
    """
    Calculate producer earnings from completed orders.

    Uses the current Order fields:
        gross_amount
        commission_amount
        net_amount
    """

    orders = (
        db.query(Order)
        .join(
            Track,
            Order.track_id == Track.id,
        )
        .filter(
            Track.creator_profile_id == profile_id,
            Order.status == OrderStatus.COMPLETED,
        )
        .all()
    )

    gross = sum(
        (
            _decimal(order.gross_amount)
            for order in orders
        ),
        Decimal("0"),
    )

    commission = sum(
        (
            _decimal(order.commission_amount)
            for order in orders
        ),
        Decimal("0"),
    )

    net = sum(
        (
            _decimal(order.net_amount)
            for order in orders
        ),
        Decimal("0"),
    )

    # --------------------------------------------------------
    # Withdrawn amounts
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Pending withdrawals
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Available balance
    # --------------------------------------------------------

    available_balance = (
        net
        - withdrawn_decimal
        - pending_decimal
    )

    if available_balance < Decimal("0"):
        available_balance = Decimal("0")

    # --------------------------------------------------------
    # Recent completed orders
    # --------------------------------------------------------

    recent_orders = sorted(
        orders,
        key=lambda order: (
            getattr(order, "completed_at", None)
            or getattr(order, "created_at", None)
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
    """
    Return creator withdrawal history.
    """

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
# DASHBOARD CONTEXT
# ============================================================

def _dashboard_context(
    request: Request,
    db: Session,
    user: User,
):
    """
    Build the complete producer dashboard context.
    """

    profile = getattr(user, "profile", None)

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    # --------------------------------------------------------
    # Catalog counts
    # --------------------------------------------------------

    track_count = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .count()
    )

    album_count = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .count()
    )

    # --------------------------------------------------------
    # Withdrawal history
    # --------------------------------------------------------

    withdrawal_requests = _withdrawal_history(
        db,
        profile.id,
    )

    # --------------------------------------------------------
    # Public producer store
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
    # YouTube
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
    # Discord
    # --------------------------------------------------------

    discord_url = getattr(
        settings,
        "DISCORD_INVITE_URL",
        None,
    )

    # --------------------------------------------------------
    # Producer tracks
    # --------------------------------------------------------

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .order_by(
            Track.created_at.desc()
        )
        .all()
    )

    albums = (
        db.query(Album)
        .filter(
            Album.creator_profile_id == profile.id
        )
        .order_by(
            Album.created_at.desc()
        )
        .all()
    )

    return ctx(
        request,
        user,

        # Profile
        profile=profile,

        # Stats object
        stats=stats,

        # Financial values
        total_sales=stats["total_sales"],
        gross_revenue=stats["gross_revenue"],
        platform_commission=stats["platform_commission"],
        net_earnings=stats["net_earnings"],
        available_balance=stats["available_balance"],
        pending_withdrawal=stats["pending_withdrawal"],

        # Orders
        recent_orders=stats["recent_orders"],

        # Catalog
        track_count=track_count,
        album_count=album_count,
        tracks=tracks,
        albums=albums,

        # Withdrawals
        withdrawal_requests=withdrawal_requests,

        # Social/store
        youtube_url=youtube_url,
        youtube_channel_id=youtube_channel_id,
        discord_url=discord_url,
        store_url=store_url,

        # Pagination compatibility
        track_page=1,
        track_total_pages=1,
        track_total=len(tracks),
        track_total_count=len(tracks),
        track_per_page=12,
        track_search="",
        track_start=1 if tracks else 0,
        track_end=len(tracks),
        q="",
    )


# ============================================================
# PRODUCER DASHBOARD
# ============================================================

@router.get("/dashboard")
def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    """
    Main producer dashboard.

    URL:
        /dashboard
    """

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
    """
    Producer withdrawal page.
    """

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
    """
    Producer upload page.
    """

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
    """
    Upload one or more tracks.
    """

    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile is None:
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

    # --------------------------------------------------------
    # Basic validation
    # --------------------------------------------------------

    if not titles:
        return error(
            "At least one track is required."
        )

    if not audio_files:
        return error(
            "At least one audio file is required."
        )

    if len(titles) != len(audio_files):
        return error(
            "Track details and audio files do not match."
        )

    created_count = 0

    try:
        for i, raw_title in enumerate(titles):

            title = (
                raw_title or ""
            ).strip()

            if not title:
                return error(
                    "Every track needs a title."
                )

            # ------------------------------------------------
            # BPM
            # ------------------------------------------------

            bpm_raw = ""

            if i < len(bpms):
                bpm_raw = (
                    bpms[i] or ""
                ).strip()

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
            # Price
            # ------------------------------------------------

            price_raw = "0"

            if i < len(prices):
                price_raw = (
                    prices[i] or "0"
                ).strip()

            try:
                price_value = Decimal(
                    price_raw
                )
            except (
                InvalidOperation,
                ValueError,
                TypeError,
            ):
                return error(
                    f"Price for '{title}' is invalid."
                )

            if price_value < Decimal("0"):
                return error(
                    f"Price for '{title}' cannot be negative."
                )

            # ------------------------------------------------
            # Sales model
            # ------------------------------------------------

            model_raw = "non_exclusive"

            if i < len(sales_models):
                model_raw = (
                    sales_models[i] or "non_exclusive"
                ).strip().lower()

            if model_raw == "exclusive":
                sales_model = SalesModel.EXCLUSIVE
            else:
                sales_model = SalesModel.NON_EXCLUSIVE

            # ------------------------------------------------
            # Audio
            # ------------------------------------------------

            audio_file = audio_files[i]

            if not audio_file.filename:
                return error(
                    f"Audio file is missing for '{title}'."
                )

            audio_path = await save_upload(
                audio_file,
                "audio",
                ALLOWED_AUDIO_EXT,
            )

            # ------------------------------------------------
            # Cover art
            # ------------------------------------------------

            cover_path = None

            if cover_files and i < len(cover_files):
                cover_file = cover_files[i]

                if (
                    cover_file is not None
                    and cover_file.filename
                ):
                    cover_path = await save_upload(
                        cover_file,
                        "covers",
                        ALLOWED_IMAGE_EXT,
                    )

            # ------------------------------------------------
            # Slug
            # ------------------------------------------------

            slug = unique_slug(
                db,
                Track,
                title,
                "track",
            )

            # ------------------------------------------------
            # Description
            # ------------------------------------------------

            description = None

            if i < len(descriptions):
                description = (
                    descriptions[i] or ""
                ).strip() or None

            # ------------------------------------------------
            # Genre
            # ------------------------------------------------

            genre = None

            if i < len(genres):
                genre = (
                    genres[i] or ""
                ).strip() or None

            # ------------------------------------------------
            # Tags
            # ------------------------------------------------

            tags = None

            if i < len(tags_list):
                tags = (
                    tags_list[i] or ""
                ).strip() or None

            # ------------------------------------------------
            # Create track
            # ------------------------------------------------

            track = Track(
                creator_profile_id=profile.id,
                title=title,
                slug=slug,
                description=description,
                genre=genre,
                bpm=bpm_value,
                tags=tags,
                audio_file_path=audio_path,
                cover_art_path=cover_path,
                price=price_value,
                sales_model=sales_model,
                is_sold=False,
                is_published=True,
            )

            db.add(track)

            created_count += 1

        db.commit()

    except UploadValidationError as exc:
        db.rollback()

        return error(
            str(exc)
        )

    except Exception:
        db.rollback()
        raise

    message = (
        f"{created_count} track(s) uploaded successfully."
    )

    return RedirectResponse(
        url=(
            "/dashboard?success="
            + quote(message)
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
    """
    Producer album creation page.
    """

    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
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
    """
    Create an album from the producer's tracks.
    """

    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    def get_creator_tracks():
        return (
            db.query(Track)
            .filter(
                Track.creator_profile_id == profile.id
            )
            .order_by(
                Track.created_at.desc()
            )
            .all()
        )

    def error(message: str):
        return templates.TemplateResponse(
            request,
            "upload_album.html",
            ctx(
                request,
                user,
                tracks=get_creator_tracks(),
                error=message,
            ),
            status_code=400,
        )

    # --------------------------------------------------------
    # Album title
    # --------------------------------------------------------

    title = (
        title or ""
    ).strip()

    if not title:
        return error(
            "Album title is required."
        )

    # --------------------------------------------------------
    # Tracks
    # --------------------------------------------------------

    if not track_ids:
        return error(
            "Select at least one track for this album."
        )

    # --------------------------------------------------------
    # Artwork
    # --------------------------------------------------------

    artwork_path = None

    if artwork and artwork.filename:
        try:
            artwork_path = await save_upload(
                artwork,
                "artwork",
                ALLOWED_IMAGE_EXT,
            )
        except UploadValidationError as exc:
            return error(
                str(exc)
            )

    # --------------------------------------------------------
    # Create album
    # --------------------------------------------------------

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
        description=(
            description or ""
        ).strip() or None,
        genre=(
            genre or ""
        ).strip() or None,
        artwork_path=artwork_path,
        is_published=True,
    )

    db.add(album)

    try:
        db.flush()

        # ----------------------------------------------------
        # Validate selected tracks belong to producer
        # ----------------------------------------------------

        valid_tracks = (
            db.query(Track)
            .filter(
                Track.id.in_(track_ids),
                Track.creator_profile_id == profile.id,
            )
            .all()
        )

        if not valid_tracks:
            db.rollback()

            return error(
                "None of the selected tracks belong to your account."
            )

        track_map = {
            str(track.id): track
            for track in valid_tracks
        }

        position = 0

        for track_id in track_ids:
            track = track_map.get(
                str(track_id)
            )

            if track is None:
                continue

            album_track = AlbumTrack(
                album_id=album.id,
                track_id=track.id,
                position=position,
            )

            db.add(album_track)

            position += 1

        if position == 0:
            db.rollback()

            return error(
                "No valid tracks were selected."
            )

        db.commit()

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
    Submit a producer withdrawal request.

    The request is created as PENDING.
    It does not automatically send B2C money.
    """

    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    # --------------------------------------------------------
    # Calculate current balance
    # --------------------------------------------------------

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
                "/dashboard/withdraw"
                "?error=Invalid%20withdrawal%20amount."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Positive amount
    # --------------------------------------------------------

    if amount_value <= Decimal("0"):
        return RedirectResponse(
            url=(
                "/dashboard/withdraw"
                "?error=Withdrawal%20amount%20must%20be%20positive."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Available balance
    # --------------------------------------------------------

    if amount_value > stats["available_balance"]:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw"
                "?error=Withdrawal%20exceeds%20your%20available%20balance."
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
                "/dashboard/withdraw"
                "?error=M-Pesa%20phone%20number%20is%20required."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Normalize phone for validation
    # --------------------------------------------------------

    normalized_phone = (
        phone_number
        .replace(" ", "")
        .replace("-", "")
    )

    if normalized_phone.startswith("+"):
        normalized_phone = normalized_phone[1:]

    valid_phone = False

    # 0712345678 / 0112345678
    if (
        len(normalized_phone) == 10
        and normalized_phone.isdigit()
        and normalized_phone.startswith(
            (
                "07",
                "01",
            )
        )
    ):
        valid_phone = True

    # 254712345678 / 254112345678
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
                "/dashboard/withdraw"
                "?error=Enter%20a%20valid%20Kenyan%20M-Pesa%20phone%20number."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Prevent duplicate pending withdrawal
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
                "/dashboard/withdraw"
                "?success=Your%20withdrawal%20request%20is%20already%20pending."
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

    db.add(withdrawal)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url=(
            "/dashboard/withdraw"
            "?success=Withdrawal%20request%20submitted."
        ),
        status_code=303,
    )
