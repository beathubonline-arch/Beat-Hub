"""Canonical Paystack checkout for BeatHub.

Paystack is the customer payment gateway for BeatHub. Currency and amount are
always read from the server-side Track/Order records; the browser never gets to
choose the amount or currency used to initialize or settle a transaction.

Successful payments are authoritative only after Paystack verification/webhook
checks status, amount, and currency against the immutable Order snapshot.
"""

import hashlib
import hmac
import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.currency import SUPPORTED_CURRENCIES, normalize_currency
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.music import SalesModel, Track
from app.models.user import User
from app.services.merchandise_payments import complete_merchandise_payment, find_merchandise_order_id
from app.services.orders import finalize_order
from app.services.pricing import BEATHUB_COMMISSION_PERCENT, calculate_split
from app.utils.deps import require_user

router = APIRouter(tags=["paystack"])
logger = logging.getLogger("beathub.paystack")

PAYSTACK_MINIMUMS = {
    "KES": Decimal("3.00"),
    "USD": Decimal("2.00"),
}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _currency_for_track(track: Track) -> str:
    """Return the canonical product currency, defaulting legacy rows to KES."""
    return normalize_currency(getattr(track, "currency", None))


def _minor_units(amount: Decimal, currency: str) -> int:
    """Convert a major-unit Decimal into Paystack's minor-unit integer."""
    currency = normalize_currency(currency)
    # BeatHub currently supports KES and USD, both with two decimal places.
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def _verify_reference(reference: str) -> dict:
    """Verify a Paystack reference without blocking FastAPI's event loop."""
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not configured.")

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(
            f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/verify/{reference}",
            headers=_headers(),
        )

    response.raise_for_status()
    payload = response.json()
    if not payload.get("status") or not isinstance(payload.get("data"), dict):
        raise RuntimeError(payload.get("message") or "Paystack verification failed.")
    return payload["data"]


def _available(track: Track) -> bool:
    """Return whether a track can currently be purchased."""
    if not bool(getattr(track, "is_published", False)):
        return False

    try:
        price = Decimal(str(getattr(track, "price", 0)))
    except Exception:
        return False
    if price <= 0:
        return False

    try:
        currency = _currency_for_track(track)
    except ValueError:
        return False
    if currency not in SUPPORTED_CURRENCIES:
        return False
    if price < PAYSTACK_MINIMUMS[currency]:
        return False

    sales_model = getattr(track, "sales_model", None)
    sales_model_value = getattr(sales_model, "value", sales_model)
    if str(sales_model_value).lower() == SalesModel.EXCLUSIVE.value:
        return not bool(getattr(track, "is_sold", False))

    return True


def _complete_verified_payment(db: Session, order: Order, payment: PaymentTransaction, data: dict) -> bool:
    """Apply a verified Paystack result exactly once under DB locks."""
    locked_payment = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.id == payment.id)
        .with_for_update()
        .one_or_none()
    )
    locked_order = (
        db.query(Order)
        .filter(Order.id == order.id)
        .with_for_update()
        .one_or_none()
    )

    if locked_payment is None or locked_order is None:
        raise RuntimeError("Payment or order disappeared during verification.")

    payment = locked_payment
    order = locked_order

    if payment.callback_processed and payment.status == PaymentStatus.COMPLETED and order.status == OrderStatus.COMPLETED:
        return True
    if payment.status == PaymentStatus.COMPLETED and order.status == OrderStatus.COMPLETED:
        return True

    status = str(data.get("status", "")).lower()
    if status != "success":
        payment.status = PaymentStatus.FAILED
        payment.result_description = f"Paystack transaction status: {status or 'unknown'}"
        payment.callback_processed = True
        order.status = OrderStatus.FAILED
        db.commit()
        return False

    try:
        expected_currency = normalize_currency(order.currency)
    except ValueError:
        payment.status = PaymentStatus.FAILED
        payment.result_description = "Order contains an unsupported currency."
        payment.callback_processed = True
        order.status = OrderStatus.FAILED
        db.commit()
        return False

    expected_amount = _minor_units(Decimal(str(order.gross_amount)), expected_currency)
    try:
        actual_amount = int(data.get("amount") or 0)
    except (TypeError, ValueError):
        actual_amount = 0
    actual_currency = str(data.get("currency") or "").upper().strip()

    if actual_currency != expected_currency or actual_amount != expected_amount:
        payment.status = PaymentStatus.FAILED
        payment.result_description = (
            "Paystack verification amount or currency mismatch. "
            f"Expected {expected_currency} {expected_amount}; "
            f"received {actual_currency or 'missing'} {actual_amount}."
        )
        payment.callback_processed = True
        order.status = OrderStatus.FAILED
        db.commit()
        return False

    payment.status = PaymentStatus.COMPLETED
    payment.result_code = 0
    payment.result_description = "Paystack payment verified successfully."
    payment.completed_at = datetime.utcnow()
    payment.callback_processed = True
    payment.currency = expected_currency
    payment.amount = Decimal(str(order.gross_amount))

    customer = data.get("customer") or {}
    phone = customer.get("phone")
    if phone:
        payment.phone_number = str(phone)[:20]

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
        return RedirectResponse(
            f"/track/{slug}?error=This%20track%20is%20no%20longer%20available%20for%20purchase.",
            303,
        )

    profile = getattr(track, "creator_profile", None)
    if getattr(profile, "user_id", None) == user.id:
        return RedirectResponse(f"/track/{slug}?error=You%20cannot%20purchase%20your%20own%20track.", 303)

    price = Decimal(str(track.price))
    currency = _currency_for_track(track)
    minimum = PAYSTACK_MINIMUMS[currency]
    if price <= 0:
        raise HTTPException(status_code=400, detail="This track has an invalid price.")
    if price < minimum:
        symbol = "KSh" if currency == "KES" else "$"
        return RedirectResponse(
            f"/checkout/track/{slug}?error=Paystack%20requires%20a%20minimum%20payment%20of%20{symbol}%20{minimum:.2f}.",
            303,
        )

    customer_email = (email or "").strip().lower() or (getattr(user, "email", "") or "").strip().lower()
    if not EMAIL_RE.fullmatch(customer_email):
        return RedirectResponse(
            f"/checkout/track/{slug}?error=Please%20enter%20a%20valid%20email%20address%20for%20Paystack%20checkout.",
            303,
        )

    split = calculate_split(price)
    if Decimal(str(split["commission_percent"])) != BEATHUB_COMMISSION_PERCENT:
        raise HTTPException(status_code=500, detail="Invalid BeatHub commission configuration.")

    order = Order(
        id=str(uuid.uuid4()),
        order_number=f"BH{uuid.uuid4().hex[:10].upper()}",
        buyer_id=user.id,
        track_id=track.id,
        album_id=None,
        sales_model_at_purchase=str(getattr(getattr(track, "sales_model", None), "value", track.sales_model)),
        gross_amount=Decimal(str(split["gross_amount"])),
        commission_amount=Decimal(str(split["commission_amount"])),
        net_amount=Decimal(str(split["net_amount"])),
        commission_percent_at_purchase=Decimal(str(split["commission_percent"])),
        currency=currency,
        status=OrderStatus.PENDING,
        phone_number="paystack",
    )
    db.add(order)
    db.flush()

    callback_url = f"{settings.BASE_URL.rstrip('/')}/paystack/callback"
    payload = {
        "email": customer_email,
        "amount": _minor_units(price, currency),
        "currency": currency,
        "reference": order.order_number,
        "callback_url": callback_url,
        "channels": ["card", "mobile_money"],
        "metadata": {
            "beathub_order_id": order.id,
            "beathub_track_slug": track.slug,
            "buyer_id": user.id,
            "beathub_currency": currency,
            "beathub_commission_percent": str(split["commission_percent"]),
            "beathub_commission_amount": str(split["commission_amount"]),
            "beathub_producer_amount": str(split["net_amount"]),
        },
    }

    producer_subaccount = getattr(profile, "paystack_subaccount_code", None)
    if producer_subaccount:
        payload["subaccount"] = str(producer_subaccount)
        payload["transaction_charge"] = _minor_units(Decimal(str(split["commission_amount"])), currency)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(
                f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/initialize",
                headers=_headers(),
                json=payload,
            )
        data = response.json()
    except Exception:
        db.rollback()
        logger.exception("Paystack initialization failed for order %s", order.id)
        raise HTTPException(status_code=502, detail="Paystack could not be reached. Please try again.")

    if response.status_code >= 400 or not data.get("status"):
        message = data.get("message") or "Paystack could not initialize checkout."
        db.rollback()
        raise HTTPException(status_code=400, detail=message)

    authorization = data.get("data", {}).get("authorization_url")
    reference = data.get("data", {}).get("reference") or order.order_number
    if not authorization:
        db.rollback()
        raise HTTPException(status_code=400, detail="Paystack did not return a checkout URL.")

    payment = PaymentTransaction(
        order_id=order.id,
        checkout_request_id=reference,
        phone_number="paystack",
        amount=Decimal(str(order.gross_amount)),
        currency=currency,
        status=PaymentStatus.PENDING,
        result_description=(
            f"Paystack {currency} checkout initialized with {split['commission_percent']}% BeatHub / "
            f"{Decimal('100') - Decimal(str(split['commission_percent']))}% producer split."
        ),
    )
    db.add(payment)
    db.commit()
    return RedirectResponse(authorization, status_code=303)


@router.get("/paystack/callback")
async def paystack_callback(
    request: Request,
    reference: str | None = None,
    trxref: str | None = None,
    db: Session = Depends(get_db),
):
    reference = reference or trxref
    if not reference:
        return RedirectResponse("/beats?error=Payment%20reference%20was%20missing.", 303)

    merch_order_id = find_merchandise_order_id(db, reference)
    if merch_order_id:
        return RedirectResponse(f"/merch/orders/{merch_order_id}", 303)

    payment = db.query(PaymentTransaction).filter(
        PaymentTransaction.checkout_request_id == reference
    ).first()
    if not payment:
        return RedirectResponse("/beats?error=Payment%20record%20was%20not%20found.", 303)

    return RedirectResponse(f"/orders/{payment.order_id}/status", 303)


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    secret = settings.PAYSTACK_SECRET_KEY or ""
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

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
            db.rollback()
            logger.exception("Paystack merchandise webhook settlement failed: %s", reference)
            raise HTTPException(status_code=500, detail="Settlement failed.")
        return {"status": True}

    payment = db.query(PaymentTransaction).filter(
        PaymentTransaction.checkout_request_id == reference
    ).first()
    if not payment:
        return {"status": True}

    order = db.get(Order, payment.order_id)
    if not order:
        return {"status": True}

    if payment.status != PaymentStatus.COMPLETED or order.status != OrderStatus.COMPLETED:
        try:
            _complete_verified_payment(db, order, payment, data)
        except Exception:
            db.rollback()
            logger.exception("Paystack webhook settlement failed: %s", reference)
            raise HTTPException(status_code=500, detail="Settlement failed.")

    return {"status": True}
