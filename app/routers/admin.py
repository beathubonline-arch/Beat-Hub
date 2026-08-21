from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import (
    AdminWithdrawal,
    WithdrawalRequest,
)
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.models.music import Track, Album
from app.utils.deps import require_admin


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)

templates = Jinja2Templates(
    directory="app/templates"
)


# =========================================================
# HELPERS
# =========================================================

ZERO = Decimal("0")


def money(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return ZERO


def status_value(value):
    """
    Safely return enum/string status.
    """
    if value is None:
        return ""

    try:
        return value.value
    except AttributeError:
        return str(value)


def admin_ctx(
    request: Request,
    current_user,
    **extra,
):
    """
    Common context.

    IMPORTANT:
    Keep all common values defined so Jinja templates never
    fail because a variable is missing.
    """

    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,

        # Basic statistics
        "users_count": 0,
        "tracks_count": 0,
        "albums_count": 0,
        "completed_sales": 0,

        # Financial statistics
        "total_sales_volume": ZERO,
        "total_commission": ZERO,
        "total_creator_earnings": ZERO,
        "platform_revenue": ZERO,

        # Withdrawal statistics
        "pending_creator_withdrawals": 0,
        "pending_withdrawals": 0,
        "already_withdrawn": ZERO,
        "pending_admin": ZERO,
        "available_balance": ZERO,

        # Order statistics
        "successful": 0,
        "pending": 0,
        "failed": 0,

        # Lists
        "recent_orders": [],
        "recent_users": [],
        "failed_payments": [],
        "admin_withdrawals": [],
        "withdrawals": [],
        "sales": [],
        "orders": [],

        # Users/content
        "users": [],
        "tracks": [],
        "albums": [],
        "q": "",
    }

    data.update(extra)

    return data


def get_platform_balance(db: Session):
    """
    Single source of truth for BeatHub platform commission.

    Templates:
        balance.commission_total
        balance.withdrawn_total
        balance.pending_total
        balance.available_balance

    Also returns the individual values for other templates.
    """

    commission_raw = (
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

    commission_total = money(commission_raw)

    withdrawn_raw = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status.in_(
                [
                    "approved",
                    "processing",
                    "paid",
                ]
            )
        )
        .scalar()
    )

    withdrawn_total = money(withdrawn_raw)

    pending_raw = (
        db.query(
            func.coalesce(
                func.sum(AdminWithdrawal.amount),
                0,
            )
        )
        .filter(
            AdminWithdrawal.status == "pending"
        )
        .scalar()
    )

    pending_total = money(pending_raw)

    available_balance = (
        commission_total
        - withdrawn_total
        - pending_total
    )

    if available_balance < ZERO:
        available_balance = ZERO

    balance = SimpleNamespace(
        commission_total=commission_total,
        withdrawn_total=withdrawn_total,
        pending_total=pending_total,
        available_balance=available_balance,
    )

    return (
        balance,
        commission_total,
        withdrawn_total,
        pending_total,
        available_balance,
    )


def redirect_message(
    path: str,
    parameter: str,
    message: str,
):
    """
    Safely create a redirect without needing manual URL encoding.
    """
    from urllib.parse import quote

    return RedirectResponse(
        url=f"{path}?{parameter}={quote(message)}",
        status_code=303,
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@router.get("")
@router.get("/")
def admin_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    users_count = db.query(User).count()

    tracks_count = db.query(Track).count()

    albums_count = db.query(Album).count()

    completed_sales = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.COMPLETED
        )
        .count()
    )

    successful = completed_sales

    pending = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING
        )
        .count()
    )

    failed_statuses = []

    if hasattr(OrderStatus, "FAILED"):
        failed_statuses.append(
            OrderStatus.FAILED
        )

    if hasattr(OrderStatus, "REJECTED"):
        failed_statuses.append(
            OrderStatus.REJECTED
        )

    if hasattr(OrderStatus, "CANCELLED"):
        failed_statuses.append(
            OrderStatus.CANCELLED
        )

    if failed_statuses:
        failed = (
            db.query(Order)
            .filter(
                Order.status.in_(failed_statuses)
            )
            .count()
        )
    else:
        failed = 0

    # -----------------------------------------------------
    # SALES VOLUME
    # Order.total_amount DOES NOT EXIST.
    # The model uses gross_amount.
    # -----------------------------------------------------

    total_sales_volume_raw = (
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

    total_sales_volume = money(
        total_sales_volume_raw
    )

    # -----------------------------------------------------
    # COMMISSION
    # -----------------------------------------------------

    total_commission_raw = (
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

    total_commission = money(
        total_commission_raw
    )

    # -----------------------------------------------------
    # CREATOR EARNINGS
    # -----------------------------------------------------

    total_creator_earnings_raw = (
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

    total_creator_earnings = money(
        total_creator_earnings_raw
    )

    platform_revenue = total_commission

    # -----------------------------------------------------
    # CREATOR WITHDRAWALS
    # -----------------------------------------------------

    pending_creator_withdrawals = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.status == "pending"
        )
        .count()
    )

    pending_withdrawals = (
        pending_creator_withdrawals
    )

    # -----------------------------------------------------
    # RECENT ORDERS
    # -----------------------------------------------------

    recent_orders = (
        db.query(Order)
        .order_by(
            Order.created_at.desc()
        )
        .limit(10)
        .all()
    )

    # -----------------------------------------------------
    # RECENT USERS
    # -----------------------------------------------------

    recent_users = (
        db.query(User)
        .order_by(
            User.created_at.desc()
        )
        .limit(10)
        .all()
    )

    # -----------------------------------------------------
    # FAILED PAYMENTS
    # -----------------------------------------------------

    failed_payments = []

    if failed_statuses:
        failed_payments = (
            db.query(Order)
            .filter(
                Order.status.in_(
                    failed_statuses
                )
            )
            .order_by(
                Order.created_at.desc()
            )
            .limit(10)
            .all()
        )

    # -----------------------------------------------------
    # ADMIN WITHDRAWALS
    # -----------------------------------------------------

    admin_withdrawals = (
        db.query(AdminWithdrawal)
        .order_by(
            AdminWithdrawal.created_at.desc()
        )
        .limit(10)
        .all()
    )

    (
        balance,
        already_withdrawn,
        withdrawn_total,
        pending_admin,
        available_balance,
    ) = (
        get_platform_balance(db)
    )

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        admin_ctx(
            request,
            user,

            users_count=users_count,
            tracks_count=tracks_count,
            albums_count=albums_count,

            completed_sales=completed_sales,

            total_sales_volume=(
                total_sales_volume
            ),

            total_commission=(
                total_commission
            ),

            total_creator_earnings=(
                total_creator_earnings
            ),

            platform_revenue=(
                platform_revenue
            ),

            pending_creator_withdrawals=(
                pending_creator_withdrawals
            ),

            pending_withdrawals=(
                pending_withdrawals
            ),

            successful=successful,
            pending=pending,
            failed=failed,

            recent_orders=recent_orders,
            recent_users=recent_users,
            failed_payments=failed_payments,

            admin_withdrawals=(
                admin_withdrawals
            ),

            balance=balance,

            already_withdrawn=(
                withdrawn_total
            ),

            pending_admin=(
                pending_admin
            ),

            available_balance=(
                available_balance
            ),
        ),
    )


# =========================================================
# USERS
# =========================================================

@router.get("/users")
def admin_users(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    q = (q or "").strip()

    query = db.query(User)

    if q:
        search = f"%{q}%"

        query = query.filter(
            or_(
                User.email.ilike(search),
                User.username.ilike(search),
            )
        )

    users = (
        query
        .order_by(
            User.created_at.desc()
        )
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/users.html",
        admin_ctx(
            request,
            user,
            users=users,
            q=q,
        ),
    )


@router.post(
    "/users/{user_id}/toggle-active"
)
def toggle_user_active(
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    target = (
        db.query(User)
        .filter(
            User.id == user_id
        )
        .first()
    )

    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    # Never deactivate yourself.
    if str(target.id) == str(user.id):
        return redirect_message(
            "/admin/users",
            "error",
            "You cannot deactivate your own admin account.",
        )

    target.is_active = not bool(
        target.is_active
    )

    target.updated_at = datetime.utcnow()

    db.commit()

    state = (
        "activated"
        if target.is_active
        else "deactivated"
    )

    return redirect_message(
        "/admin/users",
        "success",
        f"User {state} successfully.",
    )


# =========================================================
# CONTENT
# =========================================================

@router.get("/content")
def admin_content(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    q = (q or "").strip()

    track_query = db.query(Track)

    album_query = db.query(Album)

    if q:
        search = f"%{q}%"

        track_query = track_query.filter(
            or_(
                Track.title.ilike(search),
                Track.genre.ilike(search),
                Track.tags.ilike(search),
            )
        )

        album_query = album_query.filter(
            or_(
                Album.title.ilike(search),
                Album.genre.ilike(search),
            )
        )

    tracks = (
        track_query
        .order_by(
            Track.created_at.desc()
        )
        .all()
    )

    albums = (
        album_query
        .order_by(
            Album.created_at.desc()
        )
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/content.html",
        admin_ctx(
            request,
            user,
            tracks=tracks,
            albums=albums,
            q=q,
        ),
    )


@router.post(
    "/content/track/{track_id}/toggle-published"
)
def toggle_track_published(
    track_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    track = (
        db.query(Track)
        .filter(
            Track.id == track_id
        )
        .first()
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    track.is_published = not bool(
        track.is_published
    )

    track.updated_at = datetime.utcnow()

    db.commit()

    state = (
        "published"
        if track.is_published
        else "hidden"
    )

    return redirect_message(
        "/admin/content",
        "success",
        f"Track {state} successfully.",
    )


# =========================================================
# SALES
# =========================================================

@router.get("/sales")
def admin_sales(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    sales = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.COMPLETED
        )
        .order_by(
            Order.created_at.desc()
        )
        .all()
    )

    total_sales_volume_raw = (
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

    total_sales_volume = money(
        total_sales_volume_raw
    )

    total_commission_raw = (
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

    total_commission = money(
        total_commission_raw
    )

    total_creator_earnings_raw = (
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

    total_creator_earnings = money(
        total_creator_earnings_raw
    )

    return templates.TemplateResponse(
        request,
        "admin/sales.html",
        admin_ctx(
            request,
            user,

            sales=sales,
            orders=sales,

            sales_count=len(sales),

            total_sales_volume=(
                total_sales_volume
            ),

            total_commission=(
                total_commission
            ),

            total_creator_earnings=(
                total_creator_earnings
            ),

            platform_revenue=(
                total_commission
            ),
        ),
    )


# =========================================================
# CREATOR WITHDRAWALS
# =========================================================

@router.get("/withdrawals")
def creator_withdrawals(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    withdrawals = (
        db.query(WithdrawalRequest)
        .order_by(
            WithdrawalRequest.created_at.desc()
        )
        .all()
    )

    # The withdrawals template expects:
    #
    # balance.commission_total
    # balance.withdrawn_total
    # balance.pending_total
    # balance.available_balance

    (
        balance,
        commission_total,
        withdrawn_total,
        pending_total,
        available_balance,
    ) = get_platform_balance(db)

    return templates.TemplateResponse(
        request,
        "admin/withdrawals.html",
        admin_ctx(
            request,
            user,

            withdrawals=withdrawals,

            balance=balance,

            platform_revenue=(
                commission_total
            ),

            total_commission=(
                commission_total
            ),

            already_withdrawn=(
                withdrawn_total
            ),

            pending_admin=(
                pending_total
            ),

            available_balance=(
                available_balance
            ),
        ),
    )


# =========================================================
# APPROVE CREATOR WITHDRAWAL
# =========================================================

@router.post(
    "/withdrawals/{withdrawal_id}/approve"
)
def approve_creator_withdrawal(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    withdrawal = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.id
            == withdrawal_id
        )
        .first()
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Withdrawal request not found.",
        )

    if withdrawal.status != "pending":
        return redirect_message(
            "/admin/withdrawals",
            "error",
            "Only pending withdrawals can be approved.",
        )

    withdrawal.status = "approved"

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    withdrawal.admin_note = (
        "Withdrawal approved by administrator."
    )

    db.commit()

    return redirect_message(
        "/admin/withdrawals",
        "success",
        "Creator withdrawal approved.",
    )


# =========================================================
# REJECT CREATOR WITHDRAWAL
# =========================================================

@router.post(
    "/withdrawals/{withdrawal_id}/reject"
)
def reject_creator_withdrawal(
    withdrawal_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    withdrawal = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.id
            == withdrawal_id
        )
        .first()
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Withdrawal request not found.",
        )

    withdrawal.status = "rejected"

    withdrawal.admin_note = (
        note.strip()
        or "Withdrawal rejected by administrator."
    )

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    withdrawal.resolved_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_message(
        "/admin/withdrawals",
        "success",
        "Creator withdrawal rejected.",
    )


# =========================================================
# ADMIN / BEATHUB OWN WITHDRAWAL PAGE
# =========================================================

@router.get("/withdraw")
def admin_withdraw_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    (
        balance,
        platform_revenue,
        already_withdrawn,
        pending_admin,
        available_balance,
    ) = get_platform_balance(db)

    withdrawals = (
        db.query(AdminWithdrawal)
        .order_by(
            AdminWithdrawal.created_at.desc()
        )
        .all()
    )

    return templates.TemplateResponse(
        request,
        "admin/withdraw.html",
        admin_ctx(
            request,
            user,

            balance=balance,

            platform_revenue=(
                platform_revenue
            ),

            already_withdrawn=(
                already_withdrawn
            ),

            pending_admin=(
                pending_admin
            ),

            available_balance=(
                available_balance
            ),

            withdrawals=withdrawals,

            admin_withdrawals=withdrawals,
        ),
    )


# =========================================================
# CREATE ADMIN WITHDRAWAL
# =========================================================

@router.post("/withdraw")
def create_admin_withdrawal(
    amount: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        amount_val = Decimal(
            amount.strip()
        )
    except (InvalidOperation, ValueError):
        return redirect_message(
            "/admin/withdraw",
            "error",
            "Invalid withdrawal amount.",
        )

    if amount_val <= ZERO:
        return redirect_message(
            "/admin/withdraw",
            "error",
            "Amount must be greater than zero.",
        )

    phone_number = (
        phone_number or ""
    ).strip()

    if not phone_number:
        return redirect_message(
            "/admin/withdraw",
            "error",
            "M-Pesa number is required.",
        )

    (
        balance,
        platform_revenue,
        already_withdrawn,
        pending_admin,
        available,
    ) = get_platform_balance(db)

    if amount_val > available:
        return redirect_message(
            "/admin/withdraw",
            "error",
            "Withdrawal exceeds available BeatHub balance.",
        )

    withdrawal = AdminWithdrawal(
        amount=amount_val,
        phone_number=phone_number,
        status="pending",
        admin_note=(
            note.strip() or None
        ),
    )

    db.add(withdrawal)
    db.commit()

    return redirect_message(
        "/admin/withdraw",
        "success",
        "Withdrawal request created.",
    )


# =========================================================
# APPROVE ADMIN WITHDRAWAL
# =========================================================

@router.post(
    "/withdraw/{withdrawal_id}/approve"
)
def approve_admin_withdrawal(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(
            AdminWithdrawal.id
            == withdrawal_id
        )
        .first()
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Admin withdrawal not found.",
        )

    if withdrawal.status != "pending":
        return redirect_message(
            "/admin/withdraw",
            "error",
            "Only pending withdrawals can be approved.",
        )

    withdrawal.status = "approved"

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_message(
        "/admin/withdraw",
        "success",
        "Admin withdrawal approved.",
    )


# =========================================================
# REJECT ADMIN WITHDRAWAL
# =========================================================

@router.post(
    "/withdraw/{withdrawal_id}/reject"
)
def reject_admin_withdrawal(
    withdrawal_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(
            AdminWithdrawal.id
            == withdrawal_id
        )
        .first()
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Admin withdrawal not found.",
        )

    withdrawal.status = "rejected"

    withdrawal.admin_note = (
        note.strip()
        or "Admin withdrawal rejected."
    )

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    withdrawal.resolved_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_message(
        "/admin/withdraw",
        "success",
        "Admin withdrawal rejected.",
    )


# =========================================================
# MARK ADMIN WITHDRAWAL PAID
# =========================================================

@router.post(
    "/withdraw/{withdrawal_id}/paid"
)
def mark_admin_withdrawal_paid(
    withdrawal_id: str,
    payout_reference: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(
            AdminWithdrawal.id
            == withdrawal_id
        )
        .first()
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Admin withdrawal not found.",
        )

    withdrawal.status = "paid"

    withdrawal.payout_reference = (
        payout_reference.strip()
        or None
    )

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    withdrawal.resolved_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_message(
        "/admin/withdraw",
        "success",
        "Admin withdrawal marked as paid.",
    )
