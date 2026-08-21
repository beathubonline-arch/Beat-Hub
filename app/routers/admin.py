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
# PLATFORM BALANCE
# ----------------------------------------------------------------------

def get_platform_balance(db: Session) -> dict:
    """
    BeatHub's platform balance is derived from:

        completed-order commissions
        MINUS admin withdrawals that have been approved,
        processing, or paid.

    Creator earnings are NOT included in the admin balance.
    """

    commission_total = (
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

    withdrawn_total = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status.in_(
                [
                    WithdrawalStatus.APPROVED,
                    WithdrawalStatus.PROCESSING,
                    WithdrawalStatus.PAID,
                ]
            )
        )
        .scalar()
    )

    pending_total = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == WithdrawalStatus.PENDING
        )
        .scalar()
    )

    commission_total = Decimal(
        str(commission_total or 0)
    )

    withdrawn_total = Decimal(
        str(withdrawn_total or 0)
    )

    pending_total = Decimal(
        str(pending_total or 0)
    )

    available = (
        commission_total
        - withdrawn_total
        - pending_total
    )

    if available < Decimal("0"):
        available = Decimal("0")

    return {
        "commission_total": commission_total,
        "withdrawn_total": withdrawn_total,
        "pending_total": pending_total,
        "available_balance": available,
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

    platform_balance = get_platform_balance(db)

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
            platform_balance=platform_balance,
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

    if target.id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot deactivate your own admin account.",
        )

    target.is_active = (
        not target.is_active
    )

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
    admin_note: str = Form(""),
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
        "approve": WithdrawalStatus.APPROVED,
        "process": WithdrawalStatus.PROCESSING,
        "mark_paid": WithdrawalStatus.PAID,
        "reject": WithdrawalStatus.REJECTED,
    }

    if action not in valid_transitions:
        raise HTTPException(
            status_code=400,
            detail="Invalid action",
        )

    wr.status = valid_transitions[action]

    if payout_reference.strip():
        wr.payout_reference = (
            payout_reference.strip()
        )

    if admin_note.strip():
        wr.admin_note = (
            admin_note.strip()
        )

    if wr.status in (
        WithdrawalStatus.PAID,
        WithdrawalStatus.REJECTED,
    ):
        wr.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url="/admin/withdrawals?success=Withdrawal updated.",
        status_code=303,
    )


# ----------------------------------------------------------------------
# ADMIN PLATFORM WITHDRAWAL
# ----------------------------------------------------------------------

@router.get("/withdraw")
def admin_withdraw_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    balance = get_platform_balance(db)

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
            balance=balance,
            withdrawals=withdrawals,
        ),
    )


@router.post("/withdraw")
def admin_withdraw_submit(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    amount: str = Form(...),
    phone_number: str = Form(...),
    admin_note: str = Form(""),
):
    try:
        amount_value = Decimal(
            amount.strip()
        )
    except Exception:
        return RedirectResponse(
            url="/admin/withdraw?error=Invalid withdrawal amount.",
            status_code=303,
        )

    if amount_value <= Decimal("0"):
        return RedirectResponse(
            url="/admin/withdraw?error=Withdrawal amount must be greater than zero.",
            status_code=303,
        )

    phone = (
        phone_number or ""
    ).strip()

    if not phone:
        return RedirectResponse(
            url="/admin/withdraw?error=M-Pesa phone number is required.",
            status_code=303,
        )

    balance = get_platform_balance(db)

    if amount_value > balance["available_balance"]:
        return RedirectResponse(
            url="/admin/withdraw?error=Withdrawal exceeds your available BeatHub balance.",
            status_code=303,
        )

    withdrawal = AdminWithdrawal(
        amount=amount_value,
        phone_number=phone,
        status=WithdrawalStatus.PENDING,
        admin_note=(
            admin_note.strip()
            or None
        ),
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url="/admin/withdraw?success=Admin withdrawal request created.",
        status_code=303,
    )


# ----------------------------------------------------------------------
# ADMIN WITHDRAWAL STATUS
# ----------------------------------------------------------------------

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
            detail="Admin withdrawal not found",
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
            detail="Invalid action",
        )

    new_status = valid_transitions[action]

    if new_status != WithdrawalStatus.REJECTED:
        balance = get_platform_balance(db)

        if (
            new_status
            in (
                WithdrawalStatus.APPROVED,
                WithdrawalStatus.PROCESSING,
                WithdrawalStatus.PAID,
            )
            and withdrawal.amount
            > balance["available_balance"]
            + withdrawal.amount
        ):
            raise HTTPException(
                status_code=400,
                detail="Insufficient platform balance.",
            )

    withdrawal.status = new_status

    if payout_reference.strip():
        withdrawal.payout_reference = (
            payout_reference.strip()
        )

    if admin_note.strip():
        withdrawal.admin_note = (
            admin_note.strip()
        )

    if new_status in (
        WithdrawalStatus.PAID,
        WithdrawalStatus.REJECTED,
    ):
        withdrawal.resolved_at = (
            datetime.utcnow()
        )

    db.commit()

    return RedirectResponse(
        url="/admin/withdraw?success=Admin withdrawal updated.",
        status_code=303,
    )
