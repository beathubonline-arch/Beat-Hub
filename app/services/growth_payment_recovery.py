"""Safe Paystack reconciliation used by the zero-budget Growth OS.

This is intentionally narrow: it only verifies existing BeatHub Paystack
payment references and lets the canonical checkout code settle a transaction.
It never creates charges, retries payments, or contacts customers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.routers.paystack_checkout import _complete_verified_payment, _verify_reference

logger = logging.getLogger("beathub.growth_payment_recovery")


async def reconcile_recent_payments(db, days: int = 14, limit: int = 20) -> dict:
    """Verify recent pending Paystack payments and return an auditable report."""
    since = datetime.utcnow() - timedelta(days=max(1, min(int(days or 14), 30)))
    limit = max(1, min(int(limit or 20), 50))
    rows = (
        db.query(PaymentTransaction, Order)
        .join(Order, Order.id == PaymentTransaction.order_id)
        .filter(
            PaymentTransaction.status == PaymentStatus.PENDING,
            Order.status == OrderStatus.PENDING,
            PaymentTransaction.checkout_request_id.isnot(None),
            PaymentTransaction.created_at >= since,
        )
        .order_by(PaymentTransaction.created_at.desc())
        .limit(limit)
        .all()
    )

    report = {
        "checked": 0,
        "completed": 0,
        "failed": 0,
        "still_pending": 0,
        "errors": 0,
        "items": [],
    }

    for payment, order in rows:
        reference = str(payment.checkout_request_id or "").strip()
        if not reference:
            continue
        report["checked"] += 1
        item = {
            "order_id": str(order.id),
            "order_number": str(order.order_number),
            "reference": reference,
            "before": "pending",
        }
        try:
            data = await _verify_reference(reference)
            status = str(data.get("status") or "").lower()
            if status == "success":
                completed = _complete_verified_payment(db, order, payment, data)
                if completed:
                    report["completed"] += 1
                    item["after"] = "completed"
                else:
                    report["failed"] += 1
                    item["after"] = "failed"
            elif status in {"failed", "abandoned", "reversed"}:
                payment.status = PaymentStatus.FAILED
                payment.result_description = f"Paystack transaction status: {status}"
                payment.callback_processed = True
                order.status = OrderStatus.FAILED
                db.commit()
                report["failed"] += 1
                item["after"] = "failed"
            else:
                report["still_pending"] += 1
                item["after"] = "pending"
                item["paystack_status"] = status or "unknown"
        except Exception as exc:
            db.rollback()
            report["errors"] += 1
            item["after"] = "error"
            item["error"] = str(exc)[:500]
            logger.exception("Growth payment reconciliation failed for %s", reference)
        report["items"].append(item)

    return report
