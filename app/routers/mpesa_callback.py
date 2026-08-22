"""
BeatHub M-Pesa STK callback.

Responsibilities:

    Safaricom callback
        ↓
    locate PaymentTransaction
        ↓
    determine payment result
        ↓
    successful payment
        ↓
    finalize Order
        ↓
    create License
        ↓
    handle exclusive ownership safely
        ↓
    make purchased track downloadable

Important:

- Idempotent.
- Does not trust the browser.
- Does not trust checkout-page availability.
- Payment confirmation comes from Safaricom.
- Ownership comes from a completed Order + License.
- Non-exclusive tracks can sell repeatedly.
- Exclusive tracks can only be finalized once.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.music import SalesModel, Track
from app.models.order import (
    ExclusiveOwnershipLock,
    License,
    Order,
    OrderStatus,
)
from app.models.payment import PaymentStatus, PaymentTransaction


router = APIRouter(
    prefix="/mpesa",
    tags=["mpesa"],
)


# ======================================================================
# GENERAL HELPERS
# ======================================================================

def enum_value(value: Any) -> str:
    """
    Safely get the string value from either:

        Enum
        str
        None
    """

    if value is None:
        return ""

    return str(
        getattr(
            value,
            "value",
            value,
        )
    ).strip().lower()


def payment_status_member(
    preferred_names,
    fallback=None,
):
    """
    Find a PaymentStatus enum member without assuming
    the project uses a particular naming convention.
    """

    for name in preferred_names:

        member = getattr(
            PaymentStatus,
            name,
            None,
        )

        if member is not None:
            return member

    return fallback


def order_status_member(
    preferred_names,
    fallback=None,
):
    """
    Find an OrderStatus enum member safely.
    """

    for name in preferred_names:

        member = getattr(
            OrderStatus,
            name,
            None,
        )

        if member is not None:
            return member

    return fallback


def find_payment(
    db: Session,
    checkout_request_id: str | None,
    merchant_request_id: str | None,
):
    """
    Locate the payment transaction.

    CheckoutRequestID is the strongest identifier for
    an STK transaction.
    """

    if checkout_request_id:

        payment = (
            db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.checkout_request_id
                == checkout_request_id
            )
            .first()
        )

        if payment:
            return payment

    if merchant_request_id:

        payment = (
            db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.merchant_request_id
                == merchant_request_id
            )
            .first()
        )

        if payment:
            return payment

    return None


def set_optional_attribute(
    obj,
    attribute_name: str,
    value,
):
    """
    Only write optional attributes that actually exist
    on the SQLAlchemy model.

    This keeps the callback compatible with existing
    PaymentTransaction versions.
    """

    try:

        mapper = obj.__class__.__mapper__

        if attribute_name in mapper.attrs:

            setattr(
                obj,
                attribute_name,
                value,
            )

            return True

    except Exception:
        pass

    return False


def parse_stk_callback(
    payload: dict,
) -> dict:
    """
    Extract the useful information from Safaricom's
    STK callback structure.
    """

    body = (
        payload
        .get("Body", {})
        if isinstance(payload, dict)
        else {}
    )

    stk_callback = (
        body.get("stkCallback", {})
        if isinstance(body, dict)
        else {}
    )

    if not isinstance(
        stk_callback,
        dict,
    ):
        stk_callback = {}

    result_code = stk_callback.get(
        "ResultCode"
    )

    result_desc = (
        stk_callback.get(
            "ResultDesc"
        )
        or ""
    )

    metadata = {}

    callback_metadata = stk_callback.get(
        "CallbackMetadata"
    )

    if isinstance(
        callback_metadata,
        dict,
    ):

        item_list = callback_metadata.get(
            "Item",
            [],
        )

        if isinstance(
            item_list,
            list,
        ):

            for item in item_list:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                name = item.get(
                    "Name"
                )

                if not name:
                    continue

                metadata[name] = item.get(
                    "Value"
                )

    return {
        "merchant_request_id": (
            stk_callback.get(
                "MerchantRequestID"
            )
        ),
        "checkout_request_id": (
            stk_callback.get(
                "CheckoutRequestID"
            )
        ),
        "result_code": result_code,
        "result_desc": result_desc,
        "metadata": metadata,
    }


def payment_is_successful(
    result_code,
) -> bool:
    """
    Safaricom STK ResultCode 0 = successful payment.
    """

    try:
        return int(result_code) == 0
    except (
        TypeError,
        ValueError,
    ):
        return False


def track_is_exclusive(
    track: Track,
) -> bool:

    sales_model = getattr(
        track,
        "sales_model",
        None,
    )

    value = enum_value(
        sales_model
    )

    return (
        value
        == SalesModel.EXCLUSIVE.value
    )


# ======================================================================
# LICENSE CREATION
# ======================================================================

def license_already_exists(
    db: Session,
    order: Order,
) -> License | None:

    return (
        db.query(License)
        .filter(
            License.order_id == order.id
        )
        .first()
    )


def create_license(
    db: Session,
    order: Order,
    track: Track,
) -> License:

    existing = license_already_exists(
        db,
        order,
    )

    if existing:
        return existing

    license_record = License(
        id=__import__("uuid").uuid4().__str__(),
        order_id=order.id,
        buyer_id=order.buyer_id,
        track_id=track.id,
        album_id=None,
        granted_at=datetime.utcnow(),
    )

    db.add(
        license_record
    )

    db.flush()

    return license_record


# ======================================================================
# EXCLUSIVE FINALIZATION
# ======================================================================

def acquire_exclusive_lock(
    db: Session,
    order: Order,
    track: Track,
) -> bool:
    """
    Attempt to acquire the unique database lock for an exclusive track.

    Returns:

        True
            this order owns the exclusive sale.

        False
            another completed purchase already owns it.
    """

    existing_lock = (
        db.query(
            ExclusiveOwnershipLock
        )
        .filter(
            ExclusiveOwnershipLock.track_id
            == track.id
        )
        .first()
    )

    if existing_lock:

        return (
            existing_lock.order_id
            == order.id
        )

    lock = ExclusiveOwnershipLock(
        id=__import__("uuid").uuid4().__str__(),
        track_id=track.id,
        order_id=order.id,
        locked_at=datetime.utcnow(),
    )

    db.add(lock)

    try:

        db.flush()

        return True

    except IntegrityError:

        db.rollback()

        existing_lock = (
            db.query(
                ExclusiveOwnershipLock
            )
            .filter(
                ExclusiveOwnershipLock.track_id
                == track.id
            )
            .first()
        )

        if existing_lock:

            return (
                existing_lock.order_id
                == order.id
            )

        return False


# ======================================================================
# SAVE PAYMENT DETAILS
# ======================================================================

def save_callback_payment_details(
    payment: PaymentTransaction,
    callback: dict,
):
    """
    Save Safaricom callback information where the current
    PaymentTransaction model supports the corresponding fields.
    """

    metadata = callback.get(
        "metadata",
        {},
    )

    receipt = (
        metadata.get(
            "MpesaReceiptNumber"
        )
    )

    transaction_date = (
        metadata.get(
            "TransactionDate"
        )
    )

    callback_phone = (
        metadata.get(
            "PhoneNumber"
        )
    )

    amount = (
        metadata.get(
            "Amount"
        )
    )

    set_optional_attribute(
        payment,
        "mpesa_receipt_number",
        receipt,
    )

    set_optional_attribute(
        payment,
        "receipt_number",
        receipt,
    )

    set_optional_attribute(
        payment,
        "transaction_id",
        receipt,
    )

    set_optional_attribute(
        payment,
        "mpesa_transaction_id",
        receipt,
    )

    set_optional_attribute(
        payment,
        "result_code",
        callback.get(
            "result_code"
        ),
    )

    set_optional_attribute(
        payment,
        "result_desc",
        callback.get(
            "result_desc"
        ),
    )

    set_optional_attribute(
        payment,
        "transaction_date",
        transaction_date,
    )

    set_optional_attribute(
        payment,
        "callback_phone_number",
        callback_phone,
    )

    # Keep amount from the database authoritative.
    # We only store Safaricom's amount if the model has
    # a dedicated callback amount field.
    set_optional_attribute(
        payment,
        "callback_amount",
        (
            Decimal(str(amount))
            if amount is not None
            else None
        ),
    )


# ======================================================================
# SUCCESSFUL PAYMENT
# ======================================================================

def finalize_successful_payment(
    db: Session,
    payment: PaymentTransaction,
    order: Order,
    track: Track,
    callback: dict,
):
    """
    Convert a confirmed M-Pesa payment into ownership.

    This is intentionally idempotent.
    """

    # --------------------------------------------------------------
    # ALREADY COMPLETED
    # --------------------------------------------------------------

    completed_status = order_status_member(
        ["COMPLETED"]
    )

    if (
        completed_status is not None
        and order.status == completed_status
    ):

        if not license_already_exists(
            db,
            order,
        ):

            create_license(
                db,
                order,
                track,
            )

        set_optional_attribute(
            payment,
            "status",
            payment_status_member(
                [
                    "COMPLETED",
                    "SUCCESS",
                    "SUCCESSFUL",
                ],
                getattr(
                    payment,
                    "status",
                    None,
                ),
            ),
        )

        save_callback_payment_details(
            payment,
            callback,
        )

        db.commit()

        return "already_completed"

    # --------------------------------------------------------------
    # EXCLUSIVE TRACK
    # --------------------------------------------------------------

    if track_is_exclusive(track):

        locked = acquire_exclusive_lock(
            db,
            order,
            track,
        )

        if not locked:

            rejected_status = (
                order_status_member(
                    ["REJECTED"]
                )
            )

            if rejected_status is not None:
                order.status = rejected_status

            set_optional_attribute(
                payment,
                "status",
                payment_status_member(
                    [
                        "FAILED",
                        "REJECTED",
                    ],
                    getattr(
                        payment,
                        "status",
                        None,
                    ),
                ),
            )

            save_callback_payment_details(
                payment,
                callback,
            )

            db.commit()

            return "rejected_exclusive"

        track.is_sold = True

    # --------------------------------------------------------------
    # CREATE LICENSE
    # --------------------------------------------------------------

    create_license(
        db,
        order,
        track,
    )

    # --------------------------------------------------------------
    # COMPLETE ORDER
    # --------------------------------------------------------------

    if completed_status is not None:

        order.status = completed_status

    order.completed_at = datetime.utcnow()

    # --------------------------------------------------------------
    # COMPLETE PAYMENT
    # --------------------------------------------------------------

    completed_payment_status = (
        payment_status_member(
            [
                "COMPLETED",
                "SUCCESS",
                "SUCCESSFUL",
            ]
        )
    )

    if completed_payment_status is not None:

        payment.status = (
            completed_payment_status
        )

    save_callback_payment_details(
        payment,
        callback,
    )

    db.commit()

    return "completed"


# ======================================================================
# FAILED PAYMENT
# ======================================================================

def finalize_failed_payment(
    db: Session,
    payment: PaymentTransaction,
    order: Order,
    callback: dict,
):
    """
    Mark unsuccessful STK payments as failed.

    No License is created.
    """

    failed_order_status = (
        order_status_member(
            ["FAILED"]
        )
    )

    if failed_order_status is not None:

        order.status = (
            failed_order_status
        )

    failed_payment_status = (
        payment_status_member(
            [
                "FAILED",
                "CANCELLED",
                "REJECTED",
            ]
        )
    )

    if failed_payment_status is not None:

        payment.status = (
            failed_payment_status
        )

    save_callback_payment_details(
        payment,
        callback,
    )

    db.commit()

    return "failed"


# ======================================================================
# CALLBACK
# ======================================================================

@router.post("/callback")
async def mpesa_callback(
    request: Request,
):
    """
    Safaricom sends the STK result here.

    IMPORTANT:
    Safaricom expects a successful HTTP response even when
    the customer's payment failed.

    Therefore this endpoint always returns a simple acknowledgement
    instead of exposing database errors to Safaricom.
    """

    db = SessionLocal()

    try:

        try:

            payload = await request.json()

        except Exception:

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        callback = parse_stk_callback(
            payload
        )

        checkout_request_id = (
            callback.get(
                "checkout_request_id"
            )
        )

        merchant_request_id = (
            callback.get(
                "merchant_request_id"
            )
        )

        if not checkout_request_id:

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        # ----------------------------------------------------------
        # FIND PAYMENT
        # ----------------------------------------------------------

        payment = find_payment(
            db,
            checkout_request_id,
            merchant_request_id,
        )

        if not payment:

            print(
                "[BeatHub M-Pesa] "
                "Callback received for unknown "
                f"CheckoutRequestID={checkout_request_id}"
            )

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        # ----------------------------------------------------------
        # FIND ORDER
        # ----------------------------------------------------------

        order = (
            db.query(Order)
            .filter(
                Order.id == payment.order_id
            )
            .first()
        )

        if not order:

            print(
                "[BeatHub M-Pesa] "
                f"Payment {payment.order_id} has no order."
            )

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        # ----------------------------------------------------------
        # FIND TRACK
        # ----------------------------------------------------------

        track = None

        if order.track_id:

            track = (
                db.query(Track)
                .filter(
                    Track.id
                    == order.track_id
                )
                .first()
            )

        if not track:

            failed_order_status = (
                order_status_member(
                    ["FAILED"]
                )
            )

            if failed_order_status is not None:

                order.status = (
                    failed_order_status
                )

            db.commit()

            print(
                "[BeatHub M-Pesa] "
                f"Order {order.order_number} "
                "has no track."
            )

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        # ----------------------------------------------------------
        # IDEMPOTENCY
        # ----------------------------------------------------------

        completed_status = (
            order_status_member(
                ["COMPLETED"]
            )
        )

        if (
            completed_status is not None
            and order.status == completed_status
        ):

            if not license_already_exists(
                db,
                order,
            ):

                create_license(
                    db,
                    order,
                    track,
                )

                db.commit()

            return {
                "ResultCode": 0,
                "ResultDesc": "Accepted",
            }

        # ----------------------------------------------------------
        # PROCESS PAYMENT
        # ----------------------------------------------------------

        if payment_is_successful(
            callback.get(
                "result_code"
            )
        ):

            result = (
                finalize_successful_payment(
                    db=db,
                    payment=payment,
                    order=order,
                    track=track,
                    callback=callback,
                )
            )

            print(
                "[BeatHub M-Pesa] "
                f"Order={order.order_number} "
                f"result={result}"
            )

        else:

            result = (
                finalize_failed_payment(
                    db=db,
                    payment=payment,
                    order=order,
                    callback=callback,
                )
            )

            print(
                "[BeatHub M-Pesa] "
                f"Order={order.order_number} "
                f"result={result} "
                f"code={callback.get('result_code')} "
                f"desc={callback.get('result_desc')}"
            )

        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    except Exception as exc:

        db.rollback()

        print(
            "[BeatHub M-Pesa] CALLBACK ERROR:",
            repr(exc),
        )

        # Safaricom should still receive an acknowledgement.
        return {
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        }

    finally:

        db.close()
