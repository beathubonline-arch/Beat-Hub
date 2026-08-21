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
from app.models.music import Album, Track
from app.models.order import Order, OrderStatus
from app.models.user import User
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

def admin_ctx(
    request: Request,
    current_user,
    **extra,
):
    """
    Common context for every admin template.

    Defaults are intentionally supplied so a template never
    crashes just because a route forgot one variable.
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
        "total_sales_volume": Decimal("0"),
        "total_commission": Decimal("0"),
        "total_creator_earnings": Decimal("0"),
        "platform_revenue": Decimal("0"),

        # Withdrawal statistics
        "pending_creator_withdrawals": 0,
        "pending_withdrawals": 0,
        "already_withdrawn": Decimal("0"),
        "pending_admin": Decimal("0"),
        "available_balance": Decimal("0"),

        # Order statistics
        "successful": 0,
        "pending": 0,
        "failed": 0,

        # Collections
        "recent_orders": [],
        "recent_users": [],
        "failed_payments": [],
        "admin_withdrawals": [],
        "withdrawals": [],
        "sales": [],
        "orders": [],
        "users": [],
        "tracks": [],
        "albums": [],

        # Search/filter values
        "q": "",
        "status": "",

        # Withdrawal compatibility object
        "balance": SimpleNamespace(
            commission_total=Decimal("0"),
            withdrawn_total=Decimal("0"),
            pending_total=Decimal("0"),
            available_balance=Decimal("0"),
        ),
    }

    data.update(extra)

    return data


def redirect_admin(
    path: str,
    message: str,
    error: bool = False,
):
    """
    Safe redirect helper for admin actions.
    """

    key = "error" if error else "success"

    from urllib.parse import quote

    return RedirectResponse(
        url=f"{path}?{key}={quote(message)}",
        status_code=303,
    )


def status_value(status):
    """
    Convert SQLAlchemy enum/string status into a plain string.
    """

    if status is None:
        return ""

    value = getattr(status, "value", status)

    return str(value).lower()


def parse_order_status(value: str | None):
    """
    Convert a query-string status into OrderStatus safely.
    """

    if not value:
        return None

    normalized = value.strip().lower()

    for item in OrderStatus:
        if item.value.lower() == normalized:
            return item

    return None


def failed_order_statuses():
    """
    Current OrderStatus defines FAILED and REJECTED.
    Keep this defensive in case the enum changes later.
    """

    values = []

    for name in ("FAILED", "REJECTED"):
        item = getattr(OrderStatus, name, None)

        if item is not None:
            values.append(item)

    return values


def get_platform_financials(db: Session):
    """
    Calculate BeatHub platform financials from completed orders.

    IMPORTANT:
    The current Order model contains:
        gross_amount
        commission_amount
        net_amount

    There is NO total_amount field.
    """

    completed_filter = (
        Order.status == OrderStatus.COMPLETED
    )

    gross_raw = (
        db.query(
            func.coalesce(
                func.sum(Order.gross_amount),
                0,
            )
        )
        .filter(completed_filter)
        .scalar()
    )

    commission_raw = (
        db.query(
            func.coalesce(
                func.sum(Order.commission_amount),
                0,
            )
        )
        .filter(completed_filter)
        .scalar()
    )

    creator_raw = (
        db.query(
            func.coalesce(
                func.sum(Order.net_amount),
                0,
            )
        )
        .filter(completed_filter)
        .scalar()
    )

    total_sales_volume = Decimal(
        str(gross_raw or 0)
    )

    total_commission = Decimal(
        str(commission_raw or 0)
    )

    total_creator_earnings = Decimal(
        str(creator_raw or 0)
    )

    return (
        total_sales_volume,
        total_commission,
        total_creator_earnings,
    )


def get_admin_withdrawal_financials(db: Session):
    """
    Calculate BeatHub's own withdrawal balance.
    """

    (
        _total_sales_volume,
        platform_revenue,
        _creator_earnings,
    ) = get_platform_financials(db)

    already_withdrawn_raw = (
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

    pending_admin_raw = (
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

    already_withdrawn = Decimal(
        str(already_withdrawn_raw or 0)
    )

    pending_admin = Decimal(
        str(pending_admin_raw or 0)
    )

    available = (
        platform_revenue
        - already_withdrawn
        - pending_admin
    )

    if available < 0:
        available = Decimal("0")

    balance = SimpleNamespace(
        commission_total=platform_revenue,
        withdrawn_total=already_withdrawn,
        pending_total=pending_admin,
        available_balance=available,
    )

    return (
        platform_revenue,
        already_withdrawn,
        pending_admin,
        available,
        balance,
    )


# =========================================================
# ADMIN HOME / OVERVIEW
# =========================================================

@router.get("")
@router.get("/")
def admin_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # -----------------------------------------------------
    # BASIC COUNTS
    # -----------------------------------------------------

    users_count = (
        db.query(User)
        .count()
    )

    tracks_count = (
        db.query(Track)
        .count()
    )

    albums_count = (
        db.query(Album)
        .count()
    )

    completed_sales = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.COMPLETED
        )
        .count()
    )

    # -----------------------------------------------------
    # ORDER STATUS COUNTS
    # -----------------------------------------------------

    successful = completed_sales

    pending = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.PENDING
        )
        .count()
    )

    failed_statuses = failed_order_statuses()

    if failed_statuses:
        failed = (
            db.query(Order)
            .filter(
                Order.status.in_(
                    failed_statuses
                )
            )
            .count()
        )
    else:
        failed = 0

    # -----------------------------------------------------
    # FINANCIALS
    # -----------------------------------------------------

    (
        total_sales_volume,
        total_commission,
        total_creator_earnings,
    ) = get_platform_financials(db)

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

    # The dashboard calls this pending_withdrawals.
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
                Order.updated_at.desc()
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

    # -----------------------------------------------------
    # RENDER
    # -----------------------------------------------------

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
        ),
    )


# =========================================================
# ADMIN SALES
# =========================================================

@router.get("/sales")
def admin_sales(
    request: Request,
    status: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """
    Sales tab.

    Supports:
        /admin/sales
        /admin/sales?status=completed
        /admin/sales?status=pending
        /admin/sales?status=failed
        /admin/sales?status=rejected
    """

    query = db.query(Order)

    parsed_status = parse_order_status(status)

    if parsed_status is not None:
        query = query.filter(
            Order.status == parsed_status
        )

    orders = (
        query
        .order_by(
            Order.created_at.desc()
        )
        .all()
    )

    # Keep sales variable because the template also
    # expects it.
    sales = orders

    # Financial totals are always based on completed
    # sales, not whatever filter is selected.
    (
        total_sales_volume,
        total_commission,
        total_creator_earnings,
    ) = get_platform_financials(db)

    return templates.TemplateResponse(
        request,
        "admin/sales.html",
        admin_ctx(
            request,
            user,

            orders=orders,
            sales=sales,
            sales_count=len(sales),

            status=status or "",

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
# ADMIN USERS
# =========================================================

@router.get("/users")
def admin_users(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """
    Users tab.

    Supports email and username search.
    """

    search = (q or "").strip()

    query = db.query(User)

    if search:
        pattern = f"%{search}%"

        query = query.filter(
            or_(
                User.email.ilike(pattern),
                User.username.ilike(pattern),
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
            q=search,
        ),
    )


# =========================================================
# TOGGLE USER ACTIVE STATUS
# =========================================================

@router.post(
    "/users/{user_id}/toggle-active"
)
def toggle_user_active(
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    target_user = (
        db.query(User)
        .filter(
            User.id == user_id
        )
        .first()
    )

    if not target_user:
        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    # Prevent administrator from accidentally
    # disabling their own account.
    if target_user.id == user.id:
        return redirect_admin(
            "/admin/users",
            "You cannot deactivate your own administrator account.",
            error=True,
        )

    target_user.is_active = (
        not bool(target_user.is_active)
    )

    target_user.updated_at = (
        datetime.utcnow()
    )

    db.commit()

    if target_user.is_active:
        message = "User account activated."
    else:
        message = "User account deactivated."

    return redirect_admin(
        "/admin/users",
        message,
    )


# =========================================================
# ADMIN CONTENT
# =========================================================

@router.get("/content")
def admin_content(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """
    Content tab.

    Shows tracks and albums.
    """

    search = (q or "").strip()

    track_query = db.query(Track)

    album_query = db.query(Album)

    if search:
        pattern = f"%{search}%"

        track_query = track_query.filter(
            or_(
                Track.title.ilike(pattern),
                Track.slug.ilike(pattern),
                Track.genre.ilike(pattern),
                Track.tags.ilike(pattern),
            )
        )

        album_query = album_query.filter(
            or_(
                Album.title.ilike(pattern),
                Album.slug.ilike(pattern),
                Album.genre.ilike(pattern),
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
            q=search,
        ),
    )


# =========================================================
# TOGGLE TRACK PUBLISHED
# =========================================================

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

    track.is_published = (
        not bool(track.is_published)
    )

    track.updated_at = (
        datetime.utcnow()
    )

    db.commit()

    if track.is_published:
        message = "Track published."
    else:
        message = "Track unpublished."

    return redirect_admin(
        "/admin/content",
        message,
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
    """
    Creator withdrawal management.

    IMPORTANT:
    WithdrawalRequest.status is a VARCHAR/string,
    so comparisons use lowercase strings.
    """

    withdrawals = (
        db.query(WithdrawalRequest)
        .order_by(
            WithdrawalRequest.created_at.desc()
        )
        .all()
    )

    # Calculate creator withdrawal summary.
    pending_amount_raw = (
        db.query(
            func.coalesce(
                func.sum(
                    WithdrawalRequest.amount
                ),
                0,
            )
        )
        .filter(
            WithdrawalRequest.status
            == "pending"
        )
        .scalar()
    )

    pending_amount = Decimal(
        str(pending_amount_raw or 0)
    )

    # Creator earnings from completed orders.
    (
        _sales,
        total_commission,
        total_creator_earnings,
    ) = get_platform_financials(db)

    # This object is included for compatibility with
    # older withdrawal templates.
    balance = SimpleNamespace(
        commission_total=total_commission,
        withdrawn_total=Decimal("0"),
        pending_total=pending_amount,
        available_balance=(
            total_creator_earnings
        ),
    )

    return templates.TemplateResponse(
        request,
        "admin/withdrawals.html",
        admin_ctx(
            request,
            user,

            withdrawals=withdrawals,

            # Compatibility fields
            balance=balance,

            total_commission=(
                total_commission
            ),
            total_creator_earnings=(
                total_creator_earnings
            ),
            pending_withdrawal_amount=(
                pending_amount
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
        return redirect_admin(
            "/admin/withdrawals",
            "Only pending creator withdrawals can be approved.",
            error=True,
        )

    withdrawal.status = "approved"

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    withdrawal.admin_note = (
        "Withdrawal approved by administrator."
    )

    db.commit()

    return redirect_admin(
        "/admin/withdrawals",
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
    admin_note: str = Form(""),
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

    final_note = (
        admin_note.strip()
        or note.strip()
        or "Withdrawal rejected by administrator."
    )

    withdrawal.status = "rejected"

    withdrawal.admin_note = final_note

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    withdrawal.resolved_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_admin(
        "/admin/withdrawals",
        "Creator withdrawal rejected.",
    )


# =========================================================
# BEATHUB / ADMIN WITHDRAWAL PAGE
# =========================================================

@router.get("/withdraw")
def admin_withdraw_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    (
        platform_revenue,
        already_withdrawn,
        pending_admin,
        available,
        balance,
    ) = get_admin_withdrawal_financials(db)

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
                available
            ),

            withdrawals=withdrawals,
            admin_withdrawals=withdrawals,

            # REQUIRED BY THE CURRENT TEMPLATE
            balance=balance,
        ),
    )


# =========================================================
# CREATE BEATHUB / ADMIN WITHDRAWAL
# =========================================================

@router.post("/withdraw")
def create_admin_withdrawal(
    amount: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(""),
    admin_note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # -----------------------------------------------------
    # AMOUNT
    # -----------------------------------------------------

    try:
        amount_val = Decimal(
            amount.strip()
        )
    except (
        InvalidOperation,
        ValueError,
    ):
        return redirect_admin(
            "/admin/withdraw",
            "Invalid withdrawal amount.",
            error=True,
        )

    if amount_val <= 0:
        return redirect_admin(
            "/admin/withdraw",
            "Amount must be greater than zero.",
            error=True,
        )

    # -----------------------------------------------------
    # PHONE
    # -----------------------------------------------------

    phone_number = (
        phone_number.strip()
    )

    if not phone_number:
        return redirect_admin(
            "/admin/withdraw",
            "M-Pesa number is required.",
            error=True,
        )

    # -----------------------------------------------------
    # AVAILABLE BALANCE
    # -----------------------------------------------------

    (
        platform_revenue,
        already_withdrawn,
        pending_admin,
        available,
        _balance,
    ) = get_admin_withdrawal_financials(db)

    if amount_val > available:
        return redirect_admin(
            "/admin/withdraw",
            "Withdrawal exceeds available BeatHub balance.",
            error=True,
        )

    # -----------------------------------------------------
    # NOTE
    # -----------------------------------------------------

    final_note = (
        admin_note.strip()
        or note.strip()
        or None
    )

    # -----------------------------------------------------
    # CREATE WITHDRAWAL
    # -----------------------------------------------------

    withdrawal = AdminWithdrawal(
        amount=amount_val,
        phone_number=phone_number,
        status="pending",
        admin_note=final_note,
    )

    db.add(withdrawal)
    db.commit()

    return redirect_admin(
        "/admin/withdraw",
        "Withdrawal request created.",
    )


# =========================================================
# APPROVE BEATHUB / ADMIN WITHDRAWAL
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
        return redirect_admin(
            "/admin/withdraw",
            "Only pending admin withdrawals can be approved.",
            error=True,
        )

    withdrawal.status = "approved"

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_admin(
        "/admin/withdraw",
        "Admin withdrawal approved.",
    )


# =========================================================
# REJECT BEATHUB / ADMIN WITHDRAWAL
# =========================================================

@router.post(
    "/withdraw/{withdrawal_id}/reject"
)
def reject_admin_withdrawal(
    withdrawal_id: str,
    note: str = Form(""),
    admin_note: str = Form(""),
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

    final_note = (
        admin_note.strip()
        or note.strip()
        or "Admin withdrawal rejected."
    )

    withdrawal.status = "rejected"

    withdrawal.admin_note = final_note

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    withdrawal.resolved_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_admin(
        "/admin/withdraw",
        "Admin withdrawal rejected.",
    )


# =========================================================
# MARK BEATHUB / ADMIN WITHDRAWAL PAID
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

    if withdrawal.status not in (
        "approved",
        "processing",
        "paid",
    ):
        return redirect_admin(
            "/admin/withdraw",
            "Only approved or processing withdrawals can be marked paid.",
            error=True,
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

    return redirect_admin(
        "/admin/withdraw",
        "Admin withdrawal marked as paid.",
    )


# =========================================================
# OPTIONAL: START ADMIN WITHDRAWAL PROCESSING
# =========================================================

@router.post(
    "/withdraw/{withdrawal_id}/processing"
)
def mark_admin_withdrawal_processing(
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

    if withdrawal.status != "approved":
        return redirect_admin(
            "/admin/withdraw",
            "Only approved withdrawals can be moved to processing.",
            error=True,
        )

    withdrawal.status = "processing"

    withdrawal.updated_at = (
        datetime.utcnow()
    )

    db.commit()

    return redirect_admin(
        "/admin/withdraw",
        "Admin withdrawal moved to processing.",
    )
