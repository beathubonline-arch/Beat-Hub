"""Canonical BeatHub merchandise routes.

Merchandise uses Paystack for customer payments. The browser callback only
returns the buyer to the order page; the signed Paystack webhook is the main
settlement path and the order-status endpoint provides a short fallback for
webhook delivery delays.
"""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.profile import Profile
from app.models.user import User
from app.services.merchandise_payments import complete_merchandise_payment, find_merchandise_order_id
from app.services.pricing import calculate_split
from app.services.storage import (
    ALLOWED_IMAGE_EXT,
    UploadValidationError,
    _parse_r2_path,
    _r2_bucket,
    _r2_client,
    _r2_is_configured,
    media_url,
    save_upload,
    save_upload_to_r2,
)
from app.utils.deps import get_optional_user, require_creator, require_user

router = APIRouter(tags=["merchandise"])
templates = Jinja2Templates(directory="app/templates")
MERCH_TABLE = "beathub_merchandise"
MERCH_ORDER_TABLE = "beathub_merchandise_orders"
MAX_QUANTITY = 20
MAX_NOTE = 300
PAYSTACK_MINIMUM = Decimal("3.00")


def _ctx(request: Request, user: Optional[User] = None, **extra):
    data = {"request": request, "current_user": user, "user": user, "current_year": datetime.utcnow().year, "error": None, "success": None}
    data.update(extra)
    return data


def _ensure_column(db: Session, table: str, name: str, definition: str) -> None:
    columns = {column["name"] for column in inspect(db.bind).get_columns(table)}
    if name not in columns:
        db.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def ensure_merch_tables(db: Session) -> None:
    """Create/upgrade merchandise tables for existing production databases."""
    db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {MERCH_TABLE} (
            id VARCHAR(64) PRIMARY KEY, creator_profile_id VARCHAR(64), name VARCHAR(255) NOT NULL,
            slug VARCHAR(255) UNIQUE NOT NULL, description TEXT, price NUMERIC(12,2) NOT NULL,
            image_path TEXT, is_active BOOLEAN DEFAULT TRUE, stock INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {MERCH_ORDER_TABLE} (
            id VARCHAR(64) PRIMARY KEY, product_id VARCHAR(64) NOT NULL, buyer_id VARCHAR(64) NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1, unit_price NUMERIC(12,2) NOT NULL,
            total_amount NUMERIC(12,2) NOT NULL, phone_number VARCHAR(32), order_note TEXT,
            status VARCHAR(40) NOT NULL DEFAULT 'pending_payment', checkout_request_id VARCHAR(128),
            mpesa_receipt VARCHAR(128), failure_reason VARCHAR(500), paid_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    for name, definition in (
        ("commission_amount", "NUMERIC(12,2) DEFAULT 0"),
        ("net_amount", "NUMERIC(12,2) DEFAULT 0"),
        ("commission_percent_at_purchase", "NUMERIC(6,2) DEFAULT 10"),
        ("payment_provider", "VARCHAR(32) DEFAULT 'paystack'"),
    ):
        _ensure_column(db, MERCH_ORDER_TABLE, name, definition)
    db.commit()


def _paystack_headers() -> dict:
    return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}


def _paystack_amount(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _base_url(request: Request) -> str:
    configured = str(getattr(settings, "BASE_URL", "") or os.getenv("APP_BASE_URL", "")).strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def _quantity(value: str) -> int:
    try:
        quantity = int(str(value or "1").strip())
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.")
    if quantity < 1 or quantity > MAX_QUANTITY:
        raise ValueError(f"Quantity must be between 1 and {MAX_QUANTITY}.")
    return quantity


@router.post("/merch/{slug}/buy")
async def buy_merchandise(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user), phone: str = Form(""), quantity: str = Form("1"), order_note: str = Form("")):
    ensure_merch_tables(db)
    row = _product(db, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Merchandise item not found.")
    owner = db.query(Profile).filter(Profile.id == str(row["creator_profile_id"])).first()
    if owner and str(owner.user_id) == str(user.id):
        raise HTTPException(status_code=403, detail="You cannot purchase your own merchandise.")
    try:
        qty = _quantity(quantity)
    except ValueError as exc:
        return RedirectResponse(f"/merch/{slug}?error={quote(str(exc))}", 303)
    note = (order_note or "").strip()
    if len(note) > MAX_NOTE:
        return RedirectResponse(f"/merch/{slug}?error={quote('Order note is too long.')}", 303)

    unit_price = Decimal(str(row["price"])).quantize(Decimal("0.01"))
    total = (unit_price * Decimal(qty)).quantize(Decimal("0.01"))
    split = calculate_split(total)
    if total < PAYSTACK_MINIMUM:
        return RedirectResponse(f"/merch/{slug}?error={quote('Paystack requires a minimum payment of KSh 3.00.')}", 303)
    if not settings.PAYSTACK_SECRET_KEY:
        return RedirectResponse(f"/merch/{slug}?error={quote('Paystack is not configured yet.')}", 303)

    order_id = str(uuid4())
    order_number = f"BM{uuid4().hex[:10].upper()}"
    db.execute(text(f"""
        INSERT INTO {MERCH_ORDER_TABLE}
        (id, product_id, buyer_id, quantity, unit_price, total_amount, phone_number, order_note,
         status, commission_amount, net_amount, commission_percent_at_purchase, payment_provider)
        VALUES (:id,:product_id,:buyer_id,:quantity,:unit_price,:total_amount,:phone,:note,
                'pending_payment',:commission,:net,:commission_percent,'paystack')
    """), {
        "id": order_id, "product_id": str(row["id"]), "buyer_id": str(user.id), "quantity": qty,
        "unit_price": unit_price, "total_amount": total, "phone": (phone or "").strip()[:32] or None,
        "note": note or None, "commission": split["commission_amount"], "net": split["net_amount"],
        "commission_percent": split["commission_percent"],
    })
    db.commit()

    callback_url = f"{_base_url(request)}/paystack/callback"
    payload = {
        "email": (user.email or "").strip().lower(), "amount": str(_paystack_amount(total)), "currency": "KES",
        "reference": order_number, "callback_url": callback_url, "channels": ["card", "mobile_money"],
        "metadata": {"beathub_merchandise_order_id": order_id, "beathub_merchandise_product_id": str(row["id"]), "beathub_merchandise_order_number": order_number, "beathub_commission_percent": str(split["commission_percent"]), "beathub_commission_amount": str(split["commission_amount"]), "beathub_producer_amount": str(split["net_amount"]), "buyer_id": str(user.id), "customer_phone": (phone or "").strip()[:32]},
    }
    if owner and getattr(owner, "paystack_subaccount_code", None):
        payload["subaccount"] = str(owner.paystack_subaccount_code)
        payload["transaction_charge"] = _paystack_amount(split["commission_amount"])

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/initialize", headers=_paystack_headers(), json=payload)
        data = response.json()
    except Exception:
        db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed', failure_reason=:reason WHERE id=:id"), {"reason": "Paystack could not be reached.", "id": order_id})
        db.commit()
        return RedirectResponse(f"/merch/{slug}?error={quote('Paystack could not be reached. Please try again.')}", 303)

    if response.status_code >= 400 or not data.get("status"):
        message = str(data.get("message") or "Paystack could not initialize checkout.")[:500]
        db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed', failure_reason=:reason WHERE id=:id"), {"reason": message, "id": order_id})
        db.commit()
        return RedirectResponse(f"/merch/{slug}?error={quote(message)}", 303)

    checkout = data.get("data") or {}
    authorization_url = checkout.get("authorization_url")
    reference = checkout.get("reference") or order_number
    if not authorization_url:
        db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed', failure_reason=:reason WHERE id=:id"), {"reason": "Paystack did not return a checkout URL.", "id": order_id})
        db.commit()
        return RedirectResponse(f"/merch/{slug}?error={quote('Paystack did not return a checkout URL.')}", 303)

    db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET checkout_request_id=:reference WHERE id=:id AND status='pending_payment'"), {"reference": str(reference), "id": order_id})
    db.commit()
    return RedirectResponse(authorization_url, 303)


@router.get("/merch/orders/{order_id}")
def merchandise_order_status(order_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    ensure_merch_tables(db)
    row = db.execute(text(f"""
        SELECT o.*, m.name AS product_name, m.slug AS product_slug, m.image_path, m.description AS product_description
        FROM {MERCH_ORDER_TABLE} o JOIN {MERCH_TABLE} m ON m.id=o.product_id
        WHERE o.id=:id LIMIT 1
    """), {"id": str(order_id)}).mappings().first()
    if not row or str(row["buyer_id"]) != str(user.id):
        raise HTTPException(status_code=404, detail="Merchandise order not found.")
    order = dict(row)
    order["image_url"] = _image_url(request, order.get("image_path"))
    return templates.TemplateResponse(request, "merchandise_order.html", _ctx(request, user, order=order, title="Merchandise Order"))


@router.get("/api/merch/orders/{order_id}/status")
async def merchandise_order_status_api(order_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Fast payment-status endpoint used after the Paystack browser callback."""
    ensure_merch_tables(db)
    row = db.execute(text(f"SELECT * FROM {MERCH_ORDER_TABLE} WHERE id=:id LIMIT 1"), {"id": str(order_id)}).mappings().first()
    if not row or str(row["buyer_id"]) != str(user.id):
        raise HTTPException(status_code=404, detail="Merchandise order not found.")

    status = str(row["status"] or "pending_payment")
    if status == "pending_payment" and row.get("checkout_request_id"):
        reference = str(row["checkout_request_id"])
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0)) as client:
                response = await client.get(
                    f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/transaction/verify/{reference}",
                    headers=_paystack_headers(),
                )
            if response.status_code < 400:
                verified = response.json().get("data") or {}
                if str(verified.get("status") or "").lower() == "success":
                    complete_merchandise_payment(db, order_id, reference, verified)
                    status = "paid"
        except Exception:
            pass

    return {"status": status, "paid": status == "paid", "failed": status == "failed"}


@router.post("/paystack/merchandise/callback")
async def legacy_paystack_merchandise_callback(request: Request, db: Session = Depends(get_db)):
    reference = request.query_params.get("reference") or request.query_params.get("trxref")
    if not reference:
        return RedirectResponse("/merch?error=Payment%20reference%20was%20missing.", 303)
    return RedirectResponse(f"/paystack/callback?reference={quote(reference)}", 303)
