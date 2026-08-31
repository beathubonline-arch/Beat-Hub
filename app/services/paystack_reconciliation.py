"""Paystack settlement import and read-only reconciliation.

Provider settlement data is evidence about money Paystack has settled. It is
kept separate from BeatHub's immutable platform/creator ledgers so importing
settlements can never double-credit revenue.
"""

import json
from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.paystack_settlement import PaystackSettlement

ZERO = Decimal("0.00")
SUCCESS_STATUSES = {"success", "successful", "paid", "completed"}


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


async def sync_paystack_settlements(db: Session, per_page: int = 100, max_pages: int = 20) -> dict:
    """Fetch Paystack settlements and upsert local snapshots.

    This function intentionally does not create or modify financial ledger
    entries. A provider settlement is not itself a BeatHub sale.
    """
    if not settings.PAYSTACK_SECRET_KEY:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not configured.")

    per_page = max(1, min(int(per_page), 100))
    max_pages = max(1, min(int(max_pages), 100))
    imported = 0
    updated = 0
    pages = 0

    headers = {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=4.0)) as client:
        for page in range(1, max_pages + 1):
            response = await client.get(
                f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/settlement",
                headers=headers,
                params={"perPage": per_page, "page": page},
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or []
            pages = page
            if not rows:
                break

            for row in rows:
                provider_id = str(row.get("id") or "").strip()
                if not provider_id:
                    continue
                existing = db.query(PaystackSettlement).filter(PaystackSettlement.paystack_id == provider_id).first()
                values = {
                    "status": str(row.get("status") or "unknown").lower(),
                    "currency": str(row.get("currency") or "").upper()[:3],
                    "total_amount": _money(Decimal(str(row.get("total_amount") or 0)) / Decimal("100")),
                    "settlement_date": _parse_datetime(row.get("settlement_date")),
                    "raw_payload": json.dumps(row, sort_keys=True, default=str),
                    "last_seen_at": datetime.utcnow(),
                }
                if existing:
                    for key, value in values.items():
                        setattr(existing, key, value)
                    updated += 1
                else:
                    db.add(PaystackSettlement(paystack_id=provider_id, **values))
                    imported += 1

            db.commit()
            if len(rows) < per_page:
                break

    return {"pages": pages, "imported": imported, "updated": updated}


def build_reconciliation(db: Session, currency: str = "KES") -> dict:
    """Return a read-only reconciliation report for the selected currency."""
    currency = (currency or "KES").upper()

    local_payment_total = db.query(func.coalesce(func.sum(PaymentTransaction.amount), 0)).filter(
        PaymentTransaction.status == PaymentStatus.COMPLETED,
        PaymentTransaction.currency == currency,
    ).scalar()
    local_order_total = db.query(func.coalesce(func.sum(Order.gross_amount), 0)).filter(
        Order.status == OrderStatus.COMPLETED,
        Order.currency == currency,
    ).scalar()

    settlements = db.query(PaystackSettlement).filter(PaystackSettlement.currency == currency).all()
    settled_total = sum((_money(row.total_amount) for row in settlements if row.status in SUCCESS_STATUSES), ZERO)
    settlement_count = sum(1 for row in settlements if row.status in SUCCESS_STATUSES)

    local_payment_total = _money(local_payment_total)
    local_order_total = _money(local_order_total)
    settled_total = _money(settled_total)

    return {
        "currency": currency,
        "local_completed_payment_total": local_payment_total,
        "local_completed_order_total": local_order_total,
        "paystack_settled_total": settled_total,
        "paystack_settlement_count": settlement_count,
        "payment_vs_order_difference": _money(local_payment_total - local_order_total),
        "payment_vs_settlement_difference": _money(local_payment_total - settled_total),
        "status": "balanced" if local_payment_total == settled_total else "review",
        "note": "Settlement totals can differ while funds are pending settlement or because Paystack fees/refunds/other adjustments are included. This report flags differences; it never changes BeatHub balances automatically.",
    }
