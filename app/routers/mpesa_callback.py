import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.services.orders import finalize_order

router = APIRouter(tags=["mpesa"])


@router.post("/mpesa/callback")
async def mpesa_callback(request: Request, db: Session = Depends(get_db)):
    """
    Safaricom Daraja calls this endpoint after an STK Push attempt completes
    (success, failure, or user cancellation). This is the ONLY place a
    payment is confirmed — nothing on the frontend can mark an order paid.

    Daraja may retry callbacks; we guard against double-processing using
    PaymentTransaction.callback_processed keyed on the unique
    CheckoutRequestID.
    """
    body = await request.json()

    stk_callback = (
        body.get("Body", {}).get("stkCallback", {})
        if isinstance(body, dict) else {}
    )
    checkout_request_id = stk_callback.get("CheckoutRequestID")
    result_code = stk_callback.get("ResultCode")
    result_desc = stk_callback.get("ResultDesc")

    if not checkout_request_id:
        # Malformed payload — acknowledge with 200 so Daraja doesn't hammer
        # retries, but do nothing further.
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    payment = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.checkout_request_id == checkout_request_id)
        .first()
    )
    if not payment:
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    # Idempotency guard: if we've already processed this checkout request,
    # do not process it again even if Daraja resends the callback.
    if payment.callback_processed:
        return {"ResultCode": 0, "ResultDesc": "Already processed"}

    payment.raw_callback_payload = json.dumps(body)
    payment.result_code = str(result_code)
    payment.result_desc = result_desc

    order = db.get(Order, payment.order_id)

    if result_code == 0:
        # Extract the M-Pesa receipt number and confirmed amount from CallbackMetadata.
        metadata_items = stk_callback.get("CallbackMetadata", {}).get("Item", [])
        meta = {item.get("Name"): item.get("Value") for item in metadata_items}
        payment.mpesa_receipt_number = meta.get("MpesaReceiptNumber")
        payment.status = PaymentStatus.SUCCESS
        payment.callback_processed = True
        db.commit()

        if order and order.status == OrderStatus.PENDING:
            finalize_order(db, order)
    else:
        payment.status = PaymentStatus.FAILED if result_code != 1032 else PaymentStatus.CANCELLED
        payment.callback_processed = True
        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.FAILED
        db.commit()

    # Daraja expects a 200 response acknowledging receipt.
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
