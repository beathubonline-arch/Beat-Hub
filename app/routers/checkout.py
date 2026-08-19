import uuid
from datetime import datetime

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


@router.get("/checkout/track/{slug}")
def checkout_page(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    track = db.query(Track).filter(Track.slug == slug).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.is_available:
        return RedirectResponse(url=f"/track/{slug}?error=This item is no longer available for purchase.", status_code=303)

    return templates.TemplateResponse(request, 
        "checkout.html",
        {"request": request, "current_user": user, "current_year": datetime.utcnow().year, "track": track},
    )


@router.post("/checkout/track/{slug}")
async def checkout_submit(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    phone_number: str = Form(...),
):
    track = db.query(Track).filter(Track.slug == slug).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # Server is the sole authority on availability and price — never trust the client.
    if not track.is_available:
        return RedirectResponse(url=f"/track/{slug}?error=This item is no longer available for purchase.", status_code=303)

    split = calculate_split(track.price)

    order = Order(
        id=str(uuid.uuid4()),
        order_number=f"BH{uuid.uuid4().hex[:10].upper()}",
        buyer_id=user.id,
        track_id=track.id,
        sales_model_at_purchase=track.sales_model.value,
        gross_amount=split["gross_amount"],
        commission_amount=split["commission_amount"],
        net_amount=split["net_amount"],
        commission_percent_at_purchase=split["commission_percent"],
        status=OrderStatus.PENDING,
        phone_number=phone_number,
    )
    db.add(order)
    db.flush()

    try:
        stk_response = await mpesa.initiate_stk_push(
            phone_number=phone_number,
            amount=int(split["gross_amount"]),
            account_reference=order.order_number,
            transaction_desc=f"BeatHub: {track.title}",
        )
    except mpesa.MpesaError as exc:
        order.status = OrderStatus.FAILED
        db.commit()
        return templates.TemplateResponse(request, 
            "checkout.html",
            {
                "request": request, "current_user": user, "current_year": datetime.utcnow().year,
                "track": track, "error": str(exc),
            },
            status_code=400,
        )

    payment = PaymentTransaction(
        order_id=order.id,
        merchant_request_id=stk_response.get("MerchantRequestID"),
        checkout_request_id=stk_response.get("CheckoutRequestID"),
        phone_number=mpesa.normalize_phone_number(phone_number),
        amount=split["gross_amount"],
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    db.commit()

    return RedirectResponse(url=f"/orders/{order.id}/status", status_code=303)


@router.get("/orders/{order_id}/status")
def order_status_page(order_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    order = db.get(Order, order_id)
    if not order or order.buyer_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found")

    return templates.TemplateResponse(request, 
        "order_status.html",
        {"request": request, "current_user": user, "current_year": datetime.utcnow().year, "order": order},
    )


@router.get("/api/orders/{order_id}/status")
def order_status_api(order_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Lightweight polling endpoint used by the order-status page to auto-refresh."""
    order = db.get(Order, order_id)
    if not order or order.buyer_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "status": order.status.value,
        "order_number": order.order_number,
        "track_slug": order.track.slug if order.track else None,
    }
