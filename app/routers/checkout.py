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

templates = Jinja2Templates(directory="app/templates")


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
# TRACK AVAILABILITY
# ======================================================================
#
# DO NOT use track.is_available.
#
# Track has:
#
#   is_published
#   is_sold
#   sales_model
#
# Availability is calculated from those real fields.
#
# Rules:
#
# NON-EXCLUSIVE:
#     published = available
#     even if is_sold == True
#
# EXCLUSIVE:
#     published AND is_sold == False = available
#     published AND is_sold == True  = unavailable
# ======================================================================

def track_is_available(track: Track) -> bool:
    if not track:
        return False

    if not bool(getattr(track, "is_published", False)):
        return False

    sales_model = getattr(track, "sales_model", None)

    sales_model_value = getattr(
        sales_model,
        "value",
        str(sales_model or ""),
    )

    sales_model_value = str(
        sales_model_value
    ).strip().lower()

    is_exclusive = (
        sales_model_value == SalesModel.EXCLUSIVE.value
    )

    if is_exclusive:
        return not bool(
            getattr(track, "is_sold", False)
        )

    # Non-exclusive tracks can be sold repeatedly.
    return True


# ======================================================================
# FIND TRACK
# ======================================================================

def get_track(
    db: Session,
    slug: str,
) -> Track | None:
    return (
        db.query(Track)
        .filter(Track.slug == slug)
        .first()
    )


# ======================================================================
# CHECKOUT PAGE
# ======================================================================

@router.get("/checkout/track/{slug}")
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
    # Creator cannot purchase their own track.
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
    # Server-side availability check.
    # --------------------------------------------------------------

    if not track_is_available(track):
        return RedirectResponse(
            url=(
                f"/track/{track.slug}"
                "?error=This track is no longer available for purchase."
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
# START CHECKOUT / M-PESA STK PUSH
# ======================================================================

@router.post("/checkout/track/{slug}")
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
    # 2. PREVENT CREATOR FROM BUYING OWN TRACK
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
    # 3. SERVER IS THE AUTHORITY ON AVAILABILITY
    # --------------------------------------------------------------

    if not track_is_available(track):
        return RedirectResponse(
            url=(
                f"/track/{track.slug}"
                "?error=This track is no longer available for purchase."
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
            detail="This track has an invalid price.",
        )

    if price <= Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail="This track cannot be purchased at its current price.",
        )

    # --------------------------------------------------------------
    # 5. CALCULATE PLATFORM SPLIT
    # --------------------------------------------------------------

    split = calculate_split(
        track.price
    )

    gross_amount = Decimal(
        str(split["gross_amount"])
    )

    commission_amount = Decimal(
        str(split["commission_amount"])
    )

    net_amount = Decimal(
        str(split["net_amount"])
    )

    commission_percent = Decimal(
        str(split["commission_percent"])
    )

    # --------------------------------------------------------------
    # 6. NORMALIZE PHONE NUMBER
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
                    "Please enter a valid Kenyan M-Pesa "
                    "phone number."
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
                    "Please enter a valid Kenyan M-Pesa "
                    "phone number."
                ),
            ),
            status_code=400,
        )

    # --------------------------------------------------------------
    # 7. CREATE ORDER
    # --------------------------------------------------------------

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
            getattr(
                track.sales_model,
                "value",
                str(track.sales_model),
            )
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
    # 8. INITIATE M-PESA STK PUSH
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
        order.status = OrderStatus.FAILED

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
        order.status = OrderStatus.FAILED

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
                    "Please check the phone number and try again."
                ),
            ),
            status_code=400,
        )

    # --------------------------------------------------------------
    # 9. VALIDATE STK RESPONSE
    # --------------------------------------------------------------

    if not isinstance(
        stk_response,
        dict,
    ):
        order.status = OrderStatus.FAILED
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

    if not checkout_request_id:
        order.status = OrderStatus.FAILED
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
    # 10. SAVE PAYMENT TRANSACTION
    # --------------------------------------------------------------

    payment = PaymentTransaction(
        order_id=order.id,
        merchant_request_id=merchant_request_id,
        checkout_request_id=checkout_request_id,
        phone_number=normalized_phone,
        amount=gross_amount,
        status=PaymentStatus.PENDING,
    )

    db.add(payment)

    db.commit()

    # --------------------------------------------------------------
    # 11. SEND BUYER TO ORDER STATUS PAGE
    # --------------------------------------------------------------

    return RedirectResponse(
        url=f"/orders/{order.id}/status",
        status_code=303,
    )


# ======================================================================
# ORDER STATUS PAGE
# ======================================================================

@router.get("/orders/{order_id}/status")
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

@router.get("/api/orders/{order_id}/status")
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
        track_slug = order.track.slug

    return {
        "status": order.status.value,
        "order_number": order.order_number,
        "track_slug": track_slug,
        "completed": (
            order.status == OrderStatus.COMPLETED
        ),
        "failed": (
            order.status == OrderStatus.FAILED
        ),
        "rejected": (
            order.status == OrderStatus.REJECTED
        ),
    }
