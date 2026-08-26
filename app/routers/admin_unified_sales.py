from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.utils.deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin-sales"])
templates = Jinja2Templates(directory="app/templates")


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


@router.get("/sales")
def unified_sales(
    request: Request,
    status: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Unified BeatHub financial transaction ledger.

    Combines completed/pending/failed music orders with merchandise orders
    without modifying either source table. Financial totals include only
    settled/completed transactions.
    """
    normalized = (status or "").strip().lower()
    valid_statuses = {"completed", "pending", "failed", "rejected"}
    if normalized not in valid_statuses:
        normalized = ""

    music_query = db.query(Order).order_by(Order.created_at.desc()).all()
    music_rows = []
    for order in music_query:
        state = str(getattr(order.status, "value", order.status) or "").lower()
        if normalized and state != normalized:
            continue
        music_rows.append({
            "type": "Beat",
            "id": str(order.id),
            "customer": getattr(order, "customer_email", None) or getattr(getattr(order, "user", None), "email", None) or "—",
            "item": getattr(getattr(order, "track", None), "title", None) or getattr(order, "track_title", None) or "Beat purchase",
            "quantity": 1,
            "gross": _dec(getattr(order, "gross_amount", 0)),
            "commission": _dec(getattr(order, "commission_amount", 0)),
            "net": _dec(getattr(order, "net_amount", 0)),
            "status": state,
            "reference": getattr(order, "payment_reference", None) or getattr(order, "transaction_id", None) or "—",
            "created_at": getattr(order, "created_at", None),
        })

    merch_sql = text("""
        SELECT
            o.id,
            o.quantity,
            o.total_amount,
            o.commission_amount,
            o.net_amount,
            o.status,
            o.payment_provider,
            o.merchant_request_id,
            o.checkout_request_id,
            o.mpesa_receipt,
            o.created_at,
            o.paid_at,
            p.name AS product_name,
            u.email AS customer_email
        FROM beathub_merchandise_orders o
        LEFT JOIN beathub_merchandise p ON p.id = o.product_id
        LEFT JOIN users u ON u.id = o.buyer_id
        ORDER BY o.created_at DESC
    """)

    merch_rows = []
    try:
        raw_merch = db.execute(merch_sql).mappings().all()
    except Exception:
        raw_merch = []
        db.rollback()

    for row in raw_merch:
        raw_status = str(row.get("status") or "").lower()
        if raw_status == "paid":
            display_status = "completed"
        elif raw_status in {"pending_payment", "pending"}:
            display_status = "pending"
        elif raw_status in {"failed", "cancelled", "reversed"}:
            display_status = "failed"
        else:
            display_status = raw_status or "pending"

        if normalized and display_status != normalized:
            continue

        reference = (
            row.get("mpesa_receipt")
            or row.get("checkout_request_id")
            or row.get("merchant_request_id")
            or "—"
        )
        merch_rows.append({
            "type": "Merch",
            "id": str(row["id"]),
            "customer": row.get("customer_email") or "—",
            "item": row.get("product_name") or "Merchandise",
            "quantity": int(row.get("quantity") or 1),
            "gross": _dec(row.get("total_amount")),
            "commission": _dec(row.get("commission_amount")),
            "net": _dec(row.get("net_amount")),
            "status": display_status,
            "reference": reference,
            "created_at": row.get("paid_at") or row.get("created_at"),
        })

    rows = sorted(
        music_rows + merch_rows,
        key=lambda item: item["created_at"] or datetime.min,
        reverse=True,
    )

    completed_music = [r for r in music_rows if r["status"] == "completed"]
    completed_merch = [r for r in merch_rows if r["status"] == "completed"]
    settled = completed_music + completed_merch

    totals = {
        "gross": sum((r["gross"] for r in settled), Decimal("0")),
        "commission": sum((r["commission"] for r in settled), Decimal("0")),
        "net": sum((r["net"] for r in settled), Decimal("0")),
        "count": len(settled),
        "music_count": len(completed_music),
        "merch_count": len(completed_merch),
    }

    return templates.TemplateResponse(
        request,
        "admin/sales_unified.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "rows": rows,
            "totals": totals,
            "status": normalized,
        },
    )
