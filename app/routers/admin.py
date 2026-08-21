from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import WithdrawalRequest, WithdrawalStatus
from app.models.music import Album, Track
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.profile import Profile
from app.models.user import User, UserRole
from app.utils.deps import (
    SESSION_COOKIE_NAME,
    require_admin,
)
from app.utils.security import (
    create_access_token,
    verify_password,
)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

COOKIE_MAX_AGE = 60 * 60 * 24 * 7


def ctx(request: Request, current_user=None, **extra):
    base = {
        "request": request,
        "current_user": current_user,
        "current_year": datetime.utcnow().year,
    }
    base.update(extra)
    return base


# ----------------------------------------------------------------------
# ADMIN LOGIN
# ----------------------------------------------------------------------

@router.get("/login")
def admin_login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        ctx(request),
    )


@router.post("/login")
def admin_login_submit(
    request: Request,
    db: Session = Depends(get_db),
    identifier: str = Form(...),
    password: str = Form(...),
):
    identifier = (identifier or "").strip().lower()

    admin = (
        db.query(User)
        .filter(User.email == identifier)
        .first()
    )

    # Never reveal whether the email exists.
    if not admin:
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            ctx(
                request,
                error="Invalid administrator credentials.",
            ),
            status_code=401,
        )

    if not admin.is_active:
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            ctx(
                request,
                error="This administrator account is inactive.",
            ),
            status_code=403,
        )

    if not verify_password(
        password,
        admin.hashed_password,
    ):
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            ctx(
                request,
                error="Invalid administrator credentials.",
            ),
            status_code=401,
        )

    role = getattr(admin.role, "value", admin.role)

    if str(role).strip().lower() != "admin":
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            ctx(
                request,
                error="Administrator access denied.",
            ),
            status_code=403,
        )

    token = create_access_token(
        subject=admin.id,
        extra_claims={"role": "admin"},
    )

    response = RedirectResponse(
        url="/admin",
        status_code=303,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=False,
        path="/",
    )

    return response


# ----------------------------------------------------------------------
# ADMIN DASHBOARD
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
        .filter(Order.status == OrderStatus.COMPLETED)
        .scalar()
    )

    total_commission = (
        db.query(
            func.coalesce(
                func.sum(Order.commission_amount),
                0,
            )
        )
        .filter(Order.status == OrderStatus.COMPLETED)
        .scalar()
    )

    total_creator_earnings = (
        db.query(
            func.coalesce(
                func.sum(Order.net_amount),
                0,
            )
        )
        .filter(Order.status == OrderStatus.COMPLETED)
        .scalar()
    )

    successful = (
        db.query(Order)
        .filter(Order.status == OrderStatus.COMPLETED)
        .count()
    )

    pending = (
        db.query(Order)
        .filter(Order.status == OrderStatus.PENDING)
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
        .order_by(PaymentTransaction.updated_at.desc())
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


@router.post("/content/track/{track_id}/toggle-published")
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
# WITHDRAWALS
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


@router.post("/withdrawals/{withdrawal_id}/update")
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

    if payout_reference:
        wr.payout_reference = payout_reference

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
