from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import (
    AdminWithdrawal,
    AdminWithdrawalStatus,
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


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def ctx(
    request: Request,
    current_user,
    **extra,
):
    context = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    context.update(extra)

    return context


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _platform_commission(
    db: Session,
) -> Decimal:
    """
    Total BeatHub commission earned from completed orders.
    """

    value = (
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

    return _money(value)


def _admin_withdrawn_amount(
    db: Session,
) -> Decimal:
    """
    Amount already committed to admin withdrawals.

    Approved, processing and paid amounts reduce the available
    platform balance.

    Pending is also reserved so the administrator cannot submit
    several withdrawals that together exceed the available balance.
    """

    statuses = [
        AdminWithdrawalStatus.PENDING.value,
        AdminWithdrawalStatus.APPROVED.value,
        AdminWithdrawalStatus.PROCESSING.value,
        AdminWithdrawalStatus.PAID.value,
    ]

    value = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status.in_(statuses)
        )
        .scalar()
    )

    return _money(value)


def _platform_available_balance(
    db: Session,
) -> Decimal:
    commission = _platform_commission(db)
    committed = _admin_withdrawn_amount(db)

    available = commission - committed

    if available < 0:
        return Decimal("0")

    return available


# ----------------------------------------------------------------------
# ADMIN OVERVIEW
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
            Order.status == OrderStatus.COMPLETED
        )
        .scalar()
    )

    total_commission = _platform_commission(db)

    total_creator_earnings = (
        db.query(
            func.coalesce(
                func.sum(Order.net_amount),
                0,
            )
        )
        .filter(
            Order.status == OrderStatus.COMPLETED
        )
        .scalar()
    )

    successful = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.COMPLETED
        )
        .count()
    )

    pending = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING
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
        .order_by(Order.created_at.desc())
        .limit(10)
        .all()
    )

    recent_users = (
        db.query(User)
        .order_by(User.created_at.desc())
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

    pending_creator_withdrawals = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.status
            == WithdrawalStatus.PENDING.value
        )
        .count()
    )

    pending_admin_withdrawals = (
        db.query(AdminWithdrawal)
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING.value
        )
        .count()
    )

    platform_available_balance = (
        _platform_available_balance(db)
    )

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        ctx(
            request,
            admin,

            total_sales_volume=(
                total_sales_volume
            ),

            total_commission=(
                total_commission
            ),

            total_creator_earnings=(
                total_creator_earnings
            ),

            successful=successful,
            pending=pending,
            failed=failed,

            recent_orders=recent_orders,
            recent_users=recent_users,
            failed_payments=failed_payments,

            pending_withdrawals=(
                pending_creator_withdrawals
            ),

            pending_creator_withdrawals=(
                pending_creator_withdrawals
            ),

            pending_admin_withdrawals=(
                pending_admin_withdrawals
            ),

            platform_available_balance=(
                platform_available_balance
            ),
        ),
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
            User.email.ilike(f"%{q}%")
        )

    users = (
        query
        .order_by(User.created_at.desc())
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


@router.post("/users/{user_id}/toggle-active")
def admin_toggle_user(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    target = db.get(User, user_id)

    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if target.id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot deactivate your own admin account.",
        )

    target.is_active = not target.is_active

    db.commit()

    return RedirectResponse(
        url="/admin/users?success=User status updated.",
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
            Track.title.ilike(f"%{q}%")
        )

        albums_query = albums_query.filter(
            Album.title.ilike(f"%{q}%")
        )

    tracks = (
        tracks_query
        .order_by(Track.created_at.desc())
        .limit(200)
        .all()
    )

    albums = (
        albums_query
        .order_by(Album.created_at.desc())
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
    track = db.get(Track, track_id)

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found",
        )

    track.is_published = not track.is_published

    db.commit()

    return RedirectResponse(
        url="/admin/content?success=Track updated.",
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
        .order_by(Order.created_at.desc())
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
    admin_note: str = Form(""),
):
    withdrawal = db.get(
        WithdrawalRequest,
        withdrawal_id,
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Creator withdrawal request not found.",
        )

    transitions = {
        "approve": WithdrawalStatus.APPROVED.value,
        "process": WithdrawalStatus.PROCESSING.value,
        "mark_paid": WithdrawalStatus.PAID.value,
        "reject": WithdrawalStatus.REJECTED.value,
    }

    if action not in transitions:
        raise HTTPException(
            status_code=400,
            detail="Invalid withdrawal action.",
        )

    withdrawal.status = transitions[action]

    if payout_reference.strip():
        withdrawal.payout_reference = (
            payout_reference.strip()
        )

    if admin_note.strip():
        withdrawal.admin_note = (
            admin_note.strip()
        )

    if withdrawal.status in (
        WithdrawalStatus.PAID.value,
        WithdrawalStatus.REJECTED.value,
    ):
        withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdrawals"
            "?success=Creator withdrawal updated."
        ),
        status_code=303,
    )


# ----------------------------------------------------------------------
# ADMIN / PLATFORM WITHDRAWAL PAGE
# ----------------------------------------------------------------------

@router.get("/withdraw")
def admin_withdraw_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    commission = _platform_commission(db)
    committed = _admin_withdrawn_amount(db)
    available = _platform_available_balance(db)

    withdrawals = (
        db.query(AdminWithdrawal)
        .order_by(
            AdminWithdrawal.created_at.desc()
        )
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/withdraw.html",
        ctx(
            request,
            admin,

            platform_commission=commission,
            committed_amount=committed,
            available_balance=available,

            withdrawals=withdrawals,
        ),
    )


# Compatibility route in case the UI uses /admin/withdrawals/my
@router.get("/withdrawals/my")
def admin_withdraw_page_compat(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    commission = _platform_commission(db)
    committed = _admin_withdrawn_amount(db)
    available = _platform_available_balance(db)

    withdrawals = (
        db.query(AdminWithdrawal)
        .order_by(
            AdminWithdrawal.created_at.desc()
        )
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/withdraw.html",
        ctx(
            request,
            admin,
            platform_commission=commission,
            committed_amount=committed,
            available_balance=available,
            withdrawals=withdrawals,
        ),
    )


# ----------------------------------------------------------------------
# CREATE ADMIN / PLATFORM WITHDRAWAL
# ----------------------------------------------------------------------

@router.post("/withdraw")
def admin_create_withdrawal(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    amount: str = Form(...),
    phone_number: str = Form(...),
    admin_note: str = Form(""),
):
    phone = (phone_number or "").strip()

    if not phone:
        return RedirectResponse(
            url=(
                "/admin/withdraw"
                "?error=M-Pesa phone number is required."
            ),
            status_code=303,
        )

    try:
        amount_value = Decimal(
            (amount or "").strip()
        )
    except (InvalidOperation, ValueError):
        return RedirectResponse(
            url=(
                "/admin/withdraw"
                "?error=Enter a valid withdrawal amount."
            ),
            status_code=303,
        )

    if amount_value <= Decimal("0"):
        return RedirectResponse(
            url=(
                "/admin/withdraw"
                "?error=Withdrawal amount must be greater than zero."
            ),
            status_code=303,
        )

    available = _platform_available_balance(db)

    if amount_value > available:
        return RedirectResponse(
            url=(
                "/admin/withdraw"
                "?error=Withdrawal exceeds the available platform balance."
            ),
            status_code=303,
        )

    withdrawal = AdminWithdrawal(
        amount=amount_value,
        phone_number=phone,
        status=AdminWithdrawalStatus.PENDING.value,
        admin_note=(
            admin_note.strip()
            if admin_note
            else None
        ),
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdraw"
            "?success=Platform withdrawal request created."
        ),
        status_code=303,
    )


# ----------------------------------------------------------------------
# ADMIN WITHDRAWAL STATUS
# ----------------------------------------------------------------------

@router.post(
    "/withdraw/{withdrawal_id}/update"
)
def admin_update_own_withdrawal(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    action: str = Form(...),
    payout_reference: str = Form(""),
    admin_note: str = Form(""),
):
    withdrawal = db.get(
        AdminWithdrawal,
        withdrawal_id,
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Platform withdrawal not found.",
        )

    transitions = {
        "approve": AdminWithdrawalStatus.APPROVED.value,
        "process": AdminWithdrawalStatus.PROCESSING.value,
        "mark_paid": AdminWithdrawalStatus.PAID.value,
        "reject": AdminWithdrawalStatus.REJECTED.value,
    }

    if action not in transitions:
        raise HTTPException(
            status_code=400,
            detail="Invalid platform withdrawal action.",
        )

    withdrawal.status = transitions[action]

    if payout_reference.strip():
        withdrawal.payout_reference = (
            payout_reference.strip()
        )

    if admin_note.strip():
        withdrawal.admin_note = (
            admin_note.strip()
        )

    if withdrawal.status in (
        AdminWithdrawalStatus.PAID.value,
        AdminWithdrawalStatus.REJECTED.value,
    ):
        withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdraw"
            "?success=Platform withdrawal updated."
        ),
        status_code=303,
    )
