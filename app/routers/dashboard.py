from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional

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


# =========================================================
# TEMPLATE CONTEXT
# =========================================================

def ctx(request: Request, current_user, **extra):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    data.update(extra)

    return data


# =========================================================
# CREATOR STATS
# =========================================================

def _creator_stats(db: Session, profile_id: str) -> dict:
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
        (
            Decimal(str(o.gross_amount or 0))
            for o in orders
        ),
        Decimal("0"),
    )

    commission = sum(
        (
            Decimal(str(o.commission_amount or 0))
            for o in orders
        ),
        Decimal("0"),
    )

    net = sum(
        (
            Decimal(str(o.net_amount or 0))
            for o in orders
        ),
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

    withdrawn_decimal = Decimal(
        str(withdrawn or 0)
    )

    pending_decimal = Decimal(
        str(pending_withdrawal or 0)
    )

    available_balance = (
        net
        - withdrawn_decimal
        - pending_decimal
    )

    if available_balance < 0:
        available_balance = Decimal("0")

    recent_orders = sorted(
        orders,
        key=lambda o: (
            o.completed_at
            or o.created_at
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
        "withdrawn": withdrawn_decimal,
        "recent_orders": recent_orders,
    }


# =========================================================
# DASHBOARD
# =========================================================

@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_home(
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

    stats = _creator_stats(
        db,
        profile.id,
    )

    # -----------------------------------------------------
    # TRACK COUNTS
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # RECENT TRACKS
    # -----------------------------------------------------

    tracks = (
        db.query(Track)
        .filter(
            Track.creator_profile_id == profile.id
        )
        .order_by(
            Track.created_at.desc()
        )
        .limit(12)
        .all()
    )

    # -----------------------------------------------------
    # STORE URL
    # -----------------------------------------------------

    store_url = None

    try:
        if getattr(profile, "slug", None):
            store_url = (
                f"/producer/{profile.slug}"
            )
        elif getattr(profile, "username", None):
            store_url = (
                f"/producer/{profile.username}"
            )
    except Exception:
        store_url = None

    # -----------------------------------------------------
    # SOCIAL LINKS
    # -----------------------------------------------------

    youtube_channel_id = getattr(
        settings,
        "YOUTUBE_CHANNEL_ID",
        "",
    )

    discord_invite_url = getattr(
        settings,
        "DISCORD_INVITE_URL",
        "",
    )

    youtube_url = None

    if youtube_channel_id:
        youtube_url = (
            "https://www.youtube.com/channel/"
            f"{youtube_channel_id}"
        )

    # -----------------------------------------------------
    # DIRECT TEMPLATE VARIABLES
    #
    # IMPORTANT:
    # dashboard.html expects these variables directly,
    # not only inside stats.
    # -----------------------------------------------------

    available_balance = stats[
        "available_balance"
    ]

    total_sales = stats[
        "total_sales"
    ]

    gross_revenue = stats[
        "gross_revenue"
    ]

    platform_commission = stats[
        "platform_commission"
    ]

    net_earnings = stats[
        "net_earnings"
    ]

    pending_withdrawal = stats[
        "pending_withdrawal"
    ]

    withdrawn = stats[
        "withdrawn"
    ]

    recent_orders = stats[
        "recent_orders"
    ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        ctx(
            request,
            user,

            # Main objects
            profile=profile,
            stats=stats,

            # Counts
            track_count=track_count,
            album_count=album_count,

            # Tracks
            tracks=tracks,

            # Financial values
            available_balance=available_balance,
            total_sales=total_sales,
            gross_revenue=gross_revenue,
            platform_commission=platform_commission,
            net_earnings=net_earnings,
            pending_withdrawal=pending_withdrawal,
            withdrawn=withdrawn,

            # Orders
            recent_orders=recent_orders,

            # Socials
            youtube_url=youtube_url,
            discord_url=discord_invite_url,

            # Public store
            store_url=store_url,

            # Compatibility aliases
            earnings=net_earnings,
            balance=available_balance,
        ),
    )


# =========================================================
# UPLOAD PAGE
# =========================================================

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


# =========================================================
# UPLOAD TRACKS
# =========================================================

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
        for i, title in enumerate(titles):

            title = title.strip()

            if not title:
                return error(
                    "Every track needs a title."
                )

            # -------------------------------------------------
            # BPM
            # -------------------------------------------------

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

            # -------------------------------------------------
            # PRICE
            # -------------------------------------------------

            price_raw = (
                prices[i].strip()
                if i < len(prices)
                else "0"
            )

            try:
                price_val = Decimal(price_raw)

                if price_val < 0:
                    raise ValueError

            except (InvalidOperation, ValueError):
                return error(
                    f"Price for '{title}' is invalid."
                )

            # -------------------------------------------------
            # SALES MODEL
            # -------------------------------------------------

            model_raw = (
                sales_models[i]
                if i < len(sales_models)
                else "non_exclusive"
            )

            if model_raw == "exclusive":
                sales_model = SalesModel.EXCLUSIVE
            else:
                sales_model = SalesModel.NON_EXCLUSIVE

            # -------------------------------------------------
            # AUDIO
            # -------------------------------------------------

            audio_path = await save_upload(
                audio_files[i],
                "audio",
                ALLOWED_AUDIO_EXT,
            )

            # -------------------------------------------------
            # COVER
            # -------------------------------------------------

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

            # -------------------------------------------------
            # SLUG
            # -------------------------------------------------

            slug = unique_slug(
                db,
                Track,
                title,
                "track",
            )

            # -------------------------------------------------
            # TRACK
            # -------------------------------------------------

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

    return RedirectResponse(
        url=(
            "/dashboard?success="
            f"{len(created)}%20track(s)%20uploaded%20successfully."
        ),
        status_code=303,
    )


# =========================================================
# NEW ALBUM PAGE
# =========================================================

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


# =========================================================
# CREATE ALBUM
# =========================================================

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

    if not title.strip():
        return error(
            "Album title is required."
        )

    if not track_ids:
        return error(
            "Select at least one track for this album."
        )

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

    slug = unique_slug(
        db,
        Album,
        title,
        "album",
    )

    album = Album(
        creator_profile_id=profile.id,
        title=title.strip(),
        slug=slug,
        description=description.strip() or None,
        genre=genre.strip() or None,
        artwork_path=artwork_path,
    )

    db.add(album)
    db.flush()

    valid_tracks = (
        db.query(Track)
        .filter(
            Track.id.in_(track_ids),
            Track.creator_profile_id == profile.id,
        )
        .order_by(
            Track.created_at.asc()
        )
        .all()
    )

    if not valid_tracks:
        db.rollback()
        return error(
            "The selected tracks could not be found."
        )

    for position, track in enumerate(valid_tracks):
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


# =========================================================
# CREATOR WITHDRAWAL
# =========================================================

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
        raise HTTPException(
            status_code=400,
            detail="Creator profile missing.",
        )

    stats = _creator_stats(
        db,
        profile.id,
    )

    try:
        amount_val = Decimal(
            amount.strip()
        )
    except (InvalidOperation, ValueError):
        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Invalid%20withdrawal%20amount."
            ),
            status_code=303,
        )

    if amount_val <= 0:
        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Withdrawal%20amount%20must%20be%20positive."
            ),
            status_code=303,
        )

    if amount_val > stats["available_balance"]:
        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=Withdrawal%20exceeds%20your%20available%20balance."
            ),
            status_code=303,
        )

    phone_number = phone_number.strip()

    if not phone_number:
        return RedirectResponse(
            url=(
                "/dashboard?"
                "error=M-Pesa%20phone%20number%20is%20required."
            ),
            status_code=303,
        )

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
            "/dashboard?"
            "success=Withdrawal%20request%20submitted."
        ),
        status_code=303,
    )
