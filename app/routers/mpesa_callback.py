"""
BeatHub M-Pesa callback handler.

Safaricom sends the STK Push result to:

    POST /mpesa/callback

This module is responsible for:

    1. Receiving the Safaricom callback.
    2. Finding the correct PaymentTransaction.
    3. Finding the related Order.
    4. Handling successful payments.
    5. Handling failed/cancelled payments.
    6. Creating the buyer License after confirmed payment.
    7. Handling exclusive versus non-exclusive tracks.
    8. Preventing duplicate callbacks from granting ownership twice.
    9. Returning a valid Safaricom response.

IMPORTANT:

The callback is the authoritative payment confirmation.

Do NOT grant ownership when the STK request is merely initiated.
Ownership is granted only after ResultCode == 0.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import SalesModel, Track
from app.models.order import (
    ExclusiveOwnershipLock,
    License,
    Order,
    OrderStatus,
)
from app.models.payment import (
    PaymentStatus,
    PaymentTransaction,
)


router = APIRouter(
    prefix="/mpesa",
    tags=["mpesa"],
)


# ======================================================================
# HELPERS
# ======================================================================

def callback_response():
    """
    Safaricom expects a successful HTTP response from the callback.

    We return a normal JSON-compatible dictionary through FastAPI.
    """

    return {
        "ResultCode": 0,
        "ResultDesc": "Accepted",
    }


def extract_callback_metadata(
    callback_data: dict,
) -> dict:
    """
    Extract useful values from:

        Body.stkCallback.CallbackMetadata.Item

    Safaricom sends metadata in this form:

        {
            "Item": [
                {
                    "Name": "Amount",
                    "Value": 100
                },
                {
                    "Name": "MpesaReceiptNumber",
                    "Value": "ABC123"
                },
                ...
            ]
        }
    """

    metadata = {}

    body = callback_data.get("Body")

    if not isinstance(body, dict):
        return metadata

    stk_callback = body.get("stkCallback")

    if not isinstance(stk_callback, dict):
        return metadata

    callback_metadata = stk_callback.get(
        "CallbackMetadata"
    )

    if not isinstance(callback_metadata, dict):
        return metadata

    items = callback_metadata.get("Item")

    if not isinstance(items, list):
        return metadata

    for item in items:

        if not isinstance(item, dict):
            continue

        name = item.get("Name")

        if not name:
            continue

        metadata[name] = item.get("Value")

    return metadata


def get_stk_callback(
    callback_data: dict,
):
    """
    Safely return stkCallback.
    """

    body = callback_data.get("Body")

    if not isinstance(body, dict):
        return None

    stk_callback = body.get(
        "stkCallback"
    )

    if not isinstance(
        stk_callback,
        dict,
    ):
        return None

    return stk_callback


def get_sales_model_value(
    track: Track,
) -> str:
    """
    Normalize SalesModel enum/string values.
    """

    value = getattr(
        getattr(
            track,
            "sales_model",
            None,
        ),
        "value",
        None,
    )

    if value is None:
        value = str(
            getattr(
                track,
                "sales_model",
                "",
            )
        )

    return str(
        value
    ).strip().lower()


def is_exclusive_track(
    track: Track,
) -> bool:
    return (
        get_sales_model_value(track)
        == SalesModel.EXCLUSIVE.value
    )


# ======================================================================
# LICENSE CREATION
# ======================================================================

def create_track_license(
    db: Session,
    order: Order,
) -> License:
    """
    Create ownership for a completed track purchase.

    This function is deliberately idempotent.

    If the callback arrives twice, the existing License is returned
    instead of creating a second ownership record.
    """

    existing_license = (
        db.query(License)
        .filter(
            License.order_id == order.id
        )
        .first()
    )

    if existing_license:
        return existing_license

    license_record = License(
        id=None,
        order_id=order.id,
        buyer_id=order.buyer_id,
        track_id=order.track_id,
        album_id=order.album_id,
    )

    db.add(license_record)

    return license_record


# ======================================================================
# SUCCESSFUL PAYMENT
# ======================================================================

def complete_successful_payment(
    db: Session,
    payment: PaymentTransaction,
    order: Order,
    metadata: dict,
    result_code: int,
    result_description: str,
):
    """
    Finalize a successful M-Pesa payment.

    Order:
        PENDING -> COMPLETED

    Payment:
        PENDING -> COMPLETED

    License:
        created once

    Exclusive track:
        ownership lock acquired transactionally.
    """

    # --------------------------------------------------------------
    # DUPLICATE CALLBACK PROTECTION
    # --------------------------------------------------------------

    if (
        payment.status == PaymentStatus.COMPLETED
        and order.status == OrderStatus.COMPLETED
    ):
        return

    # --------------------------------------------------------------
    # TRACK PURCHASE
    # --------------------------------------------------------------

    track = None

    if order.track_id:

        track = (
            db.query(Track)
            .filter(
                Track.id == order.track_id
            )
            .first()
        )

    # --------------------------------------------------------------
    # TRACK MUST EXIST
    # --------------------------------------------------------------

    if order.track_id and not track:

        payment.status = PaymentStatus.FAILED

        payment.result_code = result_code

        payment.result_description = (
            "Payment succeeded but the "
            "purchased track no longer exists."
        )

        order.status = OrderStatus.REJECTED

        db.commit()

        return

    # --------------------------------------------------------------
    # EXCLUSIVE PURCHASE
    # --------------------------------------------------------------

    if track and is_exclusive_track(track):

        # ----------------------------------------------------------
        # FIRST CHECK EXISTING LOCK
        # ----------------------------------------------------------

        existing_lock = (
            db.query(ExclusiveOwnershipLock)
            .filter(
                ExclusiveOwnershipLock.track_id
                == track.id
            )
            .first()
        )

        if existing_lock:

            # Another buyer already owns the exclusive license.
            #
            # We MUST NOT grant this buyer ownership.

            payment.status = PaymentStatus.COMPLETED

            payment.result_code = result_code

            payment.result_description = (
                result_description
            )

            payment.completed_at = (
                datetime.utcnow()
            )

            order.status = OrderStatus.REJECTED

            db.commit()

            # ------------------------------------------------------
            # IMPORTANT:
            #
            # Safaricom payment succeeded, but the exclusive item
            # was already sold.
            #
            # Automatic refund requires a separate M-Pesa reversal/
            # refund implementation. We deliberately do not fake
            # a refund here.
            # ------------------------------------------------------

            return

        # ----------------------------------------------------------
        # ACQUIRE EXCLUSIVE LOCK
        # ----------------------------------------------------------

        ownership_lock = (
            ExclusiveOwnershipLock(
                id=None,
                track_id=track.id,
                order_id=order.id,
            )
        )

        db.add(
            ownership_lock
        )

        try:

            db.flush()

        except IntegrityError:

            # Another successful transaction won the race.

            db.rollback()

            payment = (
                db.query(
                    PaymentTransaction
                )
                .filter(
                    PaymentTransaction.id
                    == payment.id
                )
                .first()
            )

            order = (
                db.query(Order)
                .filter(
                    Order.id == order.id
                )
                .first()
            )

            if payment:
                payment.status = (
                    PaymentStatus.COMPLETED
                )

                payment.result_code = (
                    result_code
                )

                payment.result_description = (
                    result_description
                )

                payment.completed_at = (
                    datetime.utcnow()
                )

            if order:
                order.status = (
                    OrderStatus.REJECTED
                )

            db.commit()

            return

        # ----------------------------------------------------------
        # EXCLUSIVE TRACK IS NOW SOLD
        # ----------------------------------------------------------

        track.is_sold = True

    # --------------------------------------------------------------
    # PAYMENT DETAILS
    # --------------------------------------------------------------

    payment.status = PaymentStatus.COMPLETED

    payment.result_code = result_code

    payment.result_description = (
        result_description
    )

    payment.completed_at = (
        datetime.utcnow()
    )

    # --------------------------------------------------------------
    # SAVE M-PESA RECEIPT
    # --------------------------------------------------------------

    receipt = metadata.get(
        "MpesaReceiptNumber"
    )

    if receipt:

        payment.mpesa_receipt_number = str(
            receipt
        )

    # --------------------------------------------------------------
    # COMPLETE ORDER
    # --------------------------------------------------------------

    order.status = OrderStatus.COMPLETED

    order.completed_at = (
        datetime.utcnow()
    )

    # --------------------------------------------------------------
    # CREATE LICENSE
    # --------------------------------------------------------------

    create_track_license(
        db,
        order,
    )

    # --------------------------------------------------------------
    # FINAL COMMIT
    # --------------------------------------------------------------

    db.commit()


# ======================================================================
# FAILED PAYMENT
# ======================================================================

def fail_payment(
    db: Session,
    payment: PaymentTransaction,
    order: Order,
    result_code: int,
    result_description: str,
):
    """
    Mark an unsuccessful M-Pesa transaction as failed.

    No license is created.
    """

    # --------------------------------------------------------------
    # DO NOT DESTROY A SUCCESSFUL PAYMENT
    # --------------------------------------------------------------

    if (
        payment.status
        == PaymentStatus.COMPLETED
    ):
        return

    payment.status = (
        PaymentStatus.FAILED
    )

    payment.result_code = (
        result_code
    )

    payment.result_description = (
        result_description
    )

    order.status = (
        OrderStatus.FAILED
    )

    db.commit()


# ======================================================================
# M-PESA CALLBACK
# ======================================================================

@router.post("/callback")
async def mpesa_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Safaricom STK Push callback endpoint.

    Expected URL:

        https://YOUR-DOMAIN/mpesa/callback
    """

    # --------------------------------------------------------------
    # READ JSON
    # --------------------------------------------------------------

    try:

        payload = await request.json()

    except Exception:

        # Always acknowledge the callback so Safaricom does not
        # repeatedly retry malformed payloads.

        return callback_response()

    if not isinstance(
        payload,
        dict,
    ):
        return callback_response()

    # --------------------------------------------------------------
    # GET STK CALLBACK
    # --------------------------------------------------------------

    stk_callback = get_stk_callback(
        payload
    )

    if not stk_callback:

        return callback_response()

    # --------------------------------------------------------------
    # BASIC CALLBACK DATA
    # --------------------------------------------------------------

    merchant_request_id = (
        stk_callback.get(
            "MerchantRequestID"
        )
    )

    checkout_request_id = (
        stk_callback.get(
            "CheckoutRequestID"
        )
    )

    result_code_raw = (
        stk_callback.get(
            "ResultCode"
        )
    )

    result_description = (
        stk_callback.get(
            "ResultDesc"
        )
        or ""
    )

    # --------------------------------------------------------------
    # RESULT CODE
    # --------------------------------------------------------------

    try:

        result_code = int(
            result_code_raw
        )

    except (
        TypeError,
        ValueError,
    ):

        result_code = -1

    # --------------------------------------------------------------
    # CHECKOUT REQUEST ID IS REQUIRED
    # --------------------------------------------------------------

    if not checkout_request_id:

        return callback_response()

    # --------------------------------------------------------------
    # FIND PAYMENT
    # --------------------------------------------------------------

    payment = (
        db.query(
            PaymentTransaction
        )
        .filter(
            PaymentTransaction.checkout_request_id
            == checkout_request_id
        )
        .first()
    )

    # --------------------------------------------------------------
    # PAYMENT NOT FOUND
    # --------------------------------------------------------------

    if not payment:

        # Nothing in our database corresponds to this callback.
        #
        # We still acknowledge Safaricom.

        return callback_response()

    # --------------------------------------------------------------
    # FIND ORDER
    # --------------------------------------------------------------

    order = (
        db.query(Order)
        .filter(
            Order.id == payment.order_id
        )
        .first()
    )

    if not order:

        return callback_response()

    # --------------------------------------------------------------
    # DUPLICATE SUCCESS CALLBACK
    # --------------------------------------------------------------

    if (
        payment.status
        == PaymentStatus.COMPLETED
        and order.status
        == OrderStatus.COMPLETED
    ):

        return callback_response()

    # --------------------------------------------------------------
    # UPDATE MERCHANT REQUEST ID
    # --------------------------------------------------------------

    if merchant_request_id:

        payment.merchant_request_id = (
            merchant_request_id
        )

    # --------------------------------------------------------------
    # EXTRACT PAYMENT METADATA
    # --------------------------------------------------------------

    metadata = extract_callback_metadata(
        payload
    )

    # --------------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------------

    if result_code == 0:

        complete_successful_payment(
            db=db,
            payment=payment,
            order=order,
            metadata=metadata,
            result_code=result_code,
            result_description=(
                result_description
            ),
        )

        return callback_response()

    # --------------------------------------------------------------
    # FAILURE / CANCEL / TIMEOUT
    # --------------------------------------------------------------

    fail_payment(
        db=db,
        payment=payment,
        order=order,
        result_code=result_code,
        result_description=(
            result_description
        ),
    )

    return callback_response()
