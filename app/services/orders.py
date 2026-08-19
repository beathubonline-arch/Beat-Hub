"""
Order finalization logic.

CRITICAL INVARIANTS:
- An order is only ever finalized (ownership granted) after a CONFIRMED
  successful M-Pesa payment callback — never on STK push initiation alone.
- Exclusive tracks can only be sold once, enforced by a database unique
  constraint (ExclusiveOwnershipLock.track_id), not just application logic.
  If two payments somehow both succeed for the same exclusive track (should
  be prevented earlier at checkout, but defense-in-depth matters), only the
  first to insert the lock row wins; the second is REJECTED and must be
  refunded/flagged for manual admin review.
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ledger import CreatorLedgerEntry
from app.models.music import SalesModel, Track
from app.models.order import ExclusiveOwnershipLock, License, Order, OrderStatus


class OrderFinalizationResult:
    def __init__(self, status: OrderStatus, message: str):
        self.status = status
        self.message = message


def finalize_order(db: Session, order: Order) -> OrderFinalizationResult:
    # Idempotency: if this order was already finalized (e.g. duplicate
    # callback), do nothing further and report the existing state.
    if order.status == OrderStatus.COMPLETED:
        return OrderFinalizationResult(OrderStatus.COMPLETED, "Order already completed.")
    if order.status == OrderStatus.REJECTED:
        return OrderFinalizationResult(OrderStatus.REJECTED, "Order was already rejected (item no longer available).")

    track = db.get(Track, order.track_id) if order.track_id else None

    try:
        if track and track.sales_model == SalesModel.EXCLUSIVE:
            lock = ExclusiveOwnershipLock(track_id=track.id, order_id=order.id)
            db.add(lock)
            db.flush()  # triggers the unique constraint check now, inside this transaction
            track.is_sold = True

        license_record = License(
            order_id=order.id,
            buyer_id=order.buyer_id,
            track_id=order.track_id,
            album_id=order.album_id,
        )
        db.add(license_record)

        if track:
            ledger_entry = CreatorLedgerEntry(
                creator_profile_id=track.creator_profile_id,
                order_id=order.id,
                amount=order.net_amount,
                description=f"Sale of '{track.title}' (order {order.order_number})",
            )
            db.add(ledger_entry)

        order.status = OrderStatus.COMPLETED
        order.completed_at = datetime.utcnow()
        db.commit()
        return OrderFinalizationResult(OrderStatus.COMPLETED, "Order completed and ownership granted.")

    except IntegrityError:
        db.rollback()
        order.status = OrderStatus.REJECTED
        db.commit()
        return OrderFinalizationResult(
            OrderStatus.REJECTED,
            "This item was already sold to another buyer moments earlier. This purchase must be refunded.",
        )
