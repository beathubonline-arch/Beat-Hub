"""Shared Paystack settlement logic for physical merchandise orders."""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

MERCH_ORDER_TABLE = "beathub_merchandise_orders"


def complete_merchandise_payment(db: Session, order_id: str, reference: str, data: dict) -> bool:
    """Apply a verified Paystack payment exactly once."""
    row = db.execute(
        text(f"SELECT id, total_amount, status FROM {MERCH_ORDER_TABLE} WHERE id=:id LIMIT 1 FOR UPDATE"),
        {"id": str(order_id)},
    ).mappings().first()
    if not row:
        return False

    if str(row["status"] or "") == "paid":
        db.commit()
        return True

    status = str(data.get("status") or "").lower()
    if status != "success":
        db.execute(
            text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed', failure_reason=:reason WHERE id=:id AND status='pending_payment'"),
            {"reason": f"Paystack transaction status: {status or 'unknown'}"[:500], "id": str(order_id)},
        )
        db.commit()
        return False

    expected = int((Decimal(str(row["total_amount"])) * Decimal("100")).quantize(Decimal("1")))
    actual = int(data.get("amount") or 0)
    currency = str(data.get("currency") or "").upper()
    if currency != "KES" or actual != expected:
        db.execute(
            text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed', failure_reason=:reason WHERE id=:id AND status='pending_payment'"),
            {"reason": "Paystack verification amount or currency mismatch.", "id": str(order_id)},
        )
        db.commit()
        return False

    db.execute(
        text(
            f"""
            UPDATE {MERCH_ORDER_TABLE}
            SET status='paid', paid_at=CURRENT_TIMESTAMP,
                checkout_request_id=COALESCE(checkout_request_id,:reference),
                mpesa_receipt=COALESCE(mpesa_receipt,:reference), failure_reason=NULL
            WHERE id=:id AND status='pending_payment'
            """
        ),
        {"reference": str(reference)[:128], "id": str(order_id)},
    )
    db.commit()
    return True


def find_merchandise_order_id(db: Session, reference: str, metadata: dict | None = None) -> str | None:
    metadata = metadata or {}
    order_id = metadata.get("beathub_merchandise_order_id")
    if order_id:
        return str(order_id)

    row = db.execute(
        text(f"SELECT id FROM {MERCH_ORDER_TABLE} WHERE checkout_request_id=:reference LIMIT 1"),
        {"reference": str(reference)},
    ).mappings().first()
    return str(row["id"]) if row else None
