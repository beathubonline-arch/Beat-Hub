from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.models.order import Order, OrderStatus
from app.services.payout_policy import is_payout_window
from app.utils.deps import require_admin


router = APIRouter(prefix="/admin/withdrawals", tags=["admin-payouts"])
templates = Jinja2Templates(directory="app/templates")


def _redirect(message: str, error: bool = False):
    key = "error" if error else "success"
    return RedirectResponse(
        url=f"/admin/withdrawals?{key}={quote(message)}",
        status_code=303,
    )


def _withdrawal_or_404(withdrawal_id: str, db: Session):
    withdrawal = (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.id == withdrawal_id)
        .first()
    )
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Withdrawal request not found.")
    return withdrawal


@router.get("")
def creator_withdrawals_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Canonical creator-payout page, registered before the legacy admin GET route."""
    withdrawals = (
        db.query(WithdrawalRequest)
        .order_by(WithdrawalRequest.created_at.desc())
        .all()
    )

    pending_amount_raw = (
        db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0))
        .filter(WithdrawalRequest.status == "pending")
        .scalar()
    )

    completed_sales = (
        db.query(Order)
        .filter(Order.status == OrderStatus.COMPLETED)
        .all()
    )
    creator_earnings = sum(
        (Decimal(str(order.net_amount or 0)) for order in completed_sales),
        Decimal("0"),
    )
    commission = sum(
        (Decimal(str(order.commission_amount or 0)) for order in completed_sales),
        Decimal("0"),
    )

    return templates.TemplateResponse(
        request,
        "admin/withdrawals.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "withdrawals": withdrawals,
            "pending_withdrawal_amount": Decimal(str(pending_amount_raw or 0)),
            "total_creator_earnings": creator_earnings,
            "total_commission": commission,
        },
    )


@router.post("/{withdrawal_id}/approve-confirm")
def approve_creator_withdrawal_confirmed(
    withdrawal_id: str,
    amount: str = Form(...),
    phone_number: str = Form(...),
    payout_method: str = Form("mpesa"),
    admin_note: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Approve a creator request only after the admin confirms amount and destination."""
    withdrawal = _withdrawal_or_404(withdrawal_id, db)

    if str(withdrawal.status).lower() != "pending":
        return _redirect("Only pending creator withdrawals can be approved.", error=True)

    method = (payout_method or "mpesa").strip().lower()
    if method != "mpesa":
        return _redirect("The current BeatHub creator payout rail is M-Pesa. Select M-Pesa.", error=True)

    try:
        approved_amount = Decimal((amount or "").strip())
    except (InvalidOperation, ValueError):
        return _redirect("Enter a valid payout amount.", error=True)

    if not approved_amount.is_finite() or approved_amount <= 0:
        return _redirect("Payout amount must be greater than zero.", error=True)

    requested_amount = Decimal(str(withdrawal.amount or 0))
    if approved_amount != requested_amount:
        return _redirect(
            f"Approval amount must match the producer request of KSh {requested_amount:.2f}. Reject the request if the amount needs correction.",
            error=True,
        )

    phone = (phone_number or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("254") and len(digits) == 12:
        normalized_phone = digits
    elif digits.startswith("0") and len(digits) == 10:
        normalized_phone = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        normalized_phone = "254" + digits
    else:
        return _redirect("Enter a valid Kenyan M-Pesa number.", error=True)

    withdrawal.amount = approved_amount
    withdrawal.phone_number = normalized_phone
    withdrawal.status = "approved"
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = (
        (admin_note or "").strip()
        or "Withdrawal approved by administrator after amount and M-Pesa destination confirmation."
    )
    db.commit()

    return _redirect("Creator withdrawal approved with confirmed amount and M-Pesa destination.")


@router.post("/{withdrawal_id}/processing")
def mark_creator_withdrawal_processing(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    if not is_payout_window():
        return _redirect(
            "Creator payouts can only be moved to processing on Tuesday or Thursday before 6:00 PM EAT.",
            error=True,
        )

    withdrawal = _withdrawal_or_404(withdrawal_id, db)

    if str(withdrawal.status).lower() != "approved":
        return _redirect("Only approved creator withdrawals can be moved to processing.", error=True)

    withdrawal.status = "processing"
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = "Withdrawal moved to processing for scheduled M-Pesa payout."
    db.commit()

    return _redirect("Creator withdrawal moved to processing.")


@router.post("/{withdrawal_id}/paid")
def mark_creator_withdrawal_paid(
    withdrawal_id: str,
    payout_reference: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    if not is_payout_window():
        return _redirect(
            "Creator payouts can only be completed on Tuesday or Thursday before 6:00 PM EAT.",
            error=True,
        )

    reference = (payout_reference or "").strip()
    if not reference:
        return _redirect("Enter the M-Pesa payout reference before marking the withdrawal paid.", error=True)

    withdrawal = _withdrawal_or_404(withdrawal_id, db)

    if str(withdrawal.status).lower() not in ("approved", "processing"):
        return _redirect("Only approved or processing creator withdrawals can be marked paid.", error=True)

    withdrawal.status = "paid"
    withdrawal.payout_reference = reference[:100]
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.resolved_at = datetime.utcnow()
    withdrawal.admin_note = "M-Pesa payout completed and reference recorded."
    db.commit()

    return _redirect("Creator withdrawal marked as paid.")
