"""Creator-facing unified sales history.

Read-only reporting over the existing music Order records and canonical
merchandise orders. No payment, ledger, withdrawal, or ownership behavior is
changed here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order, OrderStatus
from app.models.music import Track
from app.models.user import User
from app.utils.deps import require_creator

router = APIRouter(tags=["creator-sales-history"])
templates = Jinja2Templates(directory="app/templates")

MERCH_TABLE = "beathub_merchandise"
MERCH_ORDER_TABLE = "beathub_merchandise_orders"
PER_PAGE = 20


def _money(value) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _buyer_name(user: User | None) -> str:
    if user is None:
        return "Buyer"
    return (getattr(user, "username", None) or getattr(user, "email", None) or "Buyer").strip()


def _music_sales(db: Session, profile_id: str, search: str):
    orders = (
        db.query(Order)
        .join(Track, Order.track_id == Track.id)
        .filter(
            Track.creator_profile_id == profile_id,
            Order.status == OrderStatus.COMPLETED,
        )
        .all()
    )
    result = []
    needle = search.lower().strip()
    for order in orders:
        title = getattr(order.track, "title", None) or "Track sale"
        buyer = _buyer_name(getattr(order, "buyer", None))
        haystack = f"{title} {buyer}".lower()
        if needle and needle not in haystack:
            continue
        completed = order.completed_at or order.created_at or datetime.min
        result.append({
            "id": str(order.id),
            "order_number": getattr(order, "order_number", None) or str(order.id)[:8].upper(),
            "buyer": buyer,
            "product": title,
            "type": "music",
            "type_label": "Beat / Track",
            "gross": _money(order.gross_amount),
            "commission": _money(order.commission_amount),
            "net": _money(order.net_amount),
            "status": "Completed",
            "date": completed,
            "quantity": 1,
        })
    return result


def _merch_sales(db: Session, profile_id: str, search: str):
    rows = db.execute(
        text(
            f"""
            SELECT
                o.id,
                o.product_id,
                o.buyer_id,
                o.quantity,
                o.total_amount,
                o.commission_amount,
                o.net_amount,
                o.created_at,
                o.paid_at,
                o.status,
                m.name AS product_name,
                u.username AS buyer_username,
                u.email AS buyer_email
            FROM {MERCH_ORDER_TABLE} o
            JOIN {MERCH_TABLE} m ON m.id = o.product_id
            LEFT JOIN users u ON u.id = o.buyer_id
            WHERE m.creator_profile_id = :profile_id
              AND o.status = 'paid'
            ORDER BY COALESCE(o.paid_at, o.created_at) DESC
            """
        ),
        {"profile_id": str(profile_id)},
    ).mappings().all()

    result = []
    needle = search.lower().strip()
    for row in rows:
        buyer = str(row["buyer_username"] or row["buyer_email"] or "Buyer").strip()
        product = str(row["product_name"] or "Merchandise")
        if needle and needle not in f"{product} {buyer}".lower():
            continue
        completed = row["paid_at"] or row["created_at"] or datetime.min
        result.append({
            "id": str(row["id"]),
            "order_number": f"BM-{str(row['id'])[:8].upper()}",
            "buyer": buyer,
            "product": product,
            "type": "merchandise",
            "type_label": "Merchandise",
            "gross": _money(row["total_amount"]),
            "commission": _money(row["commission_amount"]),
            "net": _money(row["net_amount"]),
            "status": "Paid",
            "date": completed,
            "quantity": int(row["quantity"] or 1),
        })
    return result


def build_sales_history(db: Session, profile_id: str, page: int = 1, sale_type: str = "all", search: str = ""):
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    sale_type = (sale_type or "all").lower().strip()
    if sale_type not in {"all", "music", "merchandise"}:
        sale_type = "all"
    search = (search or "").strip()[:100]

    sales = []
    if sale_type in {"all", "music"}:
        sales.extend(_music_sales(db, profile_id, search))
    if sale_type in {"all", "merchandise"}:
        sales.extend(_merch_sales(db, profile_id, search))

    sales.sort(key=lambda item: item["date"] or datetime.min, reverse=True)
    total_count = len(sales)
    total_gross = sum((item["gross"] for item in sales), Decimal("0"))
    total_commission = sum((item["commission"] for item in sales), Decimal("0"))
    total_net = sum((item["net"] for item in sales), Decimal("0"))
    total_pages = max(1, (total_count + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    start = (page - 1) * PER_PAGE
    visible = sales[start : start + PER_PAGE]

    return {
        "sales": visible,
        "total_count": total_count,
        "total_gross": total_gross,
        "total_commission": total_commission,
        "total_net": total_net,
        "page": page,
        "total_pages": total_pages,
        "per_page": PER_PAGE,
        "search": search,
        "sale_type": sale_type,
    }


@router.get("/dashboard/sales-history")
def creator_sales_history(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
    page: int = 1,
    sale_type: str = "all",
    q: str = "",
):
    profile = getattr(user, "profile", None)
    if profile is None:
        raise HTTPException(status_code=400, detail="Creator profile missing.")

    history = build_sales_history(db, str(profile.id), page, sale_type, q)
    return templates.TemplateResponse(
        request,
        "creator_sales_history.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "profile": profile,
            **history,
        },
    )
