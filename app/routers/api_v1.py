"""JSON API v1 for the BeatHub mobile clients.

This router deliberately reuses the existing User, Track, Order, PaymentTransaction
and order-finalization services so mobile purchases share the same accounts and
financial records as the web application.
"""
import re
import uuid
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import Track, SalesModel
from app.models.order import Order, OrderStatus, License
from app.models.payment import PaymentTransaction, PaymentStatus
from app.models.profile import Profile
from app.models.user import User, UserRole
from app.services.orders import finalize_order
from app.services.pricing import calculate_split, normalize_currency
from app.utils.deps import require_user
from app.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1", tags=["mobile-api"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class SignupIn(BaseModel):
    stage_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(default="buyer", pattern="^(buyer|creator|artist)$")


class PaymentIn(BaseModel):
    slug: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = None


def _role(user: User) -> str:
    return str(getattr(getattr(user, "role", None), "value", getattr(user, "role", "buyer")))


def _user_payload(user: User) -> dict:
    profile = getattr(user, "profile", None)
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "role": _role(user),
        "verified": bool(user.is_verified),
        "stage_name": getattr(profile, "stage_name", None),
        "slug": getattr(profile, "slug", None),
    }


def _track_payload(track: Track) -> dict:
    profile = getattr(track, "creator_profile", None)
    sales = getattr(getattr(track, "sales_model", None), "value", track.sales_model)
    return {
        "id": track.id,
        "title": track.title,
        "slug": track.slug,
        "description": track.description,
        "genre": track.genre,
        "bpm": track.bpm,
        "price": float(track.price),
        "currency": track.currency,
        "sales_model": str(sales),
        "is_sold": bool(track.is_sold),
        "artwork_url": track.cover_art_url,
        "preview_url": f"/track/{track.slug}/preview",
        "track_url": f"/track/{track.slug}",
        "producer": getattr(profile, "stage_name", None),
        "producer_slug": getattr(profile, "slug", None),
    }


def _available(track: Track) -> bool:
    if not track or not track.is_published or Decimal(str(track.price)) <= 0:
        return False
    sales = getattr(getattr(track, "sales_model", None), "value", track.sales_model)
    return str(sales) != SalesModel.EXCLUSIVE.value or not track.is_sold


@router.get("/health")
def api_health():
    return {"status": "ok", "api": "v1"}


@router.post("/auth/login")
def api_login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email.ilike(payload.email.strip())).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is inactive.")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Please verify your email before signing in.", headers={"X-BeatHub-Verification": "required"})
    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer", "user": _user_payload(user)}


@router.post("/auth/signup", status_code=201)
def api_signup(payload: SignupIn, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if db.query(User).filter(User.email.ilike(email)).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    role_name = "creator" if payload.role in {"creator", "artist"} else "buyer"
    username_base = re.sub(r"[^a-zA-Z0-9]", "", payload.stage_name.lower())[:90] or f"user{uuid.uuid4().hex[:8]}"
    username, suffix = username_base, 2
    while db.query(User).filter(User.username.ilike(username)).first():
        username = f"{username_base}{suffix}"; suffix += 1
    user = User(id=str(uuid.uuid4()), email=email, username=username, hashed_password=hash_password(payload.password), role=UserRole.CREATOR if role_name == "creator" else UserRole.BUYER, is_active=True, is_verified=False)
    profile = Profile(id=str(uuid.uuid4()), user_id=user.id, stage_name=payload.stage_name.strip(), slug=f"{username}-{uuid.uuid4().hex[:5]}", is_producer=role_name == "creator", is_artist=payload.role == "artist")
    db.add(user); db.add(profile); db.commit()
    return {"message": "Account created. Verify your email before signing in.", "user": _user_payload(user), "verification_required": True}


@router.get("/me")
def api_me(user: User = Depends(require_user)):
    return {"user": _user_payload(user)}


@router.get("/catalog")
def api_catalog(q: str = "", genre: str = "", page: int = 1, limit: int = 20, db: Session = Depends(get_db)):
    page = max(1, page); limit = min(50, max(1, limit))
    query = db.query(Track).filter(Track.is_published.is_(True))
    if q.strip():
        term = f"%{q.strip()}%"; query = query.filter(or_(Track.title.ilike(term), Track.genre.ilike(term), Track.tags.ilike(term)))
    if genre.strip(): query = query.filter(Track.genre.ilike(genre.strip()))
    total = query.count(); tracks = query.order_by(Track.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"items": [_track_payload(t) for t in tracks if _available(t)], "page": page, "limit": limit, "total": total}


@router.get("/catalog/{slug}")
def api_track(slug: str, db: Session = Depends(get_db)):
    track = db.query(Track).filter(Track.slug == slug, Track.is_published.is_(True)).first()
    if not track: raise HTTPException(status_code=404, detail="Track not found.")
    return _track_payload(track)


@router.get("/orders")
def api_orders(db: Session = Depends(get_db), user: User = Depends(require_user)):
    orders = db.query(Order).filter(Order.buyer_id == user.id).order_by(Order.created_at.desc()).all()
    return {"items": [{"id": o.id, "order_number": o.order_number, "status": getattr(o.status, "value", o.status), "amount": float(o.gross_amount), "currency": o.currency, "track_slug": getattr(o.track, "slug", None), "track_title": getattr(o.track, "title", None), "created_at": o.created_at.isoformat() if o.created_at else None, "completed_at": o.completed_at.isoformat() if o.completed_at else None} for o in orders]}


@router.get("/orders/{order_id}")
def api_order(order_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    order = db.get(Order, order_id)
    if not order or order.buyer_id != user.id: raise HTTPException(status_code=404, detail="Order not found.")
    license_row = db.query(License).filter(License.order_id == order.id).first()
    return {"id": order.id, "order_number": order.order_number, "status": getattr(order.status, "value", order.status), "amount": float(order.gross_amount), "currency": order.currency, "track": _track_payload(order.track) if order.track else None, "license_id": getattr(license_row, "id", None), "download_available": order.status == OrderStatus.COMPLETED and license_row is not None}


@router.post("/payments/paystack/initialize")
async def api_paystack_initialize(payload: PaymentIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not settings.PAYSTACK_SECRET_KEY: raise HTTPException(status_code=503, detail="Paystack is not configured.")
    track = db.query(Track).filter(Track.slug == payload.slug).first()
    if not track: raise HTTPException(status_code=404, detail="Track not found.")
    if not _available(track): raise HTTPException(status_code=409, detail="This track is no longer available for purchase.")
    if getattr(getattr(track, "creator_profile", None), "user_id", None) == user.id: raise HTTPException(status_code=400, detail="You cannot purchase your own track.")
    currency = normalize_currency(track.currency); price = Decimal(str(track.price)); split = calculate_split(price)
    order = Order(id=str(uuid.uuid4()), order_number=f"BH{uuid.uuid4().hex[:10].upper()}", buyer_id=user.id, track_id=track.id, sales_model_at_purchase=str(getattr(getattr(track, "sales_model", None), "value", track.sales_model)), gross_amount=price, currency=currency, commission_amount=Decimal(str(split["commission_amount"])), net_amount=Decimal(str(split["net_amount"])), commission_percent_at_purchase=Decimal(str(split["commission_percent"])), status=OrderStatus.PENDING, phone_number="paystack")
    db.add(order); db.flush()
    callback_url = f"{settings.BASE_URL.rstrip('/')}/paystack/callback"
    paystack_payload = {"email": (payload.email or user.email), "amount": int(price * 100), "currency": currency, "reference": order.order_number, "callback_url": callback_url, "channels": ["card", "mobile_money"], "metadata": {"beathub_order_id": order.id, "beathub_track_slug": track.slug, "buyer_id": user.id}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/initialize", headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}, json=paystack_payload)
            data = response.json()
    except Exception:
        db.rollback(); raise HTTPException(status_code=502, detail="Paystack could not be reached. Please try again.")
    if response.status_code >= 400 or not data.get("status"):
        db.rollback(); raise HTTPException(status_code=400, detail=data.get("message") or "Paystack initialization failed.")
    reference = data.get("data", {}).get("reference") or order.order_number; authorization_url = data.get("data", {}).get("authorization_url")
    if not authorization_url: db.rollback(); raise HTTPException(status_code=502, detail="Paystack did not return a checkout URL.")
    db.add(PaymentTransaction(order_id=order.id, checkout_request_id=reference, phone_number="paystack", amount=price, currency=currency, status=PaymentStatus.PENDING, result_description="Mobile Paystack checkout initialized.")); db.commit()
    return {"order_id": order.id, "order_number": order.order_number, "reference": reference, "authorization_url": authorization_url, "amount": float(price), "currency": currency}
