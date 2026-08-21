from datetime import datetime
from decimal import Decimal
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
from app.models.ledger import (
    CreatorLedgerEntry,
    WithdrawalRequest,
    WithdrawalStatus,
)
from app.models.music import (
    Album,
    AlbumTrack,
    SalesModel,
    Track,
)
from app.models.order import (
    Order,
    OrderStatus,
)
from app.models.user import User
from app.services.storage import (
    ALLOWED_AUDIO_EXT,
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    save_upload,
)
from app.utils.deps import require_creator
from app.utils.text import unique_slug


router = APIRouter(
    tags=["dashboard"]
)

templates = Jinja2Templates(
    directory="app/templates"
)


# ============================================================
# CONTEXT
# ============================================================

def ctx(
    request: Request,
    current_user: User,
    **extra,
):
    base = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    base.update(extra)

    return base


# ============================================================
# CREATOR FINANCIAL CALCULATIONS
# ============================================================

def _creator_stats(
    db: Session,
    profile_id: str,
) -> dict:

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
            o.gross_amount
            for o in orders
            if o.gross_amount is not None
        ),
        Decimal("0"),
    )

    commission = sum(
        (
            o.commission_amount
            for o in orders
            if o.commission_amount is not None
        ),
        Decimal("0"),
    )

    net = sum(
        (
            o.net_amount
            for o in orders
            if o.net_amount is not None
        ),
        Decimal("0"),
    )

    # Money already withdrawn by this creator.
    withdrawn = (
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
                    WithdrawalStatus.APPROVED,
                    WithdrawalStatus.PROCESSING,
                    WithdrawalStatus.PAID,
                ]
            ),
        )
        .scalar()
    )

    # Requests waiting for admin processing.
    pending_withdrawal = (
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
            == WithdrawalStatus.PENDING,
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
        available_balance = Decimal("0.00")

    recent_orders = sorted(
        orders,
        key=lambda o: (
            o.completed_at
            or o.created_at
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
# CREATOR DASHBOARD
# ============================================================

@router.get("/dashboard")
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

            # Withdrawal data available to the dashboard.
            available_balance=stats[
                "available_balance"
            ],
            pending_withdrawal=stats[
                "pending_withdrawal"
            ],
        ),
    )


# ============================================================
# CREATOR WITHDRAWAL PAGE
# ============================================================

@router.get("/dashboard/withdraw")
def creator_withdraw_page(
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

    withdrawals = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.creator_profile_id
            == profile.id
        )
        .order_by(
            WithdrawalRequest.created_at.desc()
        )
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "withdraw.html",
        ctx(
            request,
            user,
            profile=profile,
            stats=stats,
            available_balance=stats[
                "available_balance"
            ],
            pending_withdrawal=stats[
                "pending_withdrawal"
            ],
            withdrawals=withdrawals,
        ),
    )


# Compatibility URL.
@router.get("/dashboard/withdrawals")
def creator_withdrawals_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    return creator_withdraw_page(
        request=request,
        db=db,
        user=user,
    )


# ============================================================
# CREATOR WITHDRAWAL SUBMISSION
# ============================================================

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
                "error=Creator profile missing."
            ),
            status_code=303,
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
            (amount or "").strip()
        )
    except Exception:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Invalid withdrawal amount."
            ),
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Withdrawal amount must be greater than zero."
            ),
            status_code=303,
        )

    if amount_value > stats[
        "available_balance"
    ]:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=Withdrawal exceeds your available balance."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Phone
    # --------------------------------------------------------

    phone = (
        phone_number or ""
    ).strip()

    if not phone:
        return RedirectResponse(
            url=(
                "/dashboard/withdraw?"
                "error=M-Pesa phone number is required."
            ),
            status_code=303,
        )

    # --------------------------------------------------------
    # Create request
    # --------------------------------------------------------

    withdrawal = WithdrawalRequest(
        creator_profile_id=profile.id,
        amount=amount_value,
        phone_number=phone,
        status=WithdrawalStatus.PENDING,
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url=(
            "/dashboard/withdraw?"
            "success=Withdrawal request submitted successfully."
        ),
        status_code=303,
    )


# Compatibility POST URL.
@router.post("/dashboard/withdrawals")
def request_withdrawal_compat(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    amount: str = Form(...),
    phone_number: str = Form(...),
):
    return request_withdrawal(
        request=request,
        db=db,
        user=user,
        amount=amount,
        phone_number=phone_number,
    )


# ============================================================
# UPLOAD
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

            if not title.strip():
                return error(
                    "Every track needs a title."
                )

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

            price_raw = (
                prices[i].strip()
                if i < len(prices)
                else "0"
            )

            try:
                price_val = Decimal(
                    price_raw
                )

                if price_val < 0:
                    raise ValueError

            except Exception:
                return error(
                    f"Price for '{title}' is invalid."
                )

            model_raw = (
                sales_models[i]
                if i < len(sales_models)
                else "non_exclusive"
            )

            sales_model = (
                SalesModel.EXCLUSIVE
                if model_raw == "exclusive"
                else SalesModel.NON_EXCLUSIVE
            )

            audio_path = await save_upload(
                audio_files[i],
                "audio",
                ALLOWED_AUDIO_EXT,
            )

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

            slug = unique_slug(
                db,
                Track,
                title,
                "track",
            )

            track = Track(
                creator_profile_id=profile.id,
                title=title.strip(),
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

        return error(
            str(exc)
        )

    except Exception:
        db.rollback()

        return error(
            "Upload failed. Please try again."
        )

    return RedirectResponse(
        url=(
            "/dashboard?"
            f"success={len(created)} track(s) uploaded successfully."
        ),
        status_code=303,
    )


# ============================================================
# ALBUMS
# ============================================================

@router.get("/dashboard/albums/new")
def new_album_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    profile = user.profile

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


@router.post("/dashboard/albums/new")
async def new_album_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    title: str = Form(...),
    description: str = Form(""),
    genre: str = Form(""),
    artwork: UploadFile = File(None),
    track_ids: List[str] = Form([]),
):
    profile = user.profile

    def error(msg: str):
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
            return error(
                str(exc)
            )

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
        description=description.strip()
        or None,
        genre=genre.strip()
        or None,
        artwork_path=artwork_path,
    )

    db.add(album)
    db.flush()

    valid_tracks = (
        db.query(Track)
        .filter(
            Track.id.in_(track_ids),
            Track.creator_profile_id
            == profile.id,
        )
        .all()
    )

    for position, track in enumerate(
        valid_tracks
    ):
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
            "?success=Album created."
        ),
        status_code=303,
    )
