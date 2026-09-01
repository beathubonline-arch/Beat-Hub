"""Customer checkout pages and read-only Paystack order status."""

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.music import SalesModel, Track
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.utils.deps import get_optional_user, require_user

router = APIRouter(tags=["checkout"])
templates = Jinja2Templates(directory="app/templates")


def page_context(request: Request, user: User, **extra):
    context = {"request": request, "current_user": user, "current_year": datetime.utcnow().year}
    context.update(extra)
    return context


def track_sales_model_value(track: Track) -> str:
    sales_model = getattr(track, "sales_model", None)
    return str(getattr(sales_model, "value", sales_model) or "").strip().lower()


def track_is_available(track: Track) -> bool:
    if not track or not bool(getattr(track, "is_published", False)):
        return False
    sales_model = track_sales_model_value(track)
    if sales_model == SalesModel.NON_EXCLUSIVE.value:
        return True
    if sales_model == SalesModel.EXCLUSIVE.value:
        return not bool(getattr(track, "is_sold", False))
    return False


def get_track(db: Session, slug: str) -> Track | None:
    return db.query(Track).filter(Track.slug == slug).first()


@router.get("/checkout/track/{slug}")
def checkout_page(slug: str, request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    track = get_track(db, slug)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found.")
    if user is None:
        next_url = f"/checkout/track/{quote(slug, safe='')}"
        message = quote("Please log in to purchase this beat. After you sign in, your checkout will continue automatically.")
        return RedirectResponse(url=f"/login?next={quote(next_url, safe='')}&error={message}", status_code=303)
    profile = getattr(track, "creator_profile", None)
    creator_user_id = getattr(profile, "user_id", None)
    if creator_user_id == user.id:
        return templates.TemplateResponse(request, "checkout.html", page_context(request, user, track=track, error="You cannot purchase your own track."), status_code=400)
    if not track_is_available(track):
        return templates.TemplateResponse(request, "checkout.html", page_context(request, user, track=track, error="This track is no longer available for purchase."), status_code=400)
    return templates.TemplateResponse(request, "checkout.html", page_context(request, user, track=track))


@router.get("/orders/{order_id}/status")
def order_status_page(order_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    order = db.get(Order, order_id)
    if not order or order.buyer_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found.")
    return templates.TemplateResponse(request, "order_status.html", page_context(request, user, order=order))


@router.get("/api/orders/{order_id}/status")
def order_status_api(order_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Return persisted order state only.

    Payment verification and fulfillment have one canonical path in
    ``paystack_checkout``: the Paystack return callback verifies the reference
    and calls the shared finalizer, while the signed webhook provides the
    provider-retry path. This endpoint must never mutate payment/order state.
    """
    order = db.get(Order, order_id)
    if not order or order.buyer_id != user.id:
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
