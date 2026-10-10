"""Canonical Paystack checkout for BeatHub music products.

The server owns the order amount and currency. Paystack/webhook confirmation
must match both before a purchase is fulfilled.
"""
import hashlib
import hmac
import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.music import Album, SalesModel, Track
from app.models.user import User, UserRole
from app.utils.security import hash_password
from app.utils.deps import get_optional_user
from app.services.guest_album_access import COOKIE_NAME, MAX_AGE_SECONDS, issue_guest_token
from app.services.merchandise_payments import complete_merchandise_payment, find_merchandise_order_id
from app.services.orders import finalize_order
from app.services.pricing import BEATHUB_COMMISSION_PERCENT, calculate_split, normalize_currency
from app.utils.deps import require_user

router = APIRouter(tags=["paystack"])
logger = logging.getLogger("beathub.paystack")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PAYSTACK_MINIMUMS = {"KES": Decimal("3.00"), "USD": Decimal("2.00")}


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}


def _amount_subunit(amount: Decimal) -> int:
    return int((Decimal(amount) * Decimal("100")).quantize(Decimal("1")))


def _available(track: Track) -> bool:
    if not bool(getattr(track, "is_published", False)):
        return False
    try:
        price = Decimal(str(getattr(track, "price", 0)))
    except Exception:
        return False
    if price <= 0:
        return False
    sales_model = getattr(track, "sales_model", None)
    sales_value = getattr(sales_model, "value", sales_model)
    if str(sales_value).lower() == SalesModel.EXCLUSIVE.value:
        return not bool(getattr(track, "is_sold", False))
    return True


def _verified_currency(data: dict, expected: str) -> bool:
    return str(data.get("currency") or "").strip().upper() == expected


def _paystack_channels(currency: str) -> list[str]:
    """Return channels compatible with the transaction currency."""
    return ["card", "mobile_money"] if currency == "KES" else ["card"]


def _paystack_rejection_message(data: dict, currency: str) -> str:
    message = str(data.get("message") or "").strip()
    if message.lower() == "currency not supported by merchant":
        if currency == "USD":
            return (
                "USD checkout is not enabled on this Paystack merchant account. "
                "Enable USD/international payments in Paystack before accepting USD prices."
            )
        return f"{currency} checkout is not enabled on this Paystack merchant account."
    return message or "Paystack could not initialize checkout."


async def _verify_reference(reference: str) -> dict:
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not configured.")
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/verify/{reference}", headers=_headers())
    response.raise_for_status()
    payload = response.json()
    if not payload.get("status") or not isinstance(payload.get("data"), dict):
        raise RuntimeError(payload.get("message") or "Paystack verification failed.")
    return payload["data"]


def _complete_verified_payment(db: Session, order: Order, payment: PaymentTransaction, data: dict) -> bool:
    locked_payment = db.query(PaymentTransaction).filter(PaymentTransaction.id == payment.id).with_for_update().one_or_none()
    locked_order = db.query(Order).filter(Order.id == order.id).with_for_update().one_or_none()
    if locked_payment is None or locked_order is None:
        raise RuntimeError("Payment or order disappeared during verification.")
    payment, order = locked_payment, locked_order

    if payment.callback_processed and payment.status == PaymentStatus.COMPLETED and order.status == OrderStatus.COMPLETED:
        return True
    if payment.status == PaymentStatus.COMPLETED and order.status == OrderStatus.COMPLETED:
        return True

    # A verified transaction must belong to this exact order/reference.
    if str(data.get("reference") or "") != str(payment.checkout_request_id):
        raise RuntimeError("Paystack transaction reference mismatch.")
    status = str(data.get("status", "")).lower()
    if status != "success":
        payment.status = PaymentStatus.FAILED
        payment.result_description = f"Paystack transaction status: {status or 'unknown'}"
        payment.callback_processed = True
        order.status = OrderStatus.FAILED
        db.commit()
        return False

    expected_amount = _amount_subunit(Decimal(str(order.gross_amount)))
    actual_amount = int(data.get("amount") or 0)
    expected_currency = normalize_currency(order.currency)
    if actual_amount != expected_amount or not _verified_currency(data, expected_currency):
        payment.status = PaymentStatus.FAILED
        payment.result_description = "Paystack verification amount or currency mismatch."
        payment.callback_processed = True
        order.status = OrderStatus.FAILED
        db.commit()
        return False

    payment.status = PaymentStatus.COMPLETED
    payment.result_code = 0
    payment.result_description = "Paystack payment verified successfully."
    payment.completed_at = datetime.utcnow()
    payment.callback_processed = True
    customer = data.get("customer") or {}
    if customer.get("phone"):
        payment.phone_number = str(customer["phone"])[:20]
    result = finalize_order(db, order)
    return result.status == OrderStatus.COMPLETED


@router.post("/paystack/checkout/track/{slug}")
async def paystack_checkout(
    slug: str,
    request: Request,
    email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not settings.PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Paystack is not configured yet.")

    track = db.query(Track).filter(Track.slug == slug).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")
    if not _available(track):
        return RedirectResponse(f"/track/{slug}?error=This%20track%20is%20no%20longer%20available%20for%20purchase.", 303)

    profile = getattr(track, "creator_profile", None)
    if getattr(profile, "user_id", None) == user.id:
        return RedirectResponse(f"/track/{slug}?error=You%20cannot%20purchase%20your%20own%20track.", 303)

    price = Decimal(str(track.price))
    try:
        currency = normalize_currency(getattr(track, "currency", "KES"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if price < PAYSTACK_MINIMUMS[currency]:
        symbol = "KSh" if currency == "KES" else "$"
        return RedirectResponse(f"/checkout/track/{slug}?error=Paystack%20requires%20a%20minimum%20payment%20of%20{symbol}%20{PAYSTACK_MINIMUMS[currency]:.2f}.", 303)

    customer_email = (email or "").strip().lower() or (getattr(user, "email", "") or "").strip().lower()
    if not EMAIL_RE.fullmatch(customer_email):
        return RedirectResponse(f"/checkout/track/{slug}?error=Please%20enter%20a%20valid%20email%20address%20for%20Paystack%20checkout.", 303)

    split = calculate_split(price)
    if Decimal(str(split["commission_percent"])) != BEATHUB_COMMISSION_PERCENT:
        raise HTTPException(status_code=500, detail="Invalid BeatHub commission configuration.")

    order = Order(
        id=str(uuid.uuid4()), order_number=f"BH{uuid.uuid4().hex[:10].upper()}", buyer_id=user.id,
        track_id=track.id, album_id=None,
        sales_model_at_purchase=str(getattr(getattr(track, "sales_model", None), "value", track.sales_model)),
        gross_amount=Decimal(str(split["gross_amount"])), currency=currency,
        commission_amount=Decimal(str(split["commission_amount"])), net_amount=Decimal(str(split["net_amount"])),
        commission_percent_at_purchase=Decimal("10.00"), status=OrderStatus.PENDING, phone_number="paystack",
    )
    db.add(order)
    db.flush()

    callback_url = f"{settings.BASE_URL.rstrip('/')}/paystack/callback"
    payload = {
        "email": customer_email, "amount": _amount_subunit(price), "currency": currency,
        "reference": order.order_number, "callback_url": callback_url,
        "channels": _paystack_channels(currency),
        "metadata": {
            "beathub_order_id": order.id, "beathub_track_slug": track.slug, "buyer_id": user.id,
            "beathub_currency": currency, "beathub_commission_percent": "10.00",
            "beathub_commission_amount": str(split["commission_amount"]),
            "beathub_producer_amount": str(split["net_amount"]),
        },
    }

    producer_subaccount = getattr(profile, "paystack_subaccount_code", None)
    if producer_subaccount:
        payload["subaccount"] = str(producer_subaccount)
        payload["transaction_charge"] = _amount_subunit(split["commission_amount"])

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/initialize", headers=_headers(), json=payload)
        try:
            data = response.json()
        except ValueError:
            data = {"status": False, "message": "Paystack returned a non-JSON response."}
    except Exception:
        db.rollback()
        logger.exception("Paystack initialization failed for order %s", order.id)
        raise HTTPException(status_code=502, detail="Paystack could not be reached. Please try again.")

    if response.status_code >= 400 or not data.get("status"):
        logger.error(
            "Paystack initialization rejected: http_status=%s paystack_status=%s message=%s currency=%s amount_subunit=%s channels=%s order=%s",
            response.status_code, data.get("status"), data.get("message") or "unknown", currency,
            _amount_subunit(price), payload["channels"], order.id,
        )
        message = _paystack_rejection_message(data, currency)
        db.rollback()
        raise HTTPException(status_code=400, detail=message)

    authorization = data.get("data", {}).get("authorization_url")
    reference = data.get("data", {}).get("reference") or order.order_number
    if not authorization:
        logger.error("Paystack initialization returned no authorization URL for order %s", order.id)
        db.rollback()
        raise HTTPException(status_code=400, detail="Paystack did not return a checkout URL.")

    payment = PaymentTransaction(
        order_id=order.id, checkout_request_id=reference, phone_number="paystack",
        amount=Decimal(str(order.gross_amount)), currency=currency, status=PaymentStatus.PENDING,
        result_description=f"Paystack {currency} checkout initialized; 10% BeatHub / 90% producer split recorded internally until settlement.",
    )
    db.add(payment)
    db.commit()
    return RedirectResponse(authorization, status_code=303)



@router.post("/paystack/checkout/album/{slug}")
async def paystack_album_checkout(
    slug: str,
    email: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    # This release has an explicitly approved fixed total. Never trust a client amount.
    if slug != "time-itatell":
        raise HTTPException(status_code=404, detail="Album checkout is not configured for this release.")
    album = db.query(Album).filter(Album.slug == slug, Album.is_published.is_(True)).first()
    if not album or len(album.album_tracks) != 12:
        raise HTTPException(status_code=409, detail="Album is unavailable or its 12-track catalog is incomplete.")
    if user and album.creator_profile.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot purchase your own album.")
    if not settings.PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Paystack is not configured.")
    # Paystack requires an email; anonymous buyers use a merchant-domain technical address.
    normalized_phone = re.sub(r"[\\s-]", "", phone or "")
    if normalized_phone.startswith("0") and len(normalized_phone) == 10:
        normalized_phone = "254" + normalized_phone[1:]
    elif normalized_phone.startswith("+254"):
        normalized_phone = normalized_phone[1:]
    if not re.fullmatch(r"254[17]\\d{8}", normalized_phone):
        raise HTTPException(status_code=400, detail="Enter a valid Kenyan M-PESA number, e.g. 0712345678.")
    customer_email = (email or "").strip().lower() or (user.email if user else "")
    if customer_email and not EMAIL_RE.fullmatch(customer_email):
        raise HTTPException(status_code=400, detail="Enter a valid email or leave it blank.")
    is_guest = user is None
    if is_guest:
        guest_id = uuid.uuid4().hex
        if not customer_email:
            customer_email = f"guest-{guest_id}@mybeathub.com"
        user = User(email=f"guest-{guest_id}@mybeathub.com",
                    hashed_password=hash_password(uuid.uuid4().hex + uuid.uuid4().hex),
                    role=UserRole.BUYER, is_active=False, is_verified=False)
        db.add(user)
        db.flush()

    price, currency = Decimal("1000.00"), "KES"
    split = calculate_split(price)
    order = Order(
        id=str(uuid.uuid4()), order_number=f"BH{uuid.uuid4().hex[:10].upper()}",
        buyer_id=user.id, track_id=None, album_id=album.id,
        sales_model_at_purchase="non_exclusive",
        gross_amount=price, currency=currency,
        commission_amount=Decimal(str(split["commission_amount"])),
        net_amount=Decimal(str(split["net_amount"])),
        commission_percent_at_purchase=BEATHUB_COMMISSION_PERCENT,
        status=OrderStatus.PENDING, phone_number=normalized_phone,
    )
    db.add(order)
    db.flush()
    payload = {
        "email": customer_email, "amount": _amount_subunit(price), "currency": currency,
        "reference": order.order_number,
        "callback_url": f"{settings.BASE_URL.rstrip('/')}/paystack/callback",
        "channels": _paystack_channels(currency),
        "metadata": {"beathub_order_id": order.id, "beathub_album_slug": slug, "buyer_id": user.id, "buyer_phone": normalized_phone, "guest": is_guest},
    }
    subaccount = getattr(album.creator_profile, "paystack_subaccount_code", None)
    if subaccount:
        payload["subaccount"] = str(subaccount)
        payload["transaction_charge"] = _amount_subunit(Decimal(str(split["commission_amount"])))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/initialize", headers=_headers(), json=payload)
        result = response.json()
    except Exception:
        db.rollback()
        logger.exception("Album Paystack initialization failed")
        raise HTTPException(status_code=502, detail="Payment service unavailable. No charge was made.")
    if response.status_code >= 400 or not result.get("status"):
        db.rollback()
        raise HTTPException(status_code=400, detail=_paystack_rejection_message(result, currency))
    data = result.get("data") or {}
    if not data.get("authorization_url"):
        db.rollback()
        raise HTTPException(status_code=502, detail="Payment service did not provide a checkout URL.")
    db.add(PaymentTransaction(
        order_id=order.id, checkout_request_id=data.get("reference") or order.order_number,
        phone_number=normalized_phone, amount=price, currency=currency,
        status=PaymentStatus.PENDING, result_description="Album purchase awaiting verified payment.",
    ))
    db.commit()
    redirect = RedirectResponse(data["authorization_url"], status_code=303)
    if is_guest:
        redirect.set_cookie(COOKIE_NAME, issue_guest_token(order.id), max_age=MAX_AGE_SECONDS,
                            httponly=True, secure=settings.is_production, samesite="lax", path="/")
    return redirect


@router.get("/paystack/callback")
async def paystack_callback(reference: str | None = None, trxref: str | None = None, db: Session = Depends(get_db)):
    ref = (reference or trxref or "").strip()
    if not ref:
        return RedirectResponse("/beats?error=Payment%20reference%20was%20missing.", 303)
    merch_order_id = find_merchandise_order_id(db, ref)
    if merch_order_id:
        try:
            data = await _verify_reference(ref)
            complete_merchandise_payment(db, merch_order_id, ref, data)
        except Exception:
            db.rollback(); logger.exception("Paystack merchandise callback verification failed: %s", ref)
            return RedirectResponse(f"/merch/orders/{merch_order_id}?payment=pending", 303)
        return RedirectResponse(f"/merch/orders/{merch_order_id}?payment=success", 303)
    payment = db.query(PaymentTransaction).filter(PaymentTransaction.checkout_request_id == ref).first()
    if not payment:
        return RedirectResponse("/beats?error=Payment%20record%20was%20not%20found.", 303)
    order = db.get(Order, payment.order_id)
    if not order:
        return RedirectResponse("/beats?error=Payment%20order%20was%20not%20found.", 303)
    track_slug = order.track.slug if order.track else None
    album_slug = order.album.slug if order.album else None
    if not track_slug and not album_slug:
        return RedirectResponse("/beats?error=Purchased%20music%20was%20not%20found.", 303)
    destination = f"/album/{album_slug}" if album_slug else f"/track/{track_slug}"
    try:
        data = await _verify_reference(ref)
        completed = _complete_verified_payment(db, order, payment, data)
    except Exception:
        db.rollback(); logger.exception("Paystack music callback verification failed: %s", ref)
        return RedirectResponse(f"{destination}?payment=pending", 303)
    if completed:
        return RedirectResponse(f"{destination}?payment=success", 303)
    return RedirectResponse(f"{destination}?payment=failed", 303)


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    secret = settings.PAYSTACK_SECRET_KEY or ""
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    if not secret:
        raise HTTPException(status_code=503, detail="Payment verification is not configured.")
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid Paystack signature.")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.")
    if payload.get("event") != "charge.success":
        return {"status": True}
    data = payload.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return {"status": True}
    metadata = data.get("metadata") or {}
    merch_order_id = find_merchandise_order_id(db, reference, metadata)
    if merch_order_id:
        try:
            complete_merchandise_payment(db, merch_order_id, reference, data)
        except Exception:
            db.rollback(); logger.exception("Paystack merchandise webhook settlement failed: %s", reference)
            raise HTTPException(status_code=500, detail="Settlement failed.")
        return {"status": True}
    payment = db.query(PaymentTransaction).filter(PaymentTransaction.checkout_request_id == reference).first()
    if not payment:
        return {"status": True}
    order = db.get(Order, payment.order_id)
    if not order:
        return {"status": True}
    if payment.status != PaymentStatus.COMPLETED or order.status != OrderStatus.COMPLETED:
        try:
            _complete_verified_payment(db, order, payment, data)
        except Exception:
            db.rollback(); logger.exception("Paystack webhook settlement failed: %s", reference)
            raise HTTPException(status_code=500, detail="Settlement failed.")
    return {"status": True}
