"""Mobile payment verification endpoints.

The mobile client never decides whether a payment succeeded. It asks the
server to verify the Paystack reference, and the server uses the same
canonical fulfillment path as the web callback/webhook.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order
from app.models.payment import PaymentTransaction
from app.models.user import User
from app.routers.paystack_checkout import _complete_verified_payment, _verify_reference
from app.utils.deps import require_user

router = APIRouter(prefix="/api/v1", tags=["mobile-payments"])


@router.post("/payments/paystack/verify/{reference}")
async def verify_paystack_payment(
    reference: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Verify a Paystack reference and fulfill its BeatHub music order.

    Ownership is checked before contacting Paystack. Amount/currency are
    checked by the canonical finalizer before an order can be completed.
    Repeated verification is safe because fulfillment is idempotent.
    """
    reference = reference.strip()
    if not reference:
        raise HTTPException(400, "Payment reference is required.")

    payment = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.checkout_request_id == reference)
        .first()
    )
    if not payment:
        raise HTTPException(404, "Payment transaction not found.")

    order = db.get(Order, payment.order_id)
    if not order or order.buyer_id != user.id:
        raise HTTPException(404, "Payment order not found.")

    if order.status.value == "completed" and payment.status.value == "completed":
        return {
            "status": "completed",
            "completed": True,
            "download_available": True,
        }

    try:
        data = await _verify_reference(reference)
        completed = _complete_verified_payment(db, order, payment, data)
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, "Paystack verification could not be completed. Please try again.") from exc

    db.refresh(order)
    return {
        "status": order.status.value,
        "completed": completed and order.status.value == "completed",
        "failed": order.status.value in {"failed", "rejected"},
        "download_available": completed and order.status.value == "completed",
    }
