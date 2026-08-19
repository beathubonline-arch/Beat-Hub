from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest, WithdrawalStatus
from app.models.music import Album, Track
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
        db.query(
            func.coalesce(func.sum(WithdrawalRequest.amount), 0)
        )
        .filter(
            WithdrawalRequest.creator_profile_id == profile_id,
            WithdrawalRequest.status == WithdrawalStatus.PAID,
        )
        .scalar()
    )

    pending_withdrawals = (
        db.query(
            func.coalesce(func.sum(WithdrawalRequest.amount), 0)
        )
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
# UPLOAD
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
