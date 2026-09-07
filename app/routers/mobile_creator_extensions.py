"""Additional mobile creator endpoints registered on the v1 API router."""
from decimal import Decimal

from fastapi import Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.models.notification import Notification
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.routers.api_v1 import _creator_required
from app.utils.deps import require_user

# Importing the existing router lets these endpoints ship with the already
# registered /api/v1 router without duplicating router registration in main.py.
from app.routers.api_v1 import router


@router.get("/creator/sales")
def mobile_creator_sales(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")
    orders = (
        db.query(Order)
        .join(Order.track)
        .filter(Order.status == OrderStatus.COMPLETED, Order.track.has(creator_profile_id=profile.id))
        .order_by(Order.completed_at.desc(), Order.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "id": order.id,
                "order_number": order.order_number,
                "track_title": getattr(order.track, "title", None),
                "track_slug": getattr(order.track, "slug", None),
                "gross_amount": float(order.gross_amount),
                "commission_amount": float(order.commission_amount),
                "net_amount": float(order.net_amount),
                "currency": str(order.currency).upper(),
                "completed_at": order.completed_at.isoformat() if order.completed_at else None,
            }
            for order in orders
        ]
    }


@router.get("/creator/financial-summary")
def mobile_creator_financial_summary(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Return creator earnings split by currency.

    Orders carry their transaction currency, while creator withdrawals are
    currently M-Pesa/KES. Never combine KES and USD into one numeric balance.
    """
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")

    rows = (
        db.query(
            Order.currency,
            func.coalesce(func.sum(Order.gross_amount), 0),
            func.coalesce(func.sum(Order.commission_amount), 0),
            func.coalesce(func.sum(Order.net_amount), 0),
            func.count(Order.id),
        )
        .join(Order.track)
        .filter(
            Order.status == OrderStatus.COMPLETED,
            Order.track.has(creator_profile_id=profile.id),
        )
        .group_by(Order.currency)
        .all()
    )

    withdrawn_kes = db.query(
        func.coalesce(func.sum(WithdrawalRequest.amount), 0)
    ).filter(
        WithdrawalRequest.creator_profile_id == profile.id,
        WithdrawalRequest.status.in_(["approved", "processing", "paid"]),
    ).scalar()

    pending_kes = db.query(
        func.coalesce(func.sum(WithdrawalRequest.amount), 0)
    ).filter(
        WithdrawalRequest.creator_profile_id == profile.id,
        WithdrawalRequest.status == "pending",
    ).scalar()

    withdrawn_kes = Decimal(str(withdrawn_kes or 0))
    pending_kes = Decimal(str(pending_kes or 0))

    currencies = {}
    for currency, gross, commission, net, sales in rows:
        code = str(currency or "KES").upper()
        gross_d = Decimal(str(gross or 0))
        commission_d = Decimal(str(commission or 0))
        net_d = Decimal(str(net or 0))
        available_d = net_d - withdrawn_kes - pending_kes if code == "KES" else net_d
        if available_d < 0:
            available_d = Decimal("0")
        currencies[code] = {
            "sales": int(sales or 0),
            "gross": float(gross_d),
            "commission": float(commission_d),
            "net": float(net_d),
            "available": float(available_d),
            "pending_withdrawal": float(pending_kes) if code == "KES" else 0.0,
        }

    for code in ("KES", "USD"):
        currencies.setdefault(code, {
            "sales": 0,
            "gross": 0.0,
            "commission": 0.0,
            "net": 0.0,
            "available": 0.0,
            "pending_withdrawal": 0.0,
        })

    return {"currencies": currencies}


@router.get("/creator/withdrawals")
def mobile_creator_withdrawals(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")
    requests = (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.creator_profile_id == profile.id)
        .order_by(WithdrawalRequest.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "id": request.id,
                "amount": float(request.amount),
                "currency": "KES",
                "phone_number": request.phone_number,
                "status": str(request.status),
                "admin_note": request.admin_note,
                "payout_reference": request.payout_reference,
                "created_at": request.created_at.isoformat() if request.created_at else None,
                "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
            }
            for request in requests
        ]
    }


@router.get("/notifications")
def mobile_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    items = (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {
                "id": item.id,
                "type": item.type,
                "title": item.title,
                "message": item.message,
                "link": item.link,
                "is_read": bool(item.is_read),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ],
        "unread_count": sum(1 for item in items if not item.is_read),
    }


@router.post("/notifications/{notification_id}/read")
def mobile_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    item = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if not item:
        raise HTTPException(404, "Notification not found.")
    item.is_read = True
    db.commit()
    return {"id": item.id, "is_read": True}
