"""Shared Paystack settlement logic for physical merchandise orders."""
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.services.notifications import create_notification, notify_admins
from app.services.transactional_email_notifications import notify_completed_merch_sale, notify_failed_payment

MERCH_ORDER_TABLE = "beathub_merchandise_orders"
MERCH_TABLE = "beathub_merchandise"


def _event_details(db: Session, order_id: str) -> dict:
    row = db.execute(text(f"""
        SELECT o.id, o.order_number, o.total_amount, o.quantity,
               o.buyer_id, m.creator_profile_id,
               b.email AS buyer_email, b.username AS buyer_name,
               creator.id AS creator_id, creator.email AS creator_email,
               p.stage_name AS creator_name, m.name AS product_name
        FROM {MERCH_ORDER_TABLE} o
        JOIN {MERCH_TABLE} m ON m.id = o.product_id
        LEFT JOIN profiles p ON p.id = m.creator_profile_id
        LEFT JOIN users creator ON creator.id = p.user_id
        LEFT JOIN users b ON b.id = o.buyer_id
        WHERE o.id=:id LIMIT 1
    """), {"id": str(order_id)}).mappings().first()
    return dict(row) if row else {}


def complete_merchandise_payment(db: Session, order_id: str, reference: str, data: dict) -> bool:
    row = db.execute(text(f"SELECT id, total_amount, status FROM {MERCH_ORDER_TABLE} WHERE id=:id LIMIT 1 FOR UPDATE"), {"id": str(order_id)}).mappings().first()
    if not row: return False
    if str(row["status"] or "") == "paid": return True
    event = _event_details(db, order_id)
    status = str(data.get("status") or "").lower()
    if status != "success":
        reason = f"Paystack transaction status: {status or 'unknown'}"
        db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed', failure_reason=:reason WHERE id=:id AND status='pending_payment'"), {"reason": reason[:500], "id": str(order_id)})
        db.commit()
        if event.get("buyer_email"): notify_failed_payment(event["buyer_email"], event.get("order_number") or str(order_id), reason)
        if event.get("buyer_id"): create_notification(event["buyer_id"], f"merch-payment-failed:{order_id}", "payment", "Payment not completed", f"Your merchandise payment for {event.get('product_name') or 'your order'} was not completed.", "/account/orders")
        return False

    expected = int((Decimal(str(row["total_amount"])) * Decimal("100")).quantize(Decimal("1")))
    actual = int(data.get("amount") or 0); currency = str(data.get("currency") or "").upper()
    if currency != "KES" or actual != expected:
        reason = "Paystack verification amount or currency mismatch."
        db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='failed', failure_reason=:reason WHERE id=:id AND status='pending_payment'"), {"reason": reason, "id": str(order_id)})
        db.commit()
        if event.get("buyer_email"): notify_failed_payment(event["buyer_email"], event.get("order_number") or str(order_id), reason)
        if event.get("buyer_id"): create_notification(event["buyer_id"], f"merch-payment-failed:{order_id}", "payment", "Payment not completed", "Your merchandise payment could not be verified.", "/account/orders")
        return False

    db.execute(text(f"UPDATE {MERCH_ORDER_TABLE} SET status='paid', paid_at=CURRENT_TIMESTAMP, checkout_request_id=COALESCE(checkout_request_id,:reference), mpesa_receipt=COALESCE(mpesa_receipt,:reference), failure_reason=NULL WHERE id=:id AND status='pending_payment'"), {"reference": str(reference)[:128], "id": str(order_id)})
    db.commit()

    if event.get("buyer_email") or event.get("creator_email"):
        notify_completed_merch_sale(str(event.get("id") or order_id), str(event.get("order_number") or order_id), event.get("total_amount") or row["total_amount"], str(event.get("buyer_email") or ""), str(event.get("buyer_name") or "there"), str(event.get("creator_email") or ""), str(event.get("creator_name") or "BeatHub Creator"), str(event.get("product_name") or "Merchandise"), int(event.get("quantity") or 1), "KES")

    if event.get("buyer_id"):
        create_notification(event["buyer_id"], f"merch-sale:{order_id}", "sale", "Merch order confirmed", f"Your order for {event.get('product_name') or 'merchandise'} is confirmed.", "/account/orders")
    if event.get("creator_id"):
        create_notification(event["creator_id"], f"merch-sale:{order_id}", "sale", "You made a merch sale", f"{event.get('product_name') or 'Merchandise'} was purchased on BeatHub.", "/dashboard/merch")
    notify_admins(f"merch-sale:{order_id}", "payment", "Merch payment completed", f"Merch order {event.get('order_number') or order_id} was paid.", "/admin")
    return True


def find_merchandise_order_id(db: Session, reference: str, metadata: dict | None = None) -> str | None:
    metadata = metadata or {}; order_id = metadata.get("beathub_merchandise_order_id")
    if order_id: return str(order_id)
    if not hasattr(db, "execute"): return None
    row = db.execute(text(f"SELECT id FROM {MERCH_ORDER_TABLE} WHERE checkout_request_id=:reference LIMIT 1"), {"reference": str(reference)}).mappings().first()
    return str(row["id"]) if row else None
