"""
BeatHub Flutterwave checkout.

Flutterwave is used as the card + Kenyan M-Pesa gateway while the existing
Safaricom Daraja integration remains available as a separate checkout option.

Security rules:
- Never trust the browser redirect by itself.
- Verify every successful transaction server-side with Flutterwave.
- Verify webhook signatures before processing them.
- Match tx_ref, amount and currency before granting ownership.
- Reuse BeatHub's existing idempotent payment-completion logic.
"""

import hashlib
import hmac
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.music import SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.user import User
from app.routers.mpesa_callback import complete_successful_payment, fail_payment
from app.services.pricing import calculate_split
from app.utils.deps import require_user


router = APIRouter(prefix="/flutterwave", tags=["flutterwave"])
logger = logging.getLogger("beathub.flutterwave")


REQUEST_TIMEOUT = 30.0


def enabled() -> bool:
    return bool(
        getattr(settings, "FLW_SECRET_KEY", "").strip()
        and getattr(settings, "FLW_SECRET_HASH", "").strip()
    )


def base_url() -> str:
    value = str(getattr(settings, "BASE_URL", "") or "").strip().rstrip("/")
    return value


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


def transaction_reference(order: Order) -> str:
    return f"BEATHUB-{order.order_number}-{uuid.uuid4().hex[:8].upper()}"


async def flutterwave_request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> dict:
    if not getattr(settings, "FLW_SECRET_KEY", "").strip():
        raise RuntimeError("FLW_SECRET_KEY is not configured.")

    api_base = str(getattr(settings, "FLW_BASE_URL", "https://api.flutterwave.com")).rstrip("/")
    url = f"{api_base}{path}"

    headers = {
        "Authorization": f"Bearer {settings.FLW_SECRET_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.request(method, url, headers=headers, json=json)

    try:
        payload = response.json()
    except Exception:
        payload = {"status": "error", "message": response.text[:500]}

    if response.status_code >= 400:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise RuntimeError(message or f"Flutterwave returned HTTP {response.status_code}.")

    if not isinstance(payload, dict):
        raise RuntimeError("Flutterwave returned an invalid response.")

    return payload


async def verify_transaction(transaction_id: str) -> dict:
    payload = await flutterwave_request(
        "GET",
        f"/v3/transactions/{transaction_id}/verify",
    )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Flutterwave returned no transaction data.")

    return data


def complete_verified_flutterwave_payment(
    db: Session,
    *,
    order: Order,
    transaction: dict,
):
    status = str(transaction.get("status") or "").strip().lower()
    tx_ref = str(transaction.get("tx_ref") or "").strip()
    currency = str(transaction.get("currency") or "").strip().upper()

    try:
        paid_amount = Decimal(str(transaction.get("amount")))
        expected_amount = Decimal(str(order.gross_amount))
    except Exception:
        raise ValueError("Flutterwave returned an invalid transaction amount.")

    if status != "successful":
        raise ValueError("Flutterwave payment has not been confirmed as successful.")

    if tx_ref != str(order.payment_transaction.checkout_request_id or ""):
        raise ValueError("Flutterwave transaction reference does not match the BeatHub order.")

    if currency != "KES":
        raise ValueError("Flutterwave payment currency does not match KES.")

    if paid_amount < expected_amount:
        raise ValueError("Flutterwave payment amount is less than the BeatHub order amount.")

    payment = order.payment_transaction
    if not payment:
        raise ValueError("BeatHub payment record is missing.")

    if payment.status == PaymentStatus.COMPLETED and order.status == OrderStatus.COMPLETED:
        return

    transaction_id = transaction.get("id")
    payment.phone_number = "flutterwave"
    payment.mpesa_receipt_number = str(transaction_id) if transaction_id is not None else None
    payment.result_code = 0
    payment.result_description = "Flutterwave payment verified successfully."

    complete_successful_payment(
        db=db,
        payment=payment,
        order=order,
        metadata={},
        result_code=0,
        result_description="Flutterwave payment verified successfully.",
    )


async def process_flutterwave_transaction(
    db: Session,
    *,
    order: Order,
    transaction_id: str,
) -> bool:
    transaction = await verify_transaction(str(transaction_id))
    complete_verified_flutterwave_payment(
        db,
        order=order,
        transaction=transaction,
    )
    return True


@router.post("/checkout/track/{slug}")
async def flutterwave_checkout_track(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not enabled():
        return RedirectResponse(
            url=f"/track/{slug}?error=Flutterwave%20checkout%20is%20not%20configured%20yet.",
            status_code=303,
        )

    track = get_track(db, slug)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")

    creator_user_id = getattr(getattr(track, "creator_profile", None), "user_id", None)
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
        phone_number="flutterwave",
    )
    db.add(order)
    db.flush()

    tx_ref = transaction_reference(order)

    payment = PaymentTransaction(
        order_id=order.id,
        merchant_request_id=None,
        checkout_request_id=tx_ref,
        phone_number="flutterwave",
        amount=gross_amount,
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    db.flush()

    redirect_url = f"{base_url()}/flutterwave/callback"
    if not base_url():
        db.rollback()
        return RedirectResponse(
            url=f"/track/{track.slug}?error=BASE_URL%20is%20not%20configured.",
            status_code=303,
        )

    payload = {
        "tx_ref": tx_ref,
        "amount": str(gross_amount),
        "currency": "KES",
        "redirect_url": redirect_url,
        "payment_options": "card,mpesa",
        "customer": {
            "email": user.email,
            "name": user.username or user.email.split("@")[0],
        },
        "customizations": {
            "title": "BeatHub",
            "description": f"BeatHub license — {track.title}"[:250],
        },
        "meta": {
            "beathub_order_id": order.id,
            "beathub_order_number": order.order_number,
            "beathub_track_id": track.id,
        },
    }

    try:
        response = await flutterwave_request(
            "POST",
            "/v3/payments",
            json=payload,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Flutterwave initialization failed: order=%s", order.order_number)
        return RedirectResponse(
            url=f"/track/{track.slug}?error={quote(str(exc)[:300])}",
            status_code=303,
        )

    link = ((response.get("data") or {}).get("link"))
    if not link:
        db.rollback()
        return RedirectResponse(
            url=f"/track/{track.slug}?error=Flutterwave%20did%20not%20return%20a%20checkout%20link.",
            status_code=303,
        )

    db.commit()
    return RedirectResponse(url=link, status_code=303)


@router.get("/callback")
async def flutterwave_callback(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tx_ref = str(request.query_params.get("tx_ref") or "").strip()
    transaction_id = str(request.query_params.get("transaction_id") or "").strip()
    status = str(request.query_params.get("status") or "").strip().lower()

    if not tx_ref:
        raise HTTPException(status_code=400, detail="Missing Flutterwave transaction reference.")

    payment = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.checkout_request_id == tx_ref)
        .first()
    )

    if not payment:
        raise HTTPException(status_code=404, detail="Flutterwave payment record not found.")

    order = db.get(Order, payment.order_id)
    if not order or order.buyer_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found.")

    if order.status == OrderStatus.COMPLETED:
        return RedirectResponse(url=f"/orders/{order.id}/status", status_code=303)

    if status == "successful" and transaction_id:
        try:
            await process_flutterwave_transaction(
                db,
                order=order,
                transaction_id=transaction_id,
            )
        except Exception as exc:
            logger.exception("Flutterwave callback verification failed: tx_ref=%s", tx_ref)
            return RedirectResponse(
                url=f"/orders/{order.id}/status?error={quote(str(exc)[:300])}",
                status_code=303,
            )
    elif status in {"cancelled", "failed"}:
        fail_payment(
            db=db,
            payment=payment,
            order=order,
            result_code=1,
            result_description=f"Flutterwave payment status: {status}.",
        )

    return RedirectResponse(url=f"/orders/{order.id}/status", status_code=303)


@router.post("/webhook")
async def flutterwave_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    secret_hash = str(getattr(settings, "FLW_SECRET_HASH", "") or "").strip()
    if not secret_hash:
        raise HTTPException(status_code=503, detail="Flutterwave webhook secret is not configured.")

    raw_body = await request.body()
    signature = request.headers.get("flutterwave-signature") or request.headers.get("verif-hash")

    if not signature:
        raise HTTPException(status_code=401, detail="Missing Flutterwave webhook signature.")

    # Current Flutterwave webhooks use HMAC-SHA256 + base64 in
    # the flutterwave-signature header. The legacy verif-hash form
    # is accepted only as an exact secret-hash match for compatibility.
    expected_hmac = hmac.new(
        secret_hash.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    expected_signature = __import__("base64").b64encode(expected_hmac).decode("ascii")

    if not (
        hmac.compare_digest(signature, expected_signature)
        or hmac.compare_digest(signature, secret_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid Flutterwave webhook signature.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Flutterwave webhook payload.")

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {"received": True}

    tx_ref = str(data.get("tx_ref") or "").strip()
    transaction_id = str(data.get("id") or "").strip()

    if not tx_ref or not transaction_id:
        return {"received": True}

    payment = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.checkout_request_id == tx_ref)
        .first()
    )

    if not payment:
        return {"received": True}

    order = db.get(Order, payment.order_id)
    if not order:
        return {"received": True}

    if payment.status == PaymentStatus.COMPLETED and order.status == OrderStatus.COMPLETED:
        return {"received": True}

    try:
        await process_flutterwave_transaction(
            db,
            order=order,
            transaction_id=transaction_id,
        )
    except ValueError as exc:
        logger.warning("Flutterwave verification rejected: tx_ref=%s reason=%s", tx_ref, exc)
        return {"received": True}
    except Exception:
        logger.exception("Flutterwave webhook transaction verification failed: tx_ref=%s", tx_ref)
        raise HTTPException(status_code=500, detail="Payment verification temporarily failed.")

    return {"received": True}
