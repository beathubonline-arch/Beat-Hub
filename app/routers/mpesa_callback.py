"""
BeatHub M-Pesa callback routes.

Handles Safaricom Daraja STK Push callbacks.

Responsibilities:
    - Receive Safaricom STK callback
    - Match PaymentTransaction by CheckoutRequestID
    - Mark payment SUCCESS / FAILED
    - Complete the related Order
    - Create the buyer License exactly once
    - Handle exclusive-track ownership safely
    - Mark exclusive tracks as sold
    - Prevent duplicate callback processing
    - Preserve producer/order accounting already stored on Order

IMPORTANT:
    Payment confirmation comes ONLY from Safaricom's callback.
"""

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import SalesModel, Track
from app.models.order import License, Order, OrderStatus, ExclusiveOwnershipLock
from app.models.payment import PaymentStatus, PaymentTransaction


router = APIRouter(
    prefix="/mpesa",
    tags=["mpesa"],
)

logger = logging.getLogger("beathub.mpesa")


# ======================================================================
# HELPERS
# ======================================================================

def callback_item(items, key):
    """
    Safaricom returns callback metadata as:

        CallbackMetadata:
            Item: [
                {"Name": "...", "Value": "..."},
                ...
            ]

    Safely retrieve one value.
    """

    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue

        if item.get("Name") == key:
            return item.get("Value")

    return None


def get_result_description(callback):
    if not isinstance(callback, dict):
        return "Unknown M-Pesa result."

    return (
        callback.get("ResultDesc")
        or callback.get("ResultDescription")
        or "M-Pesa transaction failed."
    )


def sales_model_value(track):
    value = getattr(
        getattr(track, "sales_model", None),
        "value",
        getattr(track, "sales_model", ""),
    )

    return str(value or "").strip().lower()


def is_exclusive(track):
    return (
        sales_model_value(track)
        == SalesModel.EXCLUSIVE.value
    )


def mark_payment_failed(
    payment,
    order,
    description,
):
    """
    Mark a pending payment/order as failed.

    Do not overwrite a completed transaction.
    """

    if payment:
        payment.status = PaymentStatus.FAILED

        # These attributes exist in some BeatHub payment model versions.
        # Set them only when the model supports them.
        if hasattr(payment, "result_code"):
            payment.result_code = 1

        if hasattr(payment, "result_desc"):
            payment.result_desc = description

        if hasattr(payment, "failure_reason"):
            payment.failure_reason = description

        if hasattr(payment, "updated_at"):
            payment.updated_at = datetime.utcnow()

    if order and order.status == OrderStatus.PENDING:
        order.status = OrderStatus.FAILED
        order.updated_at = datetime.utcnow()


# ======================================================================
# CALLBACK
# ======================================================================

@router.post("/callback")
async def mpesa_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Safaricom Daraja STK Push callback.

    Safaricom expects a successful HTTP response from this endpoint.
    We therefore always return a simple acknowledgement after processing.
    """

    try:
        payload = await request.json()
    except Exception:
        logger.exception(
            "BeatHub M-Pesa callback contained invalid JSON."
        )

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    logger.info(
        "BeatHub M-Pesa callback received: %s",
        payload,
    )

    body = (
        payload.get("Body")
        if isinstance(payload, dict)
        else None
    )

    stk_callback = (
        body.get("stkCallback")
        if isinstance(body, dict)
        else None
    )

    if not isinstance(stk_callback, dict):
        logger.warning(
            "M-Pesa callback missing Body.stkCallback."
        )

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    checkout_request_id = (
        stk_callback.get("CheckoutRequestID")
    )

    merchant_request_id = (
        stk_callback.get("MerchantRequestID")
    )

    result_code = stk_callback.get(
        "ResultCode"
    )

    result_desc = get_result_description(
        stk_callback
    )

    if not checkout_request_id:
        logger.warning(
            "M-Pesa callback missing CheckoutRequestID."
        )

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    # ==================================================================
    # FIND PAYMENT
    # ==================================================================

    payment = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.checkout_request_id
            == checkout_request_id
        )
        .first()
    )

    if not payment:
        logger.warning(
            "No PaymentTransaction found for CheckoutRequestID=%s",
            checkout_request_id,
        )

        # Safaricom should still receive an acknowledgement.
        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    # ==================================================================
    # FIND ORDER
    # ==================================================================

    order = (
        db.query(Order)
        .filter(
            Order.id == payment.order_id
        )
        .first()
    )

    if not order:
        logger.error(
            "Payment %s points to missing order %s.",
            getattr(payment, "id", None),
            payment.order_id,
        )

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    # ==================================================================
    # IDEMPOTENCY
    # ==================================================================
    #
    # Safaricom can retry callbacks.
    #
    # If the order is already completed, NEVER create another License
    # or credit the track again.
    # ==================================================================

    if order.status == OrderStatus.COMPLETED:
        logger.info(
            "Duplicate callback ignored for completed order %s.",
            order.order_number,
        )

        if payment.status != PaymentStatus.SUCCESS:
            payment.status = PaymentStatus.SUCCESS

        db.commit()

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    # ==================================================================
    # FAILED / CANCELLED PAYMENT
    # ==================================================================

    if str(result_code) != "0":
        logger.info(
            "M-Pesa payment failed for order %s: %s",
            order.order_number,
            result_desc,
        )

        mark_payment_failed(
            payment,
            order,
            result_desc,
        )

        db.commit()

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    # ==================================================================
    # SUCCESSFUL PAYMENT
    # ==================================================================

    metadata = {}

    callback_metadata = (
        stk_callback.get("CallbackMetadata")
    )

    if isinstance(
        callback_metadata,
        dict,
    ):
        metadata_items = callback_metadata.get(
            "Item",
            [],
        )

        if isinstance(
            metadata_items,
            list,
        ):
            for item in metadata_items:
                if not isinstance(item, dict):
                    continue

                name = item.get("Name")

                if name:
                    metadata[name] = item.get(
                        "Value"
                    )

    mpesa_receipt = metadata.get(
        "MpesaReceiptNumber"
    )

    callback_amount = metadata.get(
        "Amount"
    )

    callback_phone = metadata.get(
        "PhoneNumber"
    )

    transaction_date = metadata.get(
        "TransactionDate"
    )

    # ==================================================================
    # SAVE PAYMENT SUCCESS
    # ==================================================================

    payment.status = PaymentStatus.SUCCESS

    if hasattr(payment, "result_code"):
        payment.result_code = 0

    if hasattr(payment, "result_desc"):
        payment.result_desc = result_desc

    if hasattr(payment, "mpesa_receipt"):
        payment.mpesa_receipt = (
            str(mpesa_receipt)
            if mpesa_receipt
            else None
        )

    if hasattr(payment, "receipt_number"):
        payment.receipt_number = (
            str(mpesa_receipt)
            if mpesa_receipt
            else None
        )

    if hasattr(payment, "transaction_date"):
        payment.transaction_date = (
            str(transaction_date)
            if transaction_date
            else None
        )

    if hasattr(payment, "callback_phone"):
        payment.callback_phone = (
            str(callback_phone)
            if callback_phone
            else None
        )

    if hasattr(payment, "merchant_request_id"):
        payment.merchant_request_id = (
            merchant_request_id
            or payment.merchant_request_id
        )

    if hasattr(payment, "updated_at"):
        payment.updated_at = datetime.utcnow()

    # ==================================================================
    # FIND TRACK
    # ==================================================================

    track = None

    if order.track_id:
        track = (
            db.query(Track)
            .filter(
                Track.id == order.track_id
            )
            .first()
        )

    # ==================================================================
    # AMOUNT SAFETY CHECK
    # ==================================================================
    #
    # Do not silently complete an order when Safaricom reports a
    # materially different amount.
    # ==================================================================

    if callback_amount is not None:
        try:
            callback_decimal = Decimal(
                str(callback_amount)
            )

            order_decimal = Decimal(
                str(order.gross_amount)
            )

            if callback_decimal != order_decimal:
                logger.error(
                    "M-Pesa amount mismatch for order %s: "
                    "expected=%s received=%s",
                    order.order_number,
                    order_decimal,
                    callback_decimal,
                )

                mark_payment_failed(
                    payment,
                    order,
                    "M-Pesa payment amount mismatch.",
                )

                db.commit()

                return {
                    "ResultCode": 0,
                    "ResultDesc": "Accepted",
                }

        except Exception:
            logger.exception(
                "Could not validate M-Pesa amount for order %s.",
                order.order_number,
            )

    # ==================================================================
    # TRACK MUST EXIST
    # ==================================================================

    if not track:
        logger.error(
            "Completed M-Pesa payment %s has no track.",
            order.order_number,
        )

        order.status = OrderStatus.REJECTED
        order.updated_at = datetime.utcnow()

        db.commit()

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    # ==================================================================
    # EXCLUSIVE OWNERSHIP
    # ==================================================================
    #
    # An exclusive track can only be won once.
    #
    # The database unique constraint on ExclusiveOwnershipLock.track_id
    # is the final race-condition protection.
    # ==================================================================

    if is_exclusive(track):

        existing_lock = (
            db.query(ExclusiveOwnershipLock)
            .filter(
                ExclusiveOwnershipLock.track_id
                == track.id
            )
            .first()
        )

        if existing_lock:
            # Someone else already bought the exclusive.
            #
            # Do NOT grant this buyer ownership.
            #
            # The order is rejected. A refund must be handled by the
            # platform's refund workflow if configured.
            order.status = OrderStatus.REJECTED
            order.updated_at = datetime.utcnow()

            if hasattr(payment, "failure_reason"):
                payment.failure_reason = (
                    "Exclusive track was already purchased."
                )

            db.commit()

            logger.warning(
                "Exclusive race lost: order=%s track=%s winner_order=%s",
                order.order_number,
                track.id,
                existing_lock.order_id,
            )

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        ownership_lock = ExclusiveOwnershipLock(
            track_id=track.id,
            order_id=order.id,
        )

        db.add(ownership_lock)

        try:
            db.flush()

        except IntegrityError:
            db.rollback()

            # Re-read the order/payment after rollback.
            order = (
                db.query(Order)
                .filter(
                    Order.id == payment.order_id
                )
                .first()
            )

            if order:
                order.status = OrderStatus.REJECTED
                order.updated_at = datetime.utcnow()

            db.commit()

            logger.warning(
                "Exclusive ownership race lost for order %s.",
                getattr(
                    order,
                    "order_number",
                    payment.order_id,
                ),
            )

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        track.is_sold = True

    # ==================================================================
    # CREATE LICENSE
    # ==================================================================
    #
    # Unique order_id guarantees one license per order.
    # ==================================================================

    existing_license = (
        db.query(License)
        .filter(
            License.order_id == order.id
        )
        .first()
    )

    if not existing_license:

        license_record = License(
            id=None,
            order_id=order.id,
            buyer_id=order.buyer_id,
            track_id=order.track_id,
            album_id=order.album_id,
        )

        db.add(license_record)

    # ==================================================================
    # COMPLETE ORDER
    # ==================================================================

    order.status = OrderStatus.COMPLETED
    order.completed_at = datetime.utcnow()
    order.updated_at = datetime.utcnow()

    db.commit()

    logger.info(
        "BeatHub order completed successfully: "
        "order=%s track=%s receipt=%s",
        order.order_number,
        track.id,
        mpesa_receipt,
    )

    return {
        "ResultCode": 0,
        "ResultDesc": "Accepted",
    }
