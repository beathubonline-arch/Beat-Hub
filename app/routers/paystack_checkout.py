"""Paystack checkout for BeatHub Kenya.

Paystack is the single customer payment gateway. It supports the enabled
Kenya payment channels, including M-PESA and cards, while BeatHub performs
server-side verification before granting ownership.

Producer marketplace transactions use Paystack subaccounts when a producer
has completed Paystack payout onboarding. The subaccount is configured so
BeatHub receives exactly 10% and the producer receives 90%, while BeatHub also
keeps its own immutable ledger split for accounting and reporting.
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
from app.models.music import SalesModel, Track
from app.models.user import User
from app.services.orders import finalize_order
from app.services.pricing import BEATHUB_COMMISSION_PERCENT, calculate_split
from app.utils.deps import require_user

router = APIRouter(tags=["paystack"])
logger = logging.getLogger("beathub.paystack")

PAYSTACK_KES_MINIMUM = Decimal("3.00")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _amount_kobo(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _available(track: Track) -> bool:
    if not track or not getattr(track, "is_published", False):
        return False

    model = getattr(
        getattr(track, "sales_model", None),
        "value",
        getattr(track, "sales_model", ""),
    )
    model = str(model).lower()

    if model == SalesModel.NON_EXCLUSIVE.value:
        return True
    if model == SalesModel.EXCLUSIVE.value:
        return not bool(getattr(track, "is_sold", False))
    return False


def _verify_reference(reference: str) -> dict:
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not configured.")

    response = httpx.get(
        f"{settings.PAYSTACK_BASE_URL}/transaction/verify/{reference}",
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    if not payload.get("status") or not isinstance(payload.get("data"), dict):
        raise RuntimeError(payload.get("message") or "Paystack verification failed.")

    return payload["data"]


def _complete_verified_payment(
    db: Session,
    order: Order,
    payment: PaymentTransaction,
    data: dict,
) -> bool:
    """Apply a verified Paystack result exactly once under a DB row lock."""
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

    if (
        payment.callback_processed
        and payment.status == PaymentStatus.COMPLETED
        and order.status == OrderStatus.COMPLETED
    ):
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

    expected = _amount_kobo(Decimal(str(order.gross_amount)))
    actual = int(data.get("amount") or 0)
    currency = str(data.get("currency") or "").upper()

    if currency != "KES" or actual != expected:
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
    phone = customer.get("phone")
    if phone:
        payment.phone_number = str(phone)[:20]

    result = finalize_order(db, order)

    if result.status == OrderStatus.REJECTED:
        logger.warning(
            "Verified Paystack payment completed but order rejected: order=%s reason=%s",
            order.id,
            result.message,
        )
        return False

    return True


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
        return RedirectResponse(
            f"/track/{slug}?error=You%20cannot%20purchase%20your%20own%20track.",
            303,
        )

    price = Decimal(str(track.price))
    if price <= 0:
        raise HTTPException(status_code=400, detail="This track has an invalid price.")

    if price < PAYSTACK_KES_MINIMUM:
        return RedirectResponse(
            f"/checkout/track/{slug}?error=Paystack%20requires%20a%20minimum%20payment%20of%20KSh%203.00.",
            303,
        )

    customer_email = (email or "").strip().lower()
    if not customer_email:
        customer_email = (getattr(user, "email", "") or "").strip().lower()

    if not EMAIL_RE.fullmatch(customer_email):
        return RedirectResponse(
            f"/checkout/track/{slug}?error=Please%20enter%20a%20valid%20email%20address%20for%20Paystack%20checkout.",
            303,
        )

    # Server-authoritative marketplace split: exactly 10% BeatHub / 90% producer.
    split = calculate_split(price)
    if Decimal(str(split["commission_percent"])) != BEATHUB_COMMISSION_PERCENT:
        raise HTTPException(status_code=500, detail="Invalid BeatHub commission configuration.")

    order = Order(
        id=str(uuid.uuid4()),
        order_number=f"BH{uuid.uuid4().hex[:10].upper()}",
        buyer_id=user.id,
        track_id=track.id,
        album_id=None,
        sales_model_at_purchase=str(
            getattr(getattr(track, "sales_model", None), "value", track.sales_model)
        ),
        gross_amount=Decimal(str(split["gross_amount"])),
        commission_amount=Decimal(str(split["commission_amount"])),
        net_amount=Decimal(str(split["net_amount"])),
        commission_percent_at_purchase=Decimal("10.00"),
        status=OrderStatus.PENDING,
        phone_number="paystack",
    )
    db.add(order)
    db.flush()

    callback_url = f"{settings.BASE_URL.rstrip('/')}/paystack/callback"
    payload = {
        "email": customer_email,
        "amount": str(_amount_kobo(price)),
        "currency": "KES",
        "reference": order.order_number,
        "callback_url": callback_url,
        "metadata": {
            "beathub_order_id": order.id,
            "beathub_track_slug": track.slug,
            "buyer_id": user.id,
            "beathub_commission_percent": "10.00",
            "beathub_commission_amount": str(split["commission_amount"]),
            "beathub_producer_amount": str(split["net_amount"]),
        },
    }

    # When the producer has completed Paystack payout onboarding, attach the
    # subaccount. Paystack's percentage_charge on that subaccount is configured
    # to 10%, which means the main BeatHub account receives 10% and the producer
    # receives 90%. Without a subaccount, BeatHub still records the exact 10/90
    # split internally; no fake payout is claimed.
    producer_subaccount = getattr(profile, "paystack_subaccount_code", None)
    if producer_subaccount:
        payload["subaccount"] = str(producer_subaccount)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.PAYSTACK_BASE_URL}/transaction/initialize",
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
        logger.error(
            "Paystack initialization rejected: status=%s message=%s order=%s",
            response.status_code,
            message,
            order.id,
        )
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
        status=PaymentStatus.PENDING,
        result_description=(
            "Paystack checkout initialized with 10% BeatHub / 90% producer split."
            if producer_subaccount
            else "Paystack checkout initialized; 10% BeatHub / 90% producer split recorded internally until producer Paystack subaccount is configured."
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

    payment = db.query(PaymentTransaction).filter(
        PaymentTransaction.checkout_request_id == reference
    ).first()
    if not payment:
        return RedirectResponse("/beats?error=Payment%20record%20was%20not%20found.", 303)

    order = db.get(Order, payment.order_id)
    if not order:
        return RedirectResponse("/beats?error=Order%20was%20not%20found.", 303)

    if payment.status != PaymentStatus.COMPLETED or order.status != OrderStatus.COMPLETED:
        try:
            data = _verify_reference(reference)
            _complete_verified_payment(db, order, payment, data)
        except Exception:
            db.rollback()
            logger.exception("Paystack callback verification failed: %s", reference)
            return RedirectResponse(
                f"/orders/{order.id}/status?error=Payment%20verification%20failed.",
                303,
            )

    return RedirectResponse(f"/orders/{order.id}/status", 303)


@router.post("/paystack/webhook")
async def paystack_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    expected = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

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
            verified = _verify_reference(reference)
            _complete_verified_payment(db, order, payment, verified)
        except Exception:
            db.rollback()
            logger.exception("Paystack webhook verification failed: %s", reference)
            raise HTTPException(status_code=500, detail="Verification failed.")

    return {"status": True}
