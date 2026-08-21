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
    WithdrawalRequest,
)
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.models.music import Track
from app.utils.deps import require_admin


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)

templates = Jinja2Templates(
    directory="app/templates"
)


# =========================================================
# COMMON ADMIN TEMPLATE CONTEXT
# =========================================================

def admin_ctx(
    request: Request,
    current_user,
    **extra,
):
    data = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,

        # Basic statistics
        "users_count": 0,
        "tracks_count": 0,
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

        # Lists
        "recent_orders": [],
        "recent_users": [],
        "failed_payments": [],
        "admin_withdrawals": [],
        "withdrawals": [],
        "sales": [],
        "orders": [],
    }

    data.update(extra)

    return data


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
    # -----------------------------------------------------
    # BASIC COUNTS
    # -----------------------------------------------------

    users_count = db.query(User).count()

    tracks_count = db.query(Track).count()

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

    failed_statuses = []

    if hasattr(OrderStatus, "FAILED"):
        failed_statuses.append(OrderStatus.FAILED)

    if hasattr(OrderStatus, "REJECTED"):
        failed_statuses.append(OrderStatus.REJECTED)

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
    # TOTAL SALES VOLUME
    #
    # IMPORTANT:
    # Order.total_amount DOES NOT EXIST.
    # The current Order model uses gross_amount.
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

    total_sales_volume = Decimal(
        str(total_sales_volume_raw or 0)
    )

    # -----------------------------------------------------
    # TOTAL COMMISSION
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

    total_commission = Decimal(
        str(total_commission_raw or 0)
    )

    # -----------------------------------------------------
    # TOTAL CREATOR EARNINGS
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

    total_creator_earnings = Decimal(
        str(total_creator_earnings_raw or 0)
    )

    # Platform revenue = BeatHub commission
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

    pending_withdrawals = pending_creator_withdrawals

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
                Order.status.in_(failed_statuses)
            )
            .order_by(
                Order.created_at.desc()
            )
            .limit(10)
            .all()
        )

    # -----------------------------------------------------
    # RECENT ADMIN WITHDRAWALS
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
            completed_sales=completed_sales,

            total_sales_volume=total_sales_volume,
            total_commission=total_commission,
            total_creator_earnings=total_creator_earnings,
            platform_revenue=platform_revenue,

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

            admin_withdrawals=admin_withdrawals,
        ),
    )


# =========================================================
# ADMIN SALES
# =========================================================

@router.get("/sales")
def admin_sales(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # -----------------------------------------------------
    # COMPLETED SALES
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # TOTAL SALES
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

    total_sales_volume = Decimal(
        str(total_sales_volume_raw or 0)
    )

    # -----------------------------------------------------
    # TOTAL COMMISSION
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

    total_commission = Decimal(
        str(total_commission_raw or 0)
    )

    # -----------------------------------------------------
    # TOTAL CREATOR EARNINGS
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

    total_creator_earnings = Decimal(
        str(total_creator_earnings_raw or 0)
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

    return templates.TemplateResponse(
        request,
        "admin/withdrawals.html",
        admin_ctx(
            request,
            user,
            withdrawals=withdrawals,
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
            WithdrawalRequest.id == withdrawal_id
        )
        .first()
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Withdrawal request not found.",
        )

    if withdrawal.status != "pending":
        return RedirectResponse(
            url=(
                "/admin/withdrawals?"
                "error=Only%20pending%20withdrawals%20can%20be%20approved."
            ),
            status_code=303,
        )

    withdrawal.status = "approved"

    withdrawal.updated_at = datetime.utcnow()

    withdrawal.admin_note = (
        "Withdrawal approved by administrator."
    )

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdrawals?"
            "success=Creator%20withdrawal%20approved."
        ),
        status_code=303,
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
            WithdrawalRequest.id == withdrawal_id
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

    withdrawal.updated_at = datetime.utcnow()
    withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdrawals?"
            "success=Creator%20withdrawal%20rejected."
        ),
        status_code=303,
    )


# =========================================================
# ADMIN / BEATHUB WITHDRAWAL PAGE
# =========================================================

@router.get("/withdraw")
def admin_withdraw_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # -----------------------------------------------------
    # PLATFORM REVENUE
    # -----------------------------------------------------

    platform_revenue_raw = (
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

    platform_revenue = Decimal(
        str(platform_revenue_raw or 0)
    )

    # -----------------------------------------------------
    # ALREADY WITHDRAWN
    # -----------------------------------------------------

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

    already_withdrawn = Decimal(
        str(already_withdrawn_raw or 0)
    )

    # -----------------------------------------------------
    # PENDING ADMIN WITHDRAWALS
    # -----------------------------------------------------

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

    pending_admin = Decimal(
        str(pending_admin_raw or 0)
    )

    # -----------------------------------------------------
    # AVAILABLE BALANCE
    # -----------------------------------------------------

    available = (
        platform_revenue
        - already_withdrawn
        - pending_admin
    )

    if available < 0:
        available = Decimal("0")

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

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
    # -----------------------------------------------------
    # AMOUNT
    # -----------------------------------------------------

    try:
        amount_val = Decimal(
            amount.strip()
        )
    except (InvalidOperation, ValueError):
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=Invalid%20withdrawal%20amount."
            ),
            status_code=303,
        )

    if amount_val <= 0:
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=Amount%20must%20be%20greater%20than%20zero."
            ),
            status_code=303,
        )

    # -----------------------------------------------------
    # PHONE
    # -----------------------------------------------------

    phone_number = phone_number.strip()

    if not phone_number:
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=M-Pesa%20number%20is%20required."
            ),
            status_code=303,
        )

    # -----------------------------------------------------
    # PLATFORM REVENUE
    # -----------------------------------------------------

    platform_revenue_raw = (
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

    platform_revenue = Decimal(
        str(platform_revenue_raw or 0)
    )

    # -----------------------------------------------------
    # ALREADY WITHDRAWN
    # -----------------------------------------------------

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

    already_withdrawn = Decimal(
        str(already_withdrawn_raw or 0)
    )

    # -----------------------------------------------------
    # PENDING
    # -----------------------------------------------------

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

    pending_admin = Decimal(
        str(pending_admin_raw or 0)
    )

    # -----------------------------------------------------
    # AVAILABLE
    # -----------------------------------------------------

    available = (
        platform_revenue
        - already_withdrawn
        - pending_admin
    )

    if available < 0:
        available = Decimal("0")

    if amount_val > available:
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=Withdrawal%20exceeds%20available%20BeatHub%20balance."
            ),
            status_code=303,
        )

    # -----------------------------------------------------
    # CREATE
    # -----------------------------------------------------

    withdrawal = AdminWithdrawal(
        amount=amount_val,
        phone_number=phone_number,
        status="pending",
        admin_note=note.strip() or None,
    )

    db.add(withdrawal)
    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdraw?"
            "success=Withdrawal%20request%20created."
        ),
        status_code=303,
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
            AdminWithdrawal.id == withdrawal_id
        )
        .first()
    )

    if not withdrawal:
        raise HTTPException(
            status_code=404,
            detail="Admin withdrawal not found.",
        )

    if withdrawal.status != "pending":
        return RedirectResponse(
            url=(
                "/admin/withdraw?"
                "error=Only%20pending%20withdrawals%20can%20be%20approved."
            ),
            status_code=303,
        )

    withdrawal.status = "approved"
    withdrawal.updated_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdraw?"
            "success=Admin%20withdrawal%20approved."
        ),
        status_code=303,
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
            AdminWithdrawal.id == withdrawal_id
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

    withdrawal.updated_at = datetime.utcnow()
    withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdraw?"
            "success=Admin%20withdrawal%20rejected."
        ),
        status_code=303,
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
            AdminWithdrawal.id == withdrawal_id
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

    withdrawal.updated_at = datetime.utcnow()
    withdrawal.resolved_at = datetime.utcnow()

    db.commit()

    return RedirectResponse(
        url=(
            "/admin/withdraw?"
            "success=Admin%20withdrawal%20marked%20as%20paid."
        ),
        status_code=303,
    )
