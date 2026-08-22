import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.user import User
from app.services import mpesa
from app.services.pricing import calculate_split
from app.utils.deps import require_user


router = APIRouter(tags=["checkout"])

templates = Jinja2Templates(
    directory="app/templates"
)


# ======================================================================
# CONTEXT
# ======================================================================

def page_context(
    request: Request,
    user: User,
    **extra,
):
    context = {
        "request": request,
        "current_user": user,
        "current_year": datetime.utcnow().year,
    }

    context.update(extra)

    return context


# ======================================================================
# SALES MODEL
# ======================================================================

def track_sales_model_value(
    track: Track,
) -> str:
    """
    Normalize Track.sales_model so this works whether SQLAlchemy gives
    us a SalesModel enum or a plain string.
    """

    sales_model = getattr(
        track,
        "sales_model",
        None,
    )

    value = getattr(
        sales_model,
        "value",
        sales_model,
    )

    return str(
        value or ""
    ).strip().lower()


# ======================================================================
# AVAILABILITY
# ======================================================================

def track_is_available(
    track: Track,
) -> bool:
    """
    AUTHORITATIVE checkout availability.

    NON-EXCLUSIVE:
        published = available

        is_sold is ignored because a non-exclusive beat
        may be purchased by multiple artists.

    EXCLUSIVE:
        published AND not sold = available

        published AND sold = unavailable.
    """

    if not track:
        return False

    # --------------------------------------------------------------
    # A track must be published.
    # --------------------------------------------------------------

    if not bool(
        getattr(
            track,
            "is_published",
            False,
        )
    ):
        return False

    sales_model = track_sales_model_value(
        track
    )

    # --------------------------------------------------------------
    # NON-EXCLUSIVE
    # --------------------------------------------------------------

    if (
        sales_model
        == SalesModel.NON_EXCLUSIVE.value
    ):
        return True

    # --------------------------------------------------------------
    # EXCLUSIVE
    # --------------------------------------------------------------

    if (
        sales_model
        == SalesModel.EXCLUSIVE.value
    ):
        return not bool(
            getattr(
                track,
                "is_sold",
                False,
            )
        )

    # --------------------------------------------------------------
    # Invalid sales model.
    # Fail closed.
    # --------------------------------------------------------------

    return False


def availability_error(
    track: Track,
) -> str:
    """
    Returns a useful user-facing availability message.
    """

    if not track:
        return "Track not found."

    if not bool(
        getattr(
            track,
            "is_published",
            False,
        )
    ):
        return (
            "This track is not currently "
            "available for purchase."
        )

    sales_model = track_sales_model_value(
        track
    )

    if (
        sales_model
        == SalesModel.EXCLUSIVE.value
        and bool(
            getattr(
                track,
                "is_sold",
                False,
            )
        )
    ):
        return (
            "This exclusive track has "
            "already been sold."
        )

    if sales_model not in (
        SalesModel.NON_EXCLUSIVE.value,
        SalesModel.EXCLUSIVE.value,
    ):
        return (
            "This track has an invalid "
            "sales model."
        )

    return (
        "This track is no longer "
        "available for purchase."
    )


# ======================================================================
# FIND TRACK
# ======================================================================

def get_track(
    db: Session,
    slug: str,
) -> Track | None:

    return (
        db.query(Track)
        .filter(
            Track.slug == slug
        )
        .first()
    )


# ======================================================================
# CHECKOUT PAGE
# ======================================================================

@router.get(
    "/checkout/track/{slug}"
)
def checkout_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    track = get_track(
        db,
        slug,
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------------
    # Creator cannot purchase own track.
    # --------------------------------------------------------------

    profile = getattr(
        track,
        "creator_profile",
        None,
    )

    creator_user_id = getattr(
        profile,
        "user_id",
        None,
    )

    if creator_user_id == user.id:

        return RedirectResponse(
            url=(
                f"/track/{track.slug}"
                "?error=You cannot purchase your own track."
            ),
            status_code=303,
        )

    # --------------------------------------------------------------
    # AVAILABILITY
    # --------------------------------------------------------------

    if not track_is_available(track):

        return RedirectResponse(
            url=(
                f"/track/{track.slug}"
                "?error="
                "This%20track%20is%20no%20longer%20available%20for%20purchase."
            ),
            status_code=303,
        )

    return templates.TemplateResponse(
        request,
        "checkout.html",
        page_context(
            request,
            user,
            track=track,
        ),
    )


# ======================================================================
# START CHECKOUT / M-PESA
# ======================================================================

@router.post(
    "/checkout/track/{slug}"
)
async def checkout_submit(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    phone_number: str = Form(...),
):
    # --------------------------------------------------------------
    # 1. FIND TRACK
    # --------------------------------------------------------------

    track = get_track(
        db,
        slug,
    )

    if not track:
        raise HTTPException(
            status_code=404,
            detail="Track not found.",
        )

    # --------------------------------------------------------------
    # 2. CREATOR CANNOT BUY OWN TRACK
    # --------------------------------------------------------------

    profile = getattr(
        track,
        "creator_profile",
        None,
    )

    creator_user_id = getattr(
        profile,
        "user_id",
        None,
    )

    if creator_user_id == user.id:

        return RedirectResponse(
            url=(
                f"/track/{track.slug}"
                "?error=You cannot purchase your own track."
            ),
            status_code=303,
        )

    # --------------------------------------------------------------
    # 3. SERVER-SIDE AVAILABILITY
    # --------------------------------------------------------------

    if not track_is_available(track):

        return RedirectResponse(
            url=(
                f"/track/{track.slug}"
                "?error="
                "This%20track%20is%20no%20longer%20available%20for%20purchase."
            ),
            status_code=303,
        )

    # --------------------------------------------------------------
    # 4. VALIDATE PRICE
    # --------------------------------------------------------------

    try:

        price = Decimal(
            str(track.price)
        )

    except Exception:

        raise HTTPException(
            status_code=500,
            detail=(
                "This track has an invalid price."
            ),
        )

    if price <= Decimal("0"):

        raise HTTPException(
            status_code=400,
            detail=(
                "This track cannot be purchased "
                "at its current price."
            ),
        )

    # --------------------------------------------------------------
    # 5. CALCULATE SPLIT
    # --------------------------------------------------------------

    split = calculate_split(
        track.price
    )

    gross_amount = Decimal(
        str(
            split["gross_amount"]
        )
    )

    commission_amount = Decimal(
        str(
            split["commission_amount"]
        )
    )

    net_amount = Decimal(
        str(
            split["net_amount"]
        )
    )

    commission_percent = Decimal(
        str(
            split["commission_percent"]
        )
    )

    # --------------------------------------------------------------
    # 6. NORMALIZE PHONE
    # --------------------------------------------------------------

    try:

        normalized_phone = (
            mpesa.normalize_phone_number(
                phone_number
            )
        )

    except Exception:

        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error=(
                    "Please enter a valid Kenyan "
                    "M-Pesa phone number."
                ),
            ),
            status_code=400,
        )

    if not normalized_phone:

        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error=(
                    "Please enter a valid Kenyan "
                    "M-Pesa phone number."
                ),
            ),
            status_code=400,
        )

    # --------------------------------------------------------------
    # 7. CREATE ORDER
    # --------------------------------------------------------------

    sales_model_value = (
        track_sales_model_value(
            track
        )
    )

    order = Order(
        id=str(
            uuid.uuid4()
        ),
        order_number=(
            f"BH{uuid.uuid4().hex[:10].upper()}"
        ),
        buyer_id=user.id,
        track_id=track.id,
        album_id=None,
        sales_model_at_purchase=(
            sales_model_value
        ),
        gross_amount=gross_amount,
        commission_amount=commission_amount,
        net_amount=net_amount,
        commission_percent_at_purchase=(
            commission_percent
        ),
        status=OrderStatus.PENDING,
        phone_number=normalized_phone,
    )

    db.add(order)

    # --------------------------------------------------------------
    # 8. FLUSH ORDER
    # --------------------------------------------------------------

    try:

        db.flush()

    except Exception:

        db.rollback()

        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error=(
                    "We could not create your order. "
                    "Please try again."
                ),
            ),
            status_code=400,
        )

    # --------------------------------------------------------------
    # 9. M-PESA STK PUSH
    # --------------------------------------------------------------

    try:

        stk_response = (
            await mpesa.initiate_stk_push(
                phone_number=normalized_phone,
                amount=int(
                    gross_amount
                ),
                account_reference=(
                    order.order_number
                ),
                transaction_desc=(
                    f"BeatHub: {track.title}"
                ),
            )
        )

    except mpesa.MpesaError as exc:

        order.status = (
            OrderStatus.FAILED
        )

        db.commit()

        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error=str(exc),
            ),
            status_code=400,
        )

    except Exception:

        order.status = (
            OrderStatus.FAILED
        )

        db.commit()

        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error=(
                    "M-Pesa could not be started. "
                    "Please check the phone number "
                    "and try again."
                ),
            ),
            status_code=400,
        )

    # --------------------------------------------------------------
    # 10. VALIDATE M-PESA RESPONSE
    # --------------------------------------------------------------

    if not isinstance(
        stk_response,
        dict,
    ):

        order.status = (
            OrderStatus.FAILED
        )

        db.commit()

        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error=(
                    "M-Pesa returned an invalid response."
                ),
            ),
            status_code=400,
        )

    merchant_request_id = (
        stk_response.get(
            "MerchantRequestID"
        )
    )

    checkout_request_id = (
        stk_response.get(
            "CheckoutRequestID"
        )
    )

    # --------------------------------------------------------------
    # SAFARICOM MAY RETURN ERROR INFORMATION
    # --------------------------------------------------------------

    if not checkout_request_id:

        order.status = (
            OrderStatus.FAILED
        )

        db.commit()

        error_message = (
            stk_response.get(
                "errorMessage"
            )
            or stk_response.get(
                "CustomerMessage"
            )
            or (
                "M-Pesa did not provide a "
                "CheckoutRequestID."
            )
        )

        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error=error_message,
            ),
            status_code=400,
        )

    # --------------------------------------------------------------
    # 11. SAVE PAYMENT TRANSACTION
    # --------------------------------------------------------------

    payment = PaymentTransaction(
        order_id=order.id,
        merchant_request_id=(
            merchant_request_id
        ),
        checkout_request_id=(
            checkout_request_id
        ),
        phone_number=normalized_phone,
        amount=gross_amount,
        status=PaymentStatus.PENDING,
    )

    db.add(payment)

    try:

        db.commit()

    except Exception:

        db.rollback()

        # We already initiated the STK push. We cannot safely
        # pretend the payment was never initiated.
        raise HTTPException(
            status_code=500,
            detail=(
                "The M-Pesa request was started, "
                "but the payment record could not "
                "be saved. Please contact BeatHub support."
            ),
        )

    # --------------------------------------------------------------
    # 12. BUYER PAYMENT STATUS
    # --------------------------------------------------------------

    return RedirectResponse(
        url=(
            f"/orders/{order.id}/status"
        ),
        status_code=303,
    )


# ======================================================================
# ORDER STATUS PAGE
# ======================================================================

@router.get(
    "/orders/{order_id}/status"
)
def order_status_page(
    order_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    order = db.get(
        Order,
        order_id,
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    if order.buyer_id != user.id:

        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    return templates.TemplateResponse(
        request,
        "order_status.html",
        page_context(
            request,
            user,
            order=order,
        ),
    )


# ======================================================================
# ORDER STATUS API
# ======================================================================

@router.get(
    "/api/orders/{order_id}/status"
)
def order_status_api(
    order_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    order = db.get(
        Order,
        order_id,
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    if order.buyer_id != user.id:

        raise HTTPException(
            status_code=404,
            detail="Order not found.",
        )

    track_slug = None

    if order.track:

        track_slug = (
            order.track.slug
        )

    return {
        "status": order.status.value,
        "order_number": order.order_number,
        "track_slug": track_slug,
        "completed": (
            order.status
            == OrderStatus.COMPLETED
        ),
        "failed": (
            order.status
            == OrderStatus.FAILED
        ),
        "rejected": (
            order.status
            == OrderStatus.REJECTED
        ),
    }
