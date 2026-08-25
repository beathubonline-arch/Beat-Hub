"""Paystack verification for physical merchandise orders."""

from __future__ import annotations

from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

router = APIRouter(tags=["paystack-merchandise"])


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


async def _verify(reference: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.PAYSTACK_BASE_URL}/transaction/verify/{reference}",
            headers=_headers(),
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("status") or not isinstance(payload.get("data"), dict):
        raise RuntimeError(payload.get("message") or "Paystack verification failed.")
    return payload["data"]


async def _complete(db: Session, reference: str, data: dict) -> str | None:
    metadata = data.get("metadata") or {}
    order_id = metadata.get("beathub_merchandise_order_id")

    if not order_id:
        row = db.execute(
            text("""
                SELECT id
                FROM beathub_merchandise_orders
                WHERE checkout_request_id = :reference
                LIMIT 1
            """),
            {"reference": reference},
        ).mappings().first()
        order_id = row["id"] if row else None

    if not order_id:
        return None

    row = db.execute(
        text("""
            SELECT id, total_amount, status
            FROM beathub_merchandise_orders
            WHERE id = :id
            LIMIT 1
        """),
        {"id": str(order_id)},
    ).mappings().first()

    if not row:
        return None

    if row["status"] == "paid":
        return str(row["id"])

    status = str(data.get("status") or "").lower()
    if status != "success":
        db.execute(
            text("""
                UPDATE beathub_merchandise_orders
                SET status = 'failed', failure_reason = :reason
                WHERE id = :id AND status = 'pending_payment'
            """),
            {
                "reason": f"Paystack transaction status: {status or 'unknown'}"[:500],
                "id": str(order_id),
            },
        )
        db.commit()
        return str(row["id"])

    expected = int((Decimal(str(row["total_amount"])) * Decimal("100")).quantize(Decimal("1")))
    actual = int(data.get("amount") or 0)
    currency = str(data.get("currency") or "").upper()

    if currency != "KES" or actual != expected:
        db.execute(
            text("""
                UPDATE beathub_merchandise_orders
                SET status = 'failed', failure_reason = :reason
                WHERE id = :id AND status = 'pending_payment'
            """),
            {
                "reason": "Paystack verification amount or currency mismatch.",
                "id": str(order_id),
            },
        )
        db.commit()
        return str(row["id"])

    db.execute(
        text("""
            UPDATE beathub_merchandise_orders
            SET status = 'paid',
                paid_at = CURRENT_TIMESTAMP,
                mpesa_receipt = COALESCE(mpesa_receipt, :reference),
                failure_reason = NULL
            WHERE id = :id AND status = 'pending_payment'
        """),
        {"reference": reference[:128], "id": str(order_id)},
    )
    db.commit()
    return str(row["id"])


@router.get("/paystack/merchandise/callback")
async def paystack_merchandise_callback(
    request: Request,
    reference: str | None = None,
    trxref: str | None = None,
    db: Session = Depends(get_db),
):
    reference = reference or trxref
    if not reference:
        return RedirectResponse("/merch?error=Payment%20reference%20was%20missing.", status_code=303)

    try:
        data = await _verify(reference)
        order_id = await _complete(db, reference, data)
    except Exception:
        db.rollback()
        return RedirectResponse(
            f"/merch?error=Payment%20verification%20failed.",
            status_code=303,
        )

    if not order_id:
        return RedirectResponse("/merch?error=Merchandise%20order%20was%20not%20found.", status_code=303)

    return RedirectResponse(f"/merch/orders/{order_id}", status_code=303)
