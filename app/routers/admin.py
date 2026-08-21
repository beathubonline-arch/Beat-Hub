from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import (
    PlatformWithdrawal,
    WithdrawalRequest,
    WithdrawalStatus,
)
from app.models.music import Album, Track
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.user import User
from app.utils.deps import require_admin

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)

templates = Jinja2Templates(
    directory="app/templates"
)


def ctx(
    request: Request,
    current_user,
    **extra,
):
    base = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    base.update(extra)

    return base


# ----------------------------------------------------------------------
# PLATFORM WALLET
# ----------------------------------------------------------------------

def platform_financials(db: Session) -> dict:
    """
    Calculate BeatHub's platform money.

    Platform commission comes from completed orders.

    Creator net earnings are NOT included.
    """

    total_commission = (
        db.query(
            func.coalesce(
                func.sum(Order.commission_amount),
                0,
            )
        )
        .filter(
            Order.status == OrderStatus.COMPLETED
        )
        .scalar()
    )

    total_commission = Decimal(
        str(total_commission or 0)
    )

    paid_withdrawals = (
        db.query(
            func.coalesce(
                func.sum(PlatformWithdrawal.amount),
                0,
            )
        )
        .filter(
            PlatformWithdrawal.status
            == WithdrawalStatus.PAID
        )
        .scalar()
    )

    processing_withdrawals = (
        db.query(
            func.coalesce(
                func.sum(PlatformWithdrawal.amount),
                0,
            )
        )
        .filter(
            PlatformWithdrawal.status.in_(
                [
                    WithdrawalStatus.PENDING,
                    WithdrawalStatus.APPROVED,
                    WithdrawalStatus.PROCESSING,
                ]
            )
        )
        .scalar()
    )

    paid_withdrawals = Decimal(
        str(paid_withdrawals or 0)
    )

    processing_withdrawals = Decimal(
        str(processing_withdrawals or 0)
    )

    available = (
        total_commission
        - paid_withdrawals
        - processing_withdrawals
    )

    if available < 0:
        available = Decimal("0")

    return {
        "total_commission": total_commission,
        "withdrawn": paid_withdrawals,
        "reserved": processing_withdrawals,
        "available": available,
    }


# ----------------------------------------------------------------------
# ADMIN HOME
# ----------------------------------------------------------------------

@router.get("")
def admin_home(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    total_sales_volume = (
        db.query(
            func.coalesce(
                func.sum(Order.gross_amount),
                0,
            )
        )
        .filter(
            Order.status
            == OrderStatus.COMPLETED
        )
        .scalar()
    )

    total_commission = (
        db.query(
            func.coalesce(
                func.sum(Order.commission_amount),
                0,
            )
        )
        .filter(
            Order.status
            == OrderStatus.COMPLETED
        )
        .scalar()
    )

    total_creator_earnings = (
        db.query(
            func.coalesce(
                func.sum(Order.net_amount),
                0,
            )
        )
        .filter(
            Order.status
            == OrderStatus.COMPLETED
        )
        .scalar()
    )

    successful = (
        db.query(Order)
        .filter(
            Order.status
            == OrderStatus.COMPLETED
        )
        .count()
    )

    pending = (
        db.query(Order)
        .filter(
            Order.status
            == OrderStatus.PENDING
        )
        .count()
    )

    failed = (
        db.query(Order)
        .filter(
            Order.status.in_(
                [
                    OrderStatus.FAILED,
                    OrderStatus.REJECTED,
                ]
            )
        )
        .count()
    )

    recent_orders = (
        db.query(Order)
        .order_by(
            Order.created_at.desc()
        )
        .limit(10)
        .all()
    )

    recent_users = (
        db.query(User)
        .order_by(
            User.created_at.desc()
        )
        .limit(10)
        .all()
    )

    failed_payments = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.status.in_(
                [
                    PaymentStatus.FAILED,
                    PaymentStatus.CANCELLED,
                ]
            )
        )
        .order_by(
            PaymentTransaction.updated_at.desc()
        )
        .limit(10)
        .all()
    )

    pending_withdrawals = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.status
            == WithdrawalStatus.PENDING
        )
        .count()
    )

    platform = platform_financials(db)

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        ctx(
            request,
            admin,
            total_sales_volume=total_sales_volume,
            total_commission=total_commission,
            total_creator_earnings=total_creator_earnings,
            successful=successful,
            pending=pending,
            failed=failed,
            recent_orders=recent_orders,
            recent_users=recent_users,
            failed_payments=failed_payments,
            pending_withdrawals=pending_withdrawals,
            platform=platform,
        ),
    )


# ----------------------------------------------------------------------
# PLATFORM EARNINGS
# ----------------------------------------------------------------------

@router.get("/earnings")
def admin_earnings(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    platform = platform_financials(db)

    withdrawals = (
        db.query(PlatformWithdrawal)
        .order_by(
            PlatformWithdrawal.created_at.desc()
        )
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/earnings.html",
        ctx(
            request,
            admin,
            platform=platform,
            withdrawals=withdrawals,
        ),
    )


# ----------------------------------------------------------------------
# ADMIN PLATFORM WITHDRAWAL
# ----------------------------------------------------------------------

@router.post("/earnings/withdraw")
async def admin_platform_withdraw(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    amount: str = Form(...),
    phone_number: str = Form(...),
):
    """
    Withdraw BeatHub's platform commission to M-Pesa.

    This is NOT a creator withdrawal.
    """

    try:
        amount_value = Decimal(
            amount.strip()
        ).quantize(Decimal("0.01"))

    except (InvalidOperation, ValueError):
        return RedirectResponse(
            url=(
                "/admin/earnings"
                "?error=Invalid withdrawal amount."
            ),
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url=(
                "/admin/earnings"
                "?error=Withdrawal amount must be greater than zero."
            ),
            status_code=303,
        )

    platform = platform_financials(db)

    if amount_value > platform["available"]:
        return RedirectResponse(
            url=(
                "/admin/earnings"
                "?error=Withdrawal exceeds your available BeatHub balance."
            ),
            status_code=303,
        )

    # Validate phone before creating a request.
    from app.mpesa import normalize_phone

    try:
        normalized_phone = normalize_phone(
            phone_number
        )
    except ValueError:
        return RedirectResponse(
            url=(
                "/admin/earnings"
                "?error=Invalid M-Pesa phone number."
            ),
            status_code=303,
        )

    withdrawal = PlatformWithdrawal(
        amount=amount_value,
        phone_number=normalized_phone,
        status=WithdrawalStatus.PROCESSING,
        admin_note="BeatHub platform commission withdrawal.",
    )

    db.add(withdrawal)

    try:
        db.flush()

        from app.mpesa import b2c_payment

        result = await b2c_payment(
            phone_number=normalized_phone,
            amount=float(amount_value),
            remarks="BeatHub platform commission",
            occasion="BeatHub",
            command_id="BusinessPayment",
        )

        withdrawal.conversation_id = (
            result.get("ConversationID")
        )

        withdrawal.originator_conversation_id = (
            result.get(
                "OriginatorConversationID"
            )
        )

        # Safaricom accepting the request does NOT necessarily
        # mean the recipient has received the money yet.
        withdrawal.status = (
            WithdrawalStatus.PROCESSING
        )

        db.commit()

    except Exception as exc:
        db.rollback()

        return RedirectResponse(
            url=(
                "/admin/earnings"
                "?error="
                "M-Pesa payout could not be initiated."
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=(
            "/admin/earnings"
            "?success="
            "M-Pesa payout request submitted successfully."
        ),
        status_code=303,
    )


# ----------------------------------------------------------------------
# MARK PLATFORM WITHDRAWAL PAID
# ----------------------------------------------------------------------

@router.post(
    "/earnings/{withdrawal_id}/mark-paid"
)
def admin_mark_platform_paid(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    withdrawal = db.get(
        PlatformWithdrawal,
        withdrawal_id,
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Platform withdrawal not found.",
        )

    if withdrawal.status not in {
        WithdrawalStatus.PROCESSING,
        WithdrawalStatus.APPROVED,
    }:
        raise HTTPException(
            status_code=400,
            detail="This withdrawal cannot be marked as paid.",
        )

    withdrawal.status = (
        WithdrawalStatus.PAID
    )

    withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/earnings"
            "?success=Platform withdrawal marked as paid."
        ),
        status_code=303,
    )


# ----------------------------------------------------------------------
# MARK PLATFORM WITHDRAWAL REJECTED
# ----------------------------------------------------------------------

@router.post(
    "/earnings/{withdrawal_id}/reject"
)
def admin_reject_platform_withdrawal(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    withdrawal = db.get(
        PlatformWithdrawal,
        withdrawal_id,
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Platform withdrawal not found.",
        )

    if withdrawal.status == WithdrawalStatus.PAID:
        raise HTTPException(
            status_code=400,
            detail="A paid withdrawal cannot be rejected.",
        )

    withdrawal.status = (
        WithdrawalStatus.REJECTED
    )

    withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/earnings"
            "?success=Platform withdrawal rejected."
        ),
        status_code=303,
    )


# ----------------------------------------------------------------------
# USERS
# ----------------------------------------------------------------------

@router.get("/users")
def admin_users(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = db.query(User)

    if q:
        query = query.filter(
            User.email.ilike(
                f"%{q}%"
            )
        )

    users = (
        query
        .order_by(
            User.created_at.desc()
        )
        .limit(200)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/users.html",
        ctx(
            request,
            admin,
            users=users,
            q=q,
        ),
    )


@router.post(
    "/users/{user_id}/toggle-active"
)
def admin_toggle_user(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    target = db.get(
        User,
        user_id,
    )

    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # Never allow an admin to deactivate themselves.
    if target.id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot deactivate your own admin account.",
        )

    target.is_active = not target.is_active

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/users"
            "?success=User status updated."
        ),
        status_code=303,
    )


# ----------------------------------------------------------------------
# CONTENT
# ----------------------------------------------------------------------

@router.get("/content")
def admin_content(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    tracks_query = db.query(Track)
    albums_query = db.query(Album)

    if q:
        tracks_query = tracks_query.filter(
            Track.title.ilike(
                f"%{q}%"
            )
        )

        albums_query = albums_query.filter(
            Album.title.ilike(
                f"%{q}%"
            )
        )

    tracks = (
        tracks_query
        .order_by(
            Track.created_at.desc()
        )
        .limit(200)
        .all()
    )

    albums = (
        albums_query
        .order_by(
            Album.created_at.desc()
        )
        .limit(200)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/content.html",
        ctx(
            request,
            admin,
            tracks=tracks,
            albums=albums,
            q=q,
        ),
    )


@router.post(
    "/content/track/{track_id}/toggle-published"
)
def admin_toggle_track(
    track_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    track = db.get(
        Track,
        track_id,
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found",
        )

    track.is_published = (
        not track.is_published
    )

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/content"
            "?success=Track updated."
        ),
        status_code=303,
    )


# ----------------------------------------------------------------------
# SALES
# ----------------------------------------------------------------------

@router.get("/sales")
def admin_sales(
    request: Request,
    status: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    query = db.query(Order)

    if status:
        query = query.filter(
            Order.status == status
        )

    orders = (
        query
        .order_by(
            Order.created_at.desc()
        )
        .limit(300)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/sales.html",
        ctx(
            request,
            admin,
            orders=orders,
            status=status,
        ),
    )


# ----------------------------------------------------------------------
# CREATOR WITHDRAWALS
# ----------------------------------------------------------------------

@router.get("/withdrawals")
def admin_withdrawals(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    withdrawals = (
        db.query(WithdrawalRequest)
        .order_by(
            WithdrawalRequest.created_at.desc()
        )
        .limit(200)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/withdrawals.html",
        ctx(
            request,
            admin,
            withdrawals=withdrawals,
        ),
    )


@router.post(
    "/withdrawals/{withdrawal_id}/update"
)
def admin_update_withdrawal(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    action: str = Form(...),
    payout_reference: str = Form(""),
):
    wr = db.get(
        WithdrawalRequest,
        withdrawal_id,
    )

    if not wr:
        raise HTTPException(
            status_code=404,
            detail="Withdrawal request not found",
        )

    valid_transitions = {
        "approve":
            WithdrawalStatus.APPROVED,

        "process":
            WithdrawalStatus.PROCESSING,

        "mark_paid":
            WithdrawalStatus.PAID,

        "reject":
            WithdrawalStatus.REJECTED,
    }

    if action not in valid_transitions:
        raise HTTPException(
            status_code=400,
            detail="Invalid action",
        )

    wr.status = valid_transitions[
        action
    ]

    if payout_reference:
        wr.payout_reference = (
            payout_reference.strip()
        )

    if wr.status in (
        WithdrawalStatus.PAID,
        WithdrawalStatus.REJECTED,
    ):
        wr.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdrawals"
            "?success=Withdrawal updated."
        ),
        status_code=303,
    )
