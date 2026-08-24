import logging
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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
logger = logging.getLogger("beathub.checkout")


def page_context(request: Request, user: User, **extra):
    context = {
        "request": request,
        "current_user": user,
        "current_year": datetime.utcnow().year,
    }
    context.update(extra)
    return context


def track_sales_model_value(track: Track) -> str:
    sales_model = getattr(track, "sales_model", None)
    value = getattr(sales_model, "value", sales_model)
    return str(value or "").strip().lower()


def track_is_available(track: Track) -> bool:
    if not track or not bool(getattr(track, "is_published", False)):
        return False

    sales_model = track_sales_model_value(track)

    if sales_model == SalesModel.NON_EXCLUSIVE.value:
        return True

    if sales_model == SalesModel.EXCLUSIVE.value:
        return not bool(getattr(track, "is_sold", False))

    return False


def availability_error(track: Track) -> str:
    if not track:
        return "Track not found."

    if not bool(getattr(track, "is_published", False)):
        return "This track is not currently available for purchase."

    sales_model = track_sales_model_value(track)

    if (
        sales_model == SalesModel.EXCLUSIVE.value
        and bool(getattr(track, "is_sold", False))
    ):
        return "This exclusive track has already been sold."

    if sales_model not in (
        SalesModel.NON_EXCLUSIVE.value,
        SalesModel.EXCLUSIVE.value,
    ):
        return "This track has an invalid sales model."

    return "This track is no longer available for purchase."


def get_track(db: Session, slug: str) -> Track | None:
    return db.query(Track).filter(Track.slug == slug).first()


@router.get("/checkout/track/{slug}")
def checkout_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    track = get_track(db, slug)

    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")

    profile = getattr(track, "creator_profile", None)
    creator_user_id = getattr(profile, "user_id", None)

    if creator_user_id == user.id:
        return RedirectResponse(
            url=f"/track/{track.slug}?error=You cannot purchase your own track.",
            status_code=303,
        )

    if not track_is_available(track):
        return RedirectResponse(
            url=(
                f"/track/{track.slug}?error="
                "This%20track%20is%20no%20longer%20available%20for%20purchase."
            ),
            status_code=303,
        )

    return templates.TemplateResponse(
        request,
        "checkout.html",
        page_context(request, user, track=track),
    )


@router.post("/checkout/track/{slug}")
async def checkout_submit(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    phone_number: str = Form(...),
):
    track = get_track(db, slug)

    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")

    profile = getattr(track, "creator_profile", None)
    creator_user_id = getattr(profile, "user_id", None)

    if creator_user_id == user.id:
        return RedirectResponse(
            url=f"/track/{track.slug}?error=You cannot purchase your own track.",
            status_code=303,
        )

    if not track_is_available(track):
        return RedirectResponse(
            url=(
                f"/track/{track.slug}?error="
                "This%20track%20is%20no%20longer%20available%20for%20purchase."
            ),
            status_code=303,
        )

    try:
        price = Decimal(str(track.price))
    except Exception:
        raise HTTPException(status_code=500, detail="This track has an invalid price.")

    if price <= Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail="This track cannot be purchased at its current price.",
        )

    split = calculate_split(track.price)
    gross_amount = Decimal(str(split["gross_amount"]))
    commission_amount = Decimal(str(split["commission_amount"]))
    net_amount = Decimal(str(split["net_amount"]))
    commission_percent = Decimal(str(split["commission_percent"]))

    try:
        normalized_phone = mpesa.normalize_phone_number(phone_number)
    except Exception:
        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error="Please enter a valid Kenyan M-Pesa phone number.",
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
                error="Please enter a valid Kenyan M-Pesa phone number.",
            ),
            status_code=400,
        )

    order = Order(
        id=str(uuid.uuid4()),
        order_number=f"BH{uuid.uuid4().hex[:10].upper()}",
        buyer_id=user.id,
        track_id=track.id,
        album_id=None,
        sales_model_at_purchase=track_sales_model_value(track),
        gross_amount=gross_amount,
        commission_amount=commission_amount,
        net_amount=net_amount,
        commission_percent_at_purchase=commission_percent,
        status=OrderStatus.PENDING,
        phone_number=normalized_phone,
    )
    db.add(order)

    try:
        db.flush()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Checkout order creation failed: track=%s user=%s", slug, user.id)
        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error="We could not create your order. Please try again.",
            ),
            status_code=400,
        )

    try:
        stk_response = await mpesa.initiate_stk_push(
            phone_number=normalized_phone,
            amount=int(gross_amount),
            account_reference=order.order_number,
            transaction_desc=f"BeatHub: {track.title}",
        )
    except mpesa.MpesaError as exc:
        order.status = OrderStatus.FAILED
        db.commit()
        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(request, user, track=track, error=str(exc)),
            status_code=400,
        )
    except Exception:
        logger.exception("Unexpected M-Pesa error: order=%s", order.order_number)
        order.status = OrderStatus.FAILED
        db.commit()
        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error="M-Pesa could not be started. Please check the phone number and try again.",
            ),
            status_code=400,
        )

    if not isinstance(stk_response, dict):
        order.status = OrderStatus.FAILED
        db.commit()
        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(request, user, track=track, error="M-Pesa returned an invalid response."),
            status_code=400,
        )

    merchant_request_id = stk_response.get("MerchantRequestID")
    checkout_request_id = stk_response.get("CheckoutRequestID")

    if not checkout_request_id:
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
                    stk_response.get("errorMessage")
                    or stk_response.get("CustomerMessage")
                    or "M-Pesa did not provide a CheckoutRequestID."
                ),
            ),
            status_code=400,
        )

    payment = PaymentTransaction(
        order_id=order.id,
        merchant_request_id=merchant_request_id,
        checkout_request_id=checkout_request_id,
        phone_number=normalized_phone,
        amount=gross_amount,
        status=PaymentStatus.PENDING,
    )
    db.add(payment)

    try:
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception(
            "Checkout payment integrity failure: order=%s checkout_request_id=%s",
            order.order_number,
            checkout_request_id,
        )
        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error="M-Pesa was started, but the BeatHub payment record could not be saved.",
            ),
            status_code=400,
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Checkout payment database failure: order=%s checkout_request_id=%s",
            order.order_number,
            checkout_request_id,
        )
        return templates.TemplateResponse(
            request,
            "checkout.html",
            page_context(
                request,
                user,
                track=track,
                error="M-Pesa was started, but the BeatHub payment record could not be saved.",
            ),
            status_code=400,
        )

    return RedirectResponse(
        url=f"/orders/{order.id}/status",
        status_code=303,
    )


@router.get("/orders/{order_id}/status")
def order_status_page(
    order_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    order = db.get(Order, order_id)

    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    if order.buyer_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found.")

    return templates.TemplateResponse(
        request,
        "order_status.html",
        page_context(request, user, order=order),
    )


@router.get("/api/orders/{order_id}/status")
def order_status_api(
    order_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    order = db.get(Order, order_id)

    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    if order.buyer_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found.")

    track_slug = order.track.slug if order.track else None

    return {
        "status": order.status.value,
        "order_number": order.order_number,
        "track_slug": track_slug,
        "completed": order.status == OrderStatus.COMPLETED,
        "failed": order.status == OrderStatus.FAILED,
        "rejected": order.status == OrderStatus.REJECTED,
    }
