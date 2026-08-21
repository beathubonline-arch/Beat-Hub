from datetime import datetime
from decimal import Decimal

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
# ADMIN DASHBOARD
# ============================================================

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

    pending_withdrawals = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.status
            == WithdrawalStatus.PENDING
        )
        .count()
    )

    # --------------------------------------------------------
    # ADMIN / BEATHUB WITHDRAWALS
    # IMPORTANT:
    # Use AdminWithdrawalStatus here.
    # --------------------------------------------------------

    admin_withdrawals_pending = (
        db.query(AdminWithdrawal)
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING
        )
        .count()
    )

    admin_withdrawals = (
        db.query(AdminWithdrawal)
        .order_by(
            AdminWithdrawal.created_at.desc()
        )
        .limit(10)
        .all()
    )

    # --------------------------------------------------------
    # BeatHub platform earnings
    #
    # This is the commission earned by BeatHub.
    # --------------------------------------------------------

    platform_earnings = Decimal(
        str(total_commission or 0)
    )

    # Money already paid out from the platform account.
    platform_paid = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PAID
        )
        .scalar()
    )

    platform_processing = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PROCESSING
        )
        .scalar()
    )

    platform_pending = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING
        )
        .scalar()
    )

    platform_available_balance = (
        platform_earnings
        - Decimal(str(platform_paid or 0))
        - Decimal(str(platform_processing or 0))
        - Decimal(str(platform_pending or 0))
    )

    if platform_available_balance < 0:
        platform_available_balance = Decimal("0.00")

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

            admin_withdrawals_pending=(
                admin_withdrawals_pending
            ),

            admin_withdrawals=admin_withdrawals,

            platform_earnings=platform_earnings,
            platform_paid=Decimal(
                str(platform_paid or 0)
            ),
            platform_processing=Decimal(
                str(platform_processing or 0)
            ),
            platform_pending=Decimal(
                str(platform_pending or 0)
            ),
            platform_available_balance=(
                platform_available_balance
            ),
        ),
    )


# ============================================================
# USERS
# ============================================================

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

    # Never allow an admin to deactivate
    # their own account accidentally.
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


# ============================================================
# CONTENT
# ============================================================

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
    track = db.get(
        Track,
        track_id,
    )

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


# ============================================================
# SALES
# ============================================================

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


# ============================================================
# CREATOR WITHDRAWALS
# ============================================================

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
            detail="Withdrawal request not found",
        )

    valid_transitions = {
        "approve": WithdrawalStatus.APPROVED,
        "process": WithdrawalStatus.PROCESSING,
        "mark_paid": WithdrawalStatus.PAID,
        "reject": WithdrawalStatus.REJECTED,
    }

    if action not in valid_transitions:
        raise HTTPException(
            status_code=400,
            detail="Invalid withdrawal action.",
        )

    withdrawal.status = valid_transitions[
        action
    ]

    if payout_reference.strip():
        withdrawal.payout_reference = (
            payout_reference.strip()
        )

    if admin_note.strip():
        withdrawal.admin_note = (
            admin_note.strip()
        )

    if withdrawal.status in (
        WithdrawalStatus.PAID,
        WithdrawalStatus.REJECTED,
    ):
        withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url="/admin/withdrawals?success=Withdrawal updated.",
        status_code=303,
    )


# ============================================================
# ADMIN / BEATHUB WITHDRAWAL PAGE
# ============================================================

@router.get("/withdraw")
def admin_withdraw_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
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

    platform_earnings = Decimal(
        str(total_commission or 0)
    )

    paid = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PAID
        )
        .scalar()
    )

    processing = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PROCESSING
        )
        .scalar()
    )

    pending = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING
        )
        .scalar()
    )

    available_balance = (
        platform_earnings
        - Decimal(str(paid or 0))
        - Decimal(str(processing or 0))
        - Decimal(str(pending or 0))
    )

    if available_balance < 0:
        available_balance = Decimal("0.00")

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

            platform_earnings=platform_earnings,

            paid=Decimal(
                str(paid or 0)
            ),

            processing=Decimal(
                str(processing or 0)
            ),

            pending=Decimal(
                str(pending or 0)
            ),

            available_balance=available_balance,

            withdrawals=withdrawals,
        ),
    )


# ============================================================
# CREATE ADMIN / BEATHUB WITHDRAWAL
# ============================================================

@router.post("/withdraw")
def admin_create_withdrawal(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    amount: str = Form(...),
    phone_number: str = Form(...),
    admin_note: str = Form(""),
):
    # --------------------------------------------------------
    # Calculate actual BeatHub commission earned.
    # --------------------------------------------------------

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

    platform_earnings = Decimal(
        str(total_commission or 0)
    )

    # --------------------------------------------------------
    # Calculate money already committed/withdrawn.
    # --------------------------------------------------------

    paid = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PAID
        )
        .scalar()
    )

    processing = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PROCESSING
        )
        .scalar()
    )

    pending = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING
        )
        .scalar()
    )

    available_balance = (
        platform_earnings
        - Decimal(str(paid or 0))
        - Decimal(str(processing or 0))
        - Decimal(str(pending or 0))
    )

    if available_balance < 0:
        available_balance = Decimal("0.00")

    # --------------------------------------------------------
    # Validate amount.
    # --------------------------------------------------------

    try:
        amount_value = Decimal(
            amount.strip()
        )
    except Exception:
        return RedirectResponse(
            url="/admin/withdraw?error=Invalid withdrawal amount.",
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url="/admin/withdraw?error=Withdrawal amount must be greater than zero.",
            status_code=303,
        )

    if amount_value > available_balance:
        return RedirectResponse(
            url="/admin/withdraw?error=Withdrawal exceeds your available BeatHub balance.",
            status_code=303,
        )

    # --------------------------------------------------------
    # Validate phone.
    # --------------------------------------------------------

    phone = (
        phone_number or ""
    ).strip()

    if not phone:
        return RedirectResponse(
            url="/admin/withdraw?error=M-Pesa phone number is required.",
            status_code=303,
        )

    # --------------------------------------------------------
    # Create withdrawal.
    #
    # We create it as PENDING first.
    # Actual M-Pesa payout should only happen through the
    # configured payout process.
    # --------------------------------------------------------

    withdrawal = AdminWithdrawal(
        amount=amount_value,
        phone_number=phone,
        status=AdminWithdrawalStatus.PENDING,
        admin_note=(
            admin_note.strip()
            if admin_note
            else None
        ),
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url="/admin/withdraw?success=BeatHub withdrawal request created.",
        status_code=303,
    )


# ============================================================
# UPDATE ADMIN WITHDRAWAL
# ============================================================

@router.post(
    "/withdraw/{withdrawal_id}/update"
)
def admin_update_platform_withdrawal(
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
            detail="Admin withdrawal not found.",
        )

    valid_actions = {
        "process": AdminWithdrawalStatus.PROCESSING,
        "mark_paid": AdminWithdrawalStatus.PAID,
        "reject": AdminWithdrawalStatus.REJECTED,
    }

    if action not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin withdrawal action.",
        )

    withdrawal.status = valid_actions[
        action
    ]

    if payout_reference.strip():
        withdrawal.payout_reference = (
            payout_reference.strip()
        )

    if admin_note.strip():
        withdrawal.admin_note = (
            admin_note.strip()
        )

    if withdrawal.status in (
        AdminWithdrawalStatus.PAID,
        AdminWithdrawalStatus.REJECTED,
    ):
        withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url="/admin/withdraw?success=Admin withdrawal updated.",
        status_code=303,
    )
