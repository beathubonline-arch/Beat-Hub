from datetime import datetime
from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
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
from app.models.payment import (
    PaymentStatus,
    PaymentTransaction,
)
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
    current_user: User,
    **extra,
):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }

    data.update(extra)

    return data


# ============================================================
# ADMIN HOME
# ============================================================

@router.get("")
@router.get("/")
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

    pending_creator_withdrawals = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.status
            == WithdrawalStatus.PENDING
        )
        .count()
    )

    pending_admin_withdrawals = (
        db.query(AdminWithdrawal)
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING
        )
        .count()
    )

    # BeatHub commission.
    platform_earnings = Decimal(
        str(total_commission or 0)
    )

    admin_paid = (
        db.query(
            func.coalesce(
                func.sum(
                    AdminWithdrawal.amount
                ),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PAID
        )
        .scalar()
    )

    admin_processing = (
        db.query(
            func.coalesce(
                func.sum(
                    AdminWithdrawal.amount
                ),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PROCESSING
        )
        .scalar()
    )

    admin_pending = (
        db.query(
            func.coalesce(
                func.sum(
                    AdminWithdrawal.amount
                ),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING
        )
        .scalar()
    )

    platform_available = (
        platform_earnings
        - Decimal(str(admin_paid or 0))
        - Decimal(str(admin_processing or 0))
        - Decimal(str(admin_pending or 0))
    )

    if platform_available < 0:
        platform_available = Decimal("0.00")

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

            pending_withdrawals=(
                pending_creator_withdrawals
            ),

            pending_creator_withdrawals=(
                pending_creator_withdrawals
            ),

            pending_admin_withdrawals=(
                pending_admin_withdrawals
            ),

            platform_earnings=platform_earnings,
            platform_available=platform_available,
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

    target.is_active = not target.is_active

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/users?"
            "success=User status updated."
        ),
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
            "/admin/content?"
            "success=Track updated."
        ),
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


# ============================================================
# CREATOR WITHDRAWALS
# ============================================================

@router.get("/withdrawals")
def admin_creator_withdrawals(
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
def admin_update_creator_withdrawal(
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
            detail="Creator withdrawal not found.",
        )

    transitions = {
        "approve": WithdrawalStatus.APPROVED,
        "process": WithdrawalStatus.PROCESSING,
        "mark_paid": WithdrawalStatus.PAID,
        "reject": WithdrawalStatus.REJECTED,
    }

    if action not in transitions:
        raise HTTPException(
            status_code=400,
            detail="Invalid withdrawal action.",
        )

    withdrawal.status = transitions[
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
        url=(
            "/admin/withdrawals?"
            "success=Creator withdrawal updated."
        ),
        status_code=303,
    )


# ============================================================
# ADMIN PLATFORM WITHDRAWAL
# ============================================================

def _platform_available_balance(
    db: Session,
) -> Decimal:

    commission = (
        db.query(
            func.coalesce(
                func.sum(
                    Order.commission_amount
                ),
                0,
            )
        )
        .filter(
            Order.status
            == OrderStatus.COMPLETED
        )
        .scalar()
    )

    paid = (
        db.query(
            func.coalesce(
                func.sum(
                    AdminWithdrawal.amount
                ),
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
                func.sum(
                    AdminWithdrawal.amount
                ),
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
                func.sum(
                    AdminWithdrawal.amount
                ),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status
            == AdminWithdrawalStatus.PENDING
        )
        .scalar()
    )

    balance = (
        Decimal(str(commission or 0))
        - Decimal(str(paid or 0))
        - Decimal(str(processing or 0))
        - Decimal(str(pending or 0))
    )

    return max(
        balance,
        Decimal("0.00"),
    )


@router.get("/withdraw")
def admin_withdraw(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    available_balance = (
        _platform_available_balance(db)
    )

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
            available_balance=available_balance,
            withdrawals=withdrawals,
        ),
    )


# Compatibility aliases.
@router.get("/withdrawals/platform")
def admin_platform_withdrawals(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return admin_withdraw(
        request=request,
        db=db,
        admin=admin,
    )


@router.post("/withdraw")
def admin_create_withdrawal(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    amount: str = Form(...),
    phone_number: str = Form(...),
    admin_note: str = Form(""),
):
    available_balance = (
        _platform_available_balance(db)
    )

    try:
        amount_value = Decimal(
            (amount or "").strip()
        )
    except Exception:
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=Invalid withdrawal amount."
            ),
            status_code=303,
        )

    if amount_value <= 0:
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=Withdrawal amount must be greater than zero."
            ),
            status_code=303,
        )

    if amount_value > available_balance:
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=Withdrawal exceeds your available BeatHub balance."
            ),
            status_code=303,
        )

    phone = (
        phone_number or ""
    ).strip()

    if not phone:
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=M-Pesa phone number is required."
            ),
            status_code=303,
        )

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
        url=(
            "/admin/withdraw?"
            "success=BeatHub withdrawal request created."
        ),
        status_code=303,
    )


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

    actions = {
        "process": AdminWithdrawalStatus.PROCESSING,
        "mark_paid": AdminWithdrawalStatus.PAID,
        "reject": AdminWithdrawalStatus.REJECTED,
    }

    if action not in actions:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin withdrawal action.",
        )

    withdrawal.status = actions[
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
        url=(
            "/admin/withdraw?"
            "success=BeatHub withdrawal updated."
        ),
        status_code=303,
    )
