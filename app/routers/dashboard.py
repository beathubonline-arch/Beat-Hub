from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
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
    Common template context used by dashboard-related pages.

    The context intentionally contains compatibility variables used
    by both older and newer BeatHub templates.
    """

    base = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,

        # Financial defaults
        "available_balance": Decimal("0"),
        "pending_withdrawal": Decimal("0"),
        "total_sales": 0,
        "gross_revenue": Decimal("0"),
        "platform_commission": Decimal("0"),
        "net_earnings": Decimal("0"),

        # Withdrawal
        "withdrawal_requests": [],

        # Catalog
        "track_count": 0,
        "album_count": 0,
        "tracks": [],
        "albums": [],

        # Orders
        "recent_orders": [],

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

        # Social / store
        "youtube_url": None,
        "discord_url": None,
        "store_url": None,
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
    Calculate creator earnings from completed track orders.

    BeatHub Order fields:
        gross_amount
        commission_amount
        net_amount

    Only completed orders are counted.
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
    # Withdrawals already approved / processing / paid
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
    Build one consistent context for dashboard pages.

    This function deliberately provides all pagination variables
    expected by the dashboard template.
    """

    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    # --------------------------------------------------------
    # Creator statistics
    # --------------------------------------------------------

    stats = _creator_stats(
        db,
        profile.id,
    )

    # --------------------------------------------------------
    # Search / pagination
    # --------------------------------------------------------

    track_per_page = 12

    raw_page = request.query_params.get(
        "track_page",
        request.query_params.get("page", "1"),
    )

    try:
        track_page = int(raw_page)
    except (ValueError, TypeError):
        track_page = 1

    if track_page < 1:
        track_page = 1

    track_search = (
        request.query_params.get(
            "track_search",
            request.query_params.get("q", ""),
        )
        or ""
    ).strip()

    # --------------------------------------------------------
    # All creator tracks count
    # --------------------------------------------------------

    track_query = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
    )

    if track_search:
        search_pattern = f"%{track_search}%"

        track_query = track_query.filter(
            Track.title.ilike(search_pattern)
            | Track.genre.ilike(search_pattern)
            | Track.tags.ilike(search_pattern)
        )

    track_total_count = track_query.count()

    # --------------------------------------------------------
    # Total pages
    # --------------------------------------------------------

    if track_total_count == 0:
        track_total_pages = 1
    else:
        track_total_pages = (
            (track_total_count + track_per_page - 1)
            // track_per_page
        )

    if track_page > track_total_pages:
        track_page = track_total_pages

    # --------------------------------------------------------
    # Pagination offset
    # --------------------------------------------------------

    offset = (
        (track_page - 1)
        * track_per_page
    )

    tracks = (
        track_query
        .order_by(
            Track.created_at.desc()
        )
        .offset(offset)
        .limit(track_per_page)
        .all()
    )

    # --------------------------------------------------------
    # Track display range
    # --------------------------------------------------------

    if track_total_count == 0:
        track_start = 0
        track_end = 0
    else:
        track_start = offset + 1
        track_end = min(
            offset + len(tracks),
            track_total_count,
        )

    # --------------------------------------------------------
    # Album count
    # --------------------------------------------------------

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
    # Public creator store
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
    # Full dashboard context
    # --------------------------------------------------------

    return ctx(
        request,
        user,

        # Profile
        profile=profile,

        # Statistics
        stats=stats,
        total_sales=stats["total_sales"],
        gross_revenue=stats["gross_revenue"],
        platform_commission=stats["platform_commission"],
        net_earnings=stats["net_earnings"],
        available_balance=stats["available_balance"],
        pending_withdrawal=stats["pending_withdrawal"],
        recent_orders=stats["recent_orders"],

        # Catalog
        track_count=(
            db.query(Track)
            .filter(
                Track.creator_profile_id == profile.id
            )
            .count()
        ),
        album_count=album_count,
        tracks=tracks,
        albums=[],

        # Withdrawal
        withdrawal_requests=withdrawal_requests,

        # Social / store
        youtube_url=youtube_url,
        discord_url=discord_url,
        store_url=store_url,

        # Pagination compatibility
        track_page=track_page,
        track_total_pages=track_total_pages,
        track_total=track_total_count,
        track_total_count=track_total_count,
        track_per_page=track_per_page,
        track_search=track_search,
        track_start=track_start,
        track_end=track_end,
        q=track_search,
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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _dashboard_context(
            request,
            db,
            user,
        ),
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
    if not user.profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

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
    cover_files: List[Optional[UploadFile]] = File(None),
):
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    def error(msg: str):
        return templates.TemplateResponse(
            request,
            "upload_track.html",
            ctx(
                request,
                user,
                error=msg,
            ),
            status_code=400,
        )

    # --------------------------------------------------------
    # Basic validation
    # --------------------------------------------------------

    if not titles or not audio_files:
        return error(
            "At least one track with an audio file is required."
        )

    if len(titles) != len(audio_files):
        return error(
            "Track details and audio files don't match up."
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

            bpm_val = None

            if bpm_raw:
                if not bpm_raw.isdigit():
                    return error(
                        f"BPM for '{title}' must be a whole number."
                    )

                bpm_val = int(bpm_raw)

                if bpm_val < 1 or bpm_val > 999:
                    return error(
                        f"BPM for '{title}' must be between 1 and 999."
                    )

            # ------------------------------------------------
            # Price
            # ------------------------------------------------

            price_raw = (
                prices[i].strip()
                if i < len(prices)
                else "0"
            )

            try:
                price_val = Decimal(price_raw)

                if price_val < 0:
                    raise ValueError

                price_val = price_val.quantize(
                    Decimal("0.01")
                )

            except (
                InvalidOperation,
                ValueError,
                TypeError,
            ):
                return error(
                    f"Price for '{title}' is invalid."
                )

            # ------------------------------------------------
            # Sales model
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
            # Audio
            # ------------------------------------------------

            if not audio_files[i].filename:
                return error(
                    f"Audio file for '{title}' is required."
                )

            audio_path = await save_upload(
                audio_files[i],
                "audio",
                ALLOWED_AUDIO_EXT,
            )

            # ------------------------------------------------
            # Cover
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
            # Slug
            # ------------------------------------------------

            slug = unique_slug(
                db,
                Track,
                title,
                "track",
            )

            # ------------------------------------------------
            # Track
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
                bpm=bpm_val,
                tags=(
                    tags_list[i].strip()
                    if i < len(tags_list)
                    else None
                ) or None,
                audio_file_path=audio_path,
                cover_art_path=cover_path,
                price=price_val,
                sales_model=sales_model,
                is_sold=False,
                is_published=True,
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

    message = (
        f"{len(created)} track(s) uploaded successfully."
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
    profile = user.profile

    if not profile:
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
    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    def error(msg: str):
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
                error=msg,
            ),
            status_code=400,
        )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    title = (title or "").strip()

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

    # Remove duplicate IDs while preserving order.
    cleaned_track_ids = []

    for track_id in track_ids:
        track_id = str(track_id).strip()

        if track_id and track_id not in cleaned_track_ids:
            cleaned_track_ids.append(track_id)

    if not cleaned_track_ids:
        return error(
            "Select at least one valid track."
        )

    # --------------------------------------------------------
    # Validate tracks belong to creator BEFORE creating album
    # --------------------------------------------------------

    valid_tracks = (
        db.query(Track)
        .filter(
            Track.id.in_(cleaned_track_ids),
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

    ordered_tracks = []

    for track_id in cleaned_track_ids:
        track = track_map.get(
            str(track_id)
        )

        if track:
            ordered_tracks.append(track)

    if not ordered_tracks:
        return error(
            "The selected tracks could not be found."
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
            return error(str(exc))

    # --------------------------------------------------------
    # Album
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
            description.strip()
            if description
            else None
        ) or None,
        genre=(
            genre.strip()
            if genre
            else None
        ) or None,
        artwork_path=artwork_path,
        is_published=True,
    )

    db.add(album)
    db.flush()

    # --------------------------------------------------------
    # Album tracks
    # --------------------------------------------------------

    for position, track in enumerate(ordered_tracks):
        db.add(
            AlbumTrack(
                album_id=album.id,
                track_id=track.id,
                position=position,
            )
        )

    db.commit()

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
    Submit a creator withdrawal request.

    This creates a PENDING withdrawal.

    It does NOT automatically send B2C money.
    """

    profile = user.profile

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    # --------------------------------------------------------
    # Recalculate balance at request time
    # --------------------------------------------------------

    stats = _creator_stats(
        db,
        profile.id,
    )

    # --------------------------------------------------------
    # Amount
    # --------------------------------------------------------

    try:
        amount_val = Decimal(
            str(amount).strip()
        ).quantize(
            Decimal("0.01")
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

    # --------------------------------------------------------
    # Positive amount
    # --------------------------------------------------------

    if amount_val <= 0:
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

    if amount_val > stats["available_balance"]:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Withdrawal%20exceeds%20your%20available%20balance."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Phone number
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

    # --------------------------------------------------------
    # Normalize only for validation.
    # Store the original entered value.
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
            ("07", "01")
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
                "/dashboard/withdraw?"
                "error=Enter%20a%20valid%20Kenyan%20M-Pesa%20phone%20number."
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
            WithdrawalRequest.amount == amount_val,
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
    # Create withdrawal request
    # --------------------------------------------------------

    withdrawal = WithdrawalRequest(
        creator_profile_id=profile.id,
        amount=amount_val,
        phone_number=phone_number,
        status="pending",
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url=(
            "/dashboard/withdraw?"
            "success=Withdrawal%20request%20submitted."
        ),
        status_code=303,
    )
