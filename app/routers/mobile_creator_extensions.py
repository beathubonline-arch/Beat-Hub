"""Additional mobile creator endpoints registered on the v1 API router."""
from decimal import Decimal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.models.notification import Notification
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.routers.api_v1 import _creator_required
from app.utils.deps import require_user
from app.routers.api_v1 import router


class CreatorProfileUpdateIn(BaseModel):
    stage_name: str | None = Field(default=None, min_length=1, max_length=120)
    bio: str | None = Field(default=None, max_length=5000)
    instagram_url: HttpUrl | None = None
    twitter_url: HttpUrl | None = None
    youtube_url: HttpUrl | None = None
    website_url: HttpUrl | None = None


def _profile_payload(profile):
    base_url = str(getattr(settings, "BASE_URL", "")).rstrip("/")
    return {
        "stage_name": profile.stage_name,
        "slug": profile.slug,
        "bio": profile.bio,
        "avatar_url": f"{base_url}/profile/{profile.slug}/avatar" if profile.avatar_path and base_url else None,
        "instagram_url": profile.instagram_url,
        "twitter_url": profile.twitter_url,
        "youtube_url": profile.youtube_url,
        "website_url": profile.website_url,
        "store_url": f"{base_url}/creator/{profile.slug}" if base_url else f"/creator/{profile.slug}",
        "is_producer": bool(profile.is_producer),
        "is_artist": bool(profile.is_artist),
        "is_dj": bool(profile.is_dj),
    }


@router.get("/creator/profile")
def mobile_creator_profile(db: Session = Depends(get_db), user: User = Depends(require_user)):
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")
    return _profile_payload(profile)


@router.patch("/creator/profile")
def mobile_creator_profile_update(payload: CreatorProfileUpdateIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")
    if payload.stage_name is not None:
        stage_name = payload.stage_name.strip()
        if not stage_name:
            raise HTTPException(400, "Stage name cannot be empty.")
        profile.stage_name = stage_name
    if payload.bio is not None:
        profile.bio = payload.bio.strip() or None
    for field in ("instagram_url", "twitter_url", "youtube_url", "website_url"):
        value = getattr(payload, field)
        setattr(profile, field, str(value) if value else None)
    db.commit()
    db.refresh(profile)
    return {"message": "Creator profile updated.", "profile": _profile_payload(profile)}


@router.get("/creator/sales")
def mobile_creator_sales(db: Session = Depends(get_db), user: User = Depends(require_user)):
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")
    orders = (db.query(Order).join(Order.track)
              .filter(Order.status == OrderStatus.COMPLETED, Order.track.has(creator_profile_id=profile.id))
              .order_by(Order.completed_at.desc(), Order.created_at.desc()).limit(100).all())
    return {"items": [{
        "id": o.id, "order_number": o.order_number, "track_title": getattr(o.track, "title", None),
        "track_slug": getattr(o.track, "slug", None), "gross_amount": float(o.gross_amount),
        "commission_amount": float(o.commission_amount), "net_amount": float(o.net_amount),
        "currency": str(o.currency).upper(), "completed_at": o.completed_at.isoformat() if o.completed_at else None,
    } for o in orders]}


@router.get("/creator/financial-summary")
def mobile_creator_financial_summary(db: Session = Depends(get_db), user: User = Depends(require_user)):
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")
    rows = (db.query(Order.currency, func.coalesce(func.sum(Order.gross_amount), 0),
                     func.coalesce(func.sum(Order.commission_amount), 0), func.coalesce(func.sum(Order.net_amount), 0),
                     func.count(Order.id)).join(Order.track)
            .filter(Order.status == OrderStatus.COMPLETED, Order.track.has(creator_profile_id=profile.id))
            .group_by(Order.currency).all())
    withdrawn_kes = db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).filter(
        WithdrawalRequest.creator_profile_id == profile.id,
        WithdrawalRequest.status.in_(["approved", "processing", "paid"])).scalar()
    pending_kes = db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).filter(
        WithdrawalRequest.creator_profile_id == profile.id, WithdrawalRequest.status == "pending").scalar()
    withdrawn_kes, pending_kes = Decimal(str(withdrawn_kes or 0)), Decimal(str(pending_kes or 0))
    currencies = {}
    for currency, gross, commission, net, sales in rows:
        code = str(currency or "KES").upper()
        gross_d, commission_d, net_d = Decimal(str(gross or 0)), Decimal(str(commission or 0)), Decimal(str(net or 0))
        available_d = net_d - withdrawn_kes - pending_kes if code == "KES" else net_d
        currencies[code] = {"sales": int(sales or 0), "gross": float(gross_d), "commission": float(commission_d),
                            "net": float(net_d), "available": float(max(available_d, Decimal("0"))),
                            "pending_withdrawal": float(pending_kes) if code == "KES" else 0.0}
    for code in ("KES", "USD"):
        currencies.setdefault(code, {"sales": 0, "gross": 0.0, "commission": 0.0, "net": 0.0, "available": 0.0, "pending_withdrawal": 0.0})
    return {"currencies": currencies}


@router.get("/creator/withdrawals")
def mobile_creator_withdrawals(db: Session = Depends(get_db), user: User = Depends(require_user)):
    _creator_required(user)
    profile = getattr(user, "profile", None)
    if not profile:
        raise HTTPException(400, "Creator profile missing.")
    requests = db.query(WithdrawalRequest).filter(WithdrawalRequest.creator_profile_id == profile.id).order_by(WithdrawalRequest.created_at.desc()).limit(100).all()
    return {"items": [{"id": r.id, "amount": float(r.amount), "currency": "KES", "phone_number": r.phone_number,
                       "status": str(r.status), "admin_note": r.admin_note, "payout_reference": r.payout_reference,
                       "created_at": r.created_at.isoformat() if r.created_at else None,
                       "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None} for r in requests]}


@router.get("/notifications")
def mobile_notifications(db: Session = Depends(get_db), user: User = Depends(require_user)):
    items = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(100).all()
    return {"items": [{"id": i.id, "type": i.type, "title": i.title, "message": i.message, "link": i.link,
                       "is_read": bool(i.is_read), "created_at": i.created_at.isoformat() if i.created_at else None} for i in items],
            "unread_count": sum(1 for i in items if not i.is_read)}


@router.post("/notifications/{notification_id}/read")
def mobile_notification_read(notification_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if not item:
        raise HTTPException(404, "Notification not found.")
    item.is_read = True
    db.commit()
    return {"id": item.id, "is_read": True}
