import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.user import User
from app.services.orders import finalize_order
from app.services.pricing import calculate_split
from app.utils.deps import require_user


router = APIRouter(prefix="/stripe", tags=["stripe"])


def stripe_enabled() -> bool:
    return bool(
        getattr(settings, "STRIPE_SECRET_KEY", "").strip()
        and getattr(settings, "STRIPE_WEBHOOK_SECRET", "").strip()
        and float(getattr(settings, "STRIPE_KES_TO_USD_RATE", 0) or 0) > 0
    )


def sales_model_value(track: Track) -> str:
    value = getattr(getattr(track, "sales_model", None), "value", None)
    if value is None:
        value = getattr(track, "sales_model", "")
    return str(value or "").strip().lower()


def track_is_available(track: Track) -> bool:
    if not track or not getattr(track, "is_published", False):
        return False

    model = sales_model_value(track)

    if model == SalesModel.NON_EXCLUSIVE.value:
        return True

    if model == SalesModel.EXCLUSIVE.value:
        return not bool(getattr(track, "is_sold", False))

    return False


def get_track(db: Session, slug: str) -> Track | None:
    return db.query(Track).filter(Track.slug == slug).first()


def kes_to_usd_minor_units(kes_amount: Decimal) -> int:
    rate = Decimal(str(getattr(settings, "STRIPE_KES_TO_USD_RATE", 0) or 0))

    if rate <= 0:
        raise ValueError("STRIPE_KES_TO_USD_RATE is not configured.")

    usd_amount = (kes_amount * rate).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    minor_units = int((usd_amount * Decimal("100")).to_integral_value())

    if minor_units < 50:
        raise ValueError("The Stripe price is below Stripe's minimum charge amount.")

    return minor_units


def stripe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:500] if message else "Stripe could not start the payment. Please try again."


def complete_stripe_payment(db: Session, payment: PaymentTransaction, order: Order) -> None:
    """Complete a Stripe payment without depending on the removed Daraja module."""
    if payment.status == PaymentStatus.COMPLETED and order.status == OrderStatus.COMPLETED:
        return

    payment.status = PaymentStatus.COMPLETED
    payment.result_code = 0
    payment.result_description = "Stripe Checkout payment confirmed."
    payment.callback_processed = True
    payment.completed_at = datetime.utcnow()

    finalize_order(db, order)


@router.post("/checkout/track/{slug}")
async def stripe_checkout_track(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not stripe_enabled():
        return RedirectResponse(
            url=f"/track/{slug}?error=Stripe%20checkout%20is%20not%20configured%20yet.",
            status_code=303,
        )

    track = get_track(db, slug)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")

    profile = getattr(track, "creator_profile", None)
    creator_user_id = getattr(profile, "user_id", None)

    if creator_user_id == user.id:
        return RedirectResponse(
            url=f"/track/{track.slug}?error=You%20cannot%20purchase%20your%20own%20track.",
            status_code=303,
        )

    if not track_is_available(track):
        return RedirectResponse(
            url=f"/track/{track.slug}?error=This%20track%20is%20no%20longer%20available%20for%20purchase.",
            status_code=303,
        )

    try:
        price = Decimal(str(track.price))
    except Exception:
        raise HTTPException(status_code=500, detail="This track has an invalid price.")

    if price <= Decimal("0"):
        raise HTTPException(status_code=400, detail="This track cannot be purchased at its current price.")

    split = calculate_split(track.price)
    gross_amount = Decimal(str(split["gross_amount"]))
    commission_amount = Decimal(str(split["commission_amount"]))
    net_amount = Decimal(str(split["net_amount"]))
    commission_percent = Decimal(str(split["commission_percent"]))

    try:
        stripe_amount = kes_to_usd_minor_units(gross_amount)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/track/{track.slug}?error={quote(str(exc))}",
            status_code=303,
        )

    order = Order(
        id=str(uuid.uuid4()),
        order_number=f"BH{uuid.uuid4().hex[:10].upper()}",
        buyer_id=user.id,
        track_id=track.id,
        album_id=None,
        sales_model_at_purchase=sales_model_value(track),
        gross_amount=gross_amount,
        commission_amount=commission_amount,
        net_amount=net_amount,
        commission_percent_at_purchase=commission_percent,
        status=OrderStatus.PENDING,
        phone_number="stripe",
    )

    db.add(order)
    db.flush()

    stripe.api_key = settings.STRIPE_SECRET_KEY

    base_url = str(getattr(settings, "BASE_URL", "") or "").rstrip("/")
    if not base_url:
        db.rollback()
        raise HTTPException(status_code=503, detail="BASE_URL is not configured.")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=f"{base_url}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/track/{track.slug}",
            customer_email=user.email,
            client_reference_id=order.id,
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": stripe_amount,
                        "product_data": {
                            "name": track.title[:250],
                            "description": "BeatHub digital music license",
                        },
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "beathub_order_id": order.id,
                "beathub_order_number": order.order_number,
                "beathub_track_id": track.id,
                "beathub_kes_amount": str(gross_amount),
            },
            payment_intent_data={
                "metadata": {
                    "beathub_order_id": order.id,
                    "beathub_order_number": order.order_number,
                }
            },
        )
    except Exception as exc:
        db.rollback()
        return RedirectResponse(
            url=f"/track/{track.slug}?error={quote(stripe_error_message(exc))}",
            status_code=303,
        )

    checkout_url = getattr(session, "url", None)
    if not checkout_url:
        db.rollback()
        raise HTTPException(status_code=502, detail="Stripe did not return a checkout URL.")

    db.commit()
    return RedirectResponse(url=checkout_url, status_code=303)


@router.get("/success")
def stripe_success(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise HTTPException(status_code=503, detail="Stripe is not configured.")

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Stripe checkout session.")

    metadata = getattr(session, "metadata", {}) or {}
    order_id = metadata.get("beathub_order_id") or getattr(session, "client_reference_id", None)

    if not order_id:
        raise HTTPException(status_code=400, detail="Stripe session is not linked to a BeatHub order.")

    order = db.get(Order, order_id)
    if not order or order.buyer_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found.")

    return RedirectResponse(url=f"/orders/{order.id}/status", status_code=303)


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        return {"received": True}

    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook payload.")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")

    event_type = event.get("type")
    if event_type not in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }:
        return {"received": True}

    session = event["data"]["object"]
    if session.get("payment_status") != "paid":
        return {"received": True}

    metadata = session.get("metadata") or {}
    order_id = metadata.get("beathub_order_id") or session.get("client_reference_id")
    if not order_id:
        return {"received": True}

    order = db.get(Order, order_id)
    if not order:
        return {"received": True}

    if order.status == OrderStatus.COMPLETED:
        return {"received": True}

    expected_usd_minor = kes_to_usd_minor_units(Decimal(str(order.gross_amount)))
    received_usd_minor = int(session.get("amount_total") or 0)
    received_currency = str(session.get("currency") or "").lower()

    if received_currency != "usd" or received_usd_minor != expected_usd_minor:
        order.status = OrderStatus.FAILED
        db.commit()
        return {"received": True}

    payment = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.order_id == order.id)
        .first()
    )

    if payment and payment.status == PaymentStatus.COMPLETED:
        return {"received": True}

    if not payment:
        payment = PaymentTransaction(
            order_id=order.id,
            merchant_request_id=None,
            checkout_request_id=None,
            phone_number="stripe",
            amount=order.gross_amount,
            status=PaymentStatus.PENDING,
        )
        db.add(payment)
        db.flush()

    complete_stripe_payment(
        db=db,
        payment=payment,
        order=order,
    )

    return {"received": True}
