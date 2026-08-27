"""Provider-neutral order finalization for confirmed successful payments.

Payment providers verify a transaction first, then call ``finalize_order``.
Ownership and both creator/platform financial credits are created atomically.

CRITICAL INVARIANTS:
- Never finalize an order merely because checkout was initialized.
- Finalization happens only after the payment provider confirms success.
- Exclusive tracks are protected by the database unique constraint.
- Duplicate webhook/callback delivery is idempotent.
- A completed order creates at most one creator credit and one platform credit.
"""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ledger import CreatorLedgerEntry
from app.models.music import SalesModel, Track
from app.models.order import ExclusiveOwnershipLock, License, Order, OrderStatus
from app.services.platform_finance import record_platform_commission


class OrderFinalizationResult:
    def __init__(self, status: OrderStatus, message: str):
        self.status = status
        self.message = message


def finalize_order(db: Session, order: Order) -> OrderFinalizationResult:
    """Finalize a successfully verified payment's BeatHub order."""

    if order.status == OrderStatus.COMPLETED:
        # A retry after a committed transaction must not create another ledger
        # credit. The service-level uniqueness check makes this safe as well.
        return OrderFinalizationResult(OrderStatus.COMPLETED, "Order already completed.")

    if order.status == OrderStatus.REJECTED:
        return OrderFinalizationResult(
            OrderStatus.REJECTED,
            "Order was already rejected because the item is unavailable.",
        )

    track = db.get(Track, order.track_id) if order.track_id else None

    if order.track_id and track is None:
        order.status = OrderStatus.REJECTED
        db.commit()
        return OrderFinalizationResult(OrderStatus.REJECTED, "The purchased track no longer exists.")

    try:
        if track and track.sales_model == SalesModel.EXCLUSIVE:
            lock = ExclusiveOwnershipLock(track_id=track.id, order_id=order.id)
            db.add(lock)
            db.flush()
            track.is_sold = True

        existing_license = db.query(License).filter(License.order_id == order.id).first()
        if not existing_license:
            db.add(
                License(
                    order_id=order.id,
                    buyer_id=order.buyer_id,
                    track_id=order.track_id,
                    album_id=order.album_id,
                )
            )

        if track:
            existing_ledger = (
                db.query(CreatorLedgerEntry)
                .filter(CreatorLedgerEntry.order_id == order.id)
                .first()
            )
            if not existing_ledger:
                db.add(
                    CreatorLedgerEntry(
                        creator_profile_id=track.creator_profile_id,
                        order_id=order.id,
                        amount=order.net_amount,
                        description=f"Sale of '{track.title}' (order {order.order_number})",
                    )
                )

        order.status = OrderStatus.COMPLETED
        order.completed_at = datetime.utcnow()

        # Platform revenue is recorded in its own ledger, distinct from the
        # creator ledger. It is created before commit in the same transaction.
        record_platform_commission(db, order)

        db.commit()

        return OrderFinalizationResult(
            OrderStatus.COMPLETED,
            "Order completed, ownership granted, creator earnings recorded, and BeatHub commission ledgered.",
        )

    except IntegrityError:
        db.rollback()

        refreshed_order = db.get(Order, order.id)
        if refreshed_order is None:
            return OrderFinalizationResult(
                OrderStatus.REJECTED,
                "The order could not be found after the finalization conflict.",
            )

        # A unique exclusive lock conflict means another buyer won the item.
        # Do not create financial credits for this rejected order.
        if refreshed_order.status != OrderStatus.COMPLETED:
            refreshed_order.status = OrderStatus.REJECTED
            db.commit()

        return OrderFinalizationResult(
            OrderStatus.REJECTED,
            "This exclusive item was already sold to another buyer. Refund/manual review is required.",
        )
