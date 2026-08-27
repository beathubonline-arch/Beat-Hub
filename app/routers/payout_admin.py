from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import AdminWithdrawal, WithdrawalRequest
from app.models.order import Order, OrderStatus
from app.services.payout_policy import is_payout_window
from app.services.paystack_transfers import PaystackTransferError, initiate_mpesa_transfer, verify_transfer
from app.utils.deps import require_admin


router = APIRouter(prefix="/admin/withdrawals", tags=["admin-payouts"])
templates = Jinja2Templates(directory="app/templates")


def _redirect(path: str, message: str, error: bool = False):
    key = "error" if error else "success"
    return RedirectResponse(url=f"{path}?{key}={quote(message)}", status_code=303)


def _creator_redirect(message: str, error: bool = False):
    return _redirect("/admin/withdrawals", message, error)


def _admin_redirect(message: str, error: bool = False):
    return _redirect("/admin/withdraw", message, error)


def _withdrawal_or_404(withdrawal_id: str, db: Session):
    withdrawal = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Withdrawal request not found.")
    return withdrawal


def _admin_withdrawal_or_404(withdrawal_id: str, db: Session):
    withdrawal = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal_id).first()
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Admin withdrawal not found.")
    return withdrawal


# ---------------------------------------------------------------------------
# CREATOR WITHDRAWALS
# ---------------------------------------------------------------------------

@router.get("")
def creator_withdrawals_page(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    withdrawals = db.query(WithdrawalRequest).order_by(WithdrawalRequest.created_at.desc()).all()
    pending_amount_raw = db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).filter(WithdrawalRequest.status == "pending").scalar()
    completed_sales = db.query(Order).filter(Order.status == OrderStatus.COMPLETED).all()
    creator_earnings = sum((Decimal(str(order.net_amount or 0)) for order in completed_sales), Decimal("0"))
    commission = sum((Decimal(str(order.commission_amount or 0)) for order in completed_sales), Decimal("0"))
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
    withdrawal = _withdrawal_or_404(withdrawal_id, db)
    if str(withdrawal.status).lower() != "pending":
        return _creator_redirect("Only pending creator withdrawals can be approved.", True)
    if (payout_method or "mpesa").strip().lower() != "mpesa":
        return _creator_redirect("The current BeatHub creator payout rail is M-Pesa.", True)
    try:
        approved_amount = Decimal((amount or "").strip())
    except (InvalidOperation, ValueError):
        return _creator_redirect("Enter a valid payout amount.", True)
    if not approved_amount.is_finite() or approved_amount <= 0:
        return _creator_redirect("Payout amount must be greater than zero.", True)
    requested_amount = Decimal(str(withdrawal.amount or 0))
    if approved_amount != requested_amount:
        return _creator_redirect(f"Approval amount must match the producer request of KSh {requested_amount:.2f}.", True)
    phone = "".join(ch for ch in (phone_number or "") if ch.isdigit())
    if phone.startswith("254") and len(phone) == 12:
        normalized_phone = phone
    elif phone.startswith("0") and len(phone) == 10:
        normalized_phone = "254" + phone[1:]
    elif phone.startswith("7") and len(phone) == 9:
        normalized_phone = "254" + phone
    else:
        return _creator_redirect("Enter a valid Kenyan M-Pesa number.", True)
    withdrawal.amount = approved_amount
    withdrawal.phone_number = normalized_phone
    withdrawal.status = "approved"
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = (admin_note or "").strip() or "Withdrawal approved after amount and M-Pesa destination confirmation."
    db.commit()
    return _creator_redirect("Creator withdrawal approved with confirmed amount and M-Pesa destination.")


@router.post("/{withdrawal_id}/processing")
def mark_creator_withdrawal_processing(withdrawal_id: str, db: Session = Depends(get_db), user=Depends(require_admin)):
    if not is_payout_window():
        return _creator_redirect("Creator payouts can only be moved to processing on Tuesday or Thursday before 6:00 PM EAT.", True)
    withdrawal = _withdrawal_or_404(withdrawal_id, db)
    if str(withdrawal.status).lower() != "approved":
        return _creator_redirect("Only approved creator withdrawals can be moved to processing.", True)
    withdrawal.status = "processing"
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = "Withdrawal moved to processing for scheduled M-Pesa payout."
    db.commit()
    return _creator_redirect("Creator withdrawal moved to processing.")


@router.post("/{withdrawal_id}/paid")
def mark_creator_withdrawal_paid(withdrawal_id: str, payout_reference: str = Form(...), db: Session = Depends(get_db), user=Depends(require_admin)):
    if not is_payout_window():
        return _creator_redirect("Creator payouts can only be completed on Tuesday or Thursday before 6:00 PM EAT.", True)
    reference = (payout_reference or "").strip()
    if not reference:
        return _creator_redirect("Enter the M-Pesa payout reference before marking the withdrawal paid.", True)
    withdrawal = _withdrawal_or_404(withdrawal_id, db)
    if str(withdrawal.status).lower() not in ("approved", "processing"):
        return _creator_redirect("Only approved or processing creator withdrawals can be marked paid.", True)
    withdrawal.status = "paid"
    withdrawal.payout_reference = reference[:100]
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.resolved_at = datetime.utcnow()
    withdrawal.admin_note = "M-Pesa payout completed and reference recorded."
    db.commit()
    return _creator_redirect("Creator withdrawal marked as paid.")


# ---------------------------------------------------------------------------
# BEATHUB'S OWN PLATFORM WITHDRAWALS
# ---------------------------------------------------------------------------

@router.get("/../withdraw")
def _unused_admin_withdraw_alias():
    # This route is never used; the canonical /admin/withdraw route below is
    # injected into the legacy admin router before application startup.
    raise HTTPException(status_code=404)


def _admin_financials(db: Session):
    revenue_raw = db.query(func.coalesce(func.sum(Order.commission_amount), 0)).filter(Order.status == OrderStatus.COMPLETED).scalar()
    withdrawn_raw = db.query(func.coalesce(func.sum(AdminWithdrawal.amount), 0)).filter(AdminWithdrawal.status.in_(["approved", "processing", "paid"])).scalar()
    pending_raw = db.query(func.coalesce(func.sum(AdminWithdrawal.amount), 0)).filter(AdminWithdrawal.status == "pending").scalar()
    revenue = Decimal(str(revenue_raw or 0))
    withdrawn = Decimal(str(withdrawn_raw or 0))
    pending = Decimal(str(pending_raw or 0))
    available = max(Decimal("0"), revenue - withdrawn - pending)
    return revenue, withdrawn, pending, available


@router.get("/admin-page")
def admin_withdraw_page(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    revenue, withdrawn, pending, available = _admin_financials(db)
    withdrawals = db.query(AdminWithdrawal).order_by(AdminWithdrawal.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "admin/withdraw.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "platform_revenue": revenue,
            "already_withdrawn": withdrawn,
            "pending_admin": pending,
            "available_balance": available,
            "withdrawals": withdrawals,
            "admin_withdrawals": withdrawals,
        },
    )


@router.post("/admin-create")
def create_admin_withdrawal(
    amount: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(""),
    admin_note: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    try:
        amount_val = Decimal((amount or "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return _admin_redirect("Invalid withdrawal amount.", True)
    if not amount_val.is_finite() or amount_val <= 0:
        return _admin_redirect("Amount must be greater than zero.", True)
    phone = "".join(ch for ch in (phone_number or "") if ch.isdigit())
    if phone.startswith("254") and len(phone) == 12:
        normalized_phone = phone
    elif phone.startswith("0") and len(phone) == 10:
        normalized_phone = "254" + phone[1:]
    elif phone.startswith("7") and len(phone) == 9:
        normalized_phone = "254" + phone
    else:
        return _admin_redirect("Enter a valid Kenyan M-Pesa number.", True)
    _revenue, _withdrawn, _pending, available = _admin_financials(db)
    if amount_val > available:
        return _admin_redirect("Withdrawal exceeds available BeatHub platform balance.", True)
    withdrawal = AdminWithdrawal(
        amount=amount_val,
        phone_number=normalized_phone,
        status="pending",
        admin_note=(admin_note.strip() or note.strip() or None),
    )
    db.add(withdrawal)
    db.commit()
    return _admin_redirect("Admin withdrawal request created. Review it before sending the M-Pesa payout.")


@router.post("/admin/{withdrawal_id}/approve")
def approve_admin_withdrawal(withdrawal_id: str, db: Session = Depends(get_db), user=Depends(require_admin)):
    withdrawal = _admin_withdrawal_or_404(withdrawal_id, db)
    if str(withdrawal.status).lower() != "pending":
        return _admin_redirect("Only pending admin withdrawals can be approved.", True)
    withdrawal.status = "approved"
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = (withdrawal.admin_note or "") + " Approved by administrator."
    db.commit()
    return _admin_redirect("Admin withdrawal approved. It is ready to send to M-Pesa.")


@router.post("/admin/{withdrawal_id}/reject")
def reject_admin_withdrawal(
    withdrawal_id: str,
    note: str = Form(""),
    admin_note: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    withdrawal = _admin_withdrawal_or_404(withdrawal_id, db)
    if str(withdrawal.status).lower() in ("paid", "processing"):
        return _admin_redirect("A processing or paid admin withdrawal cannot be rejected.", True)
    withdrawal.status = "rejected"
    withdrawal.admin_note = (admin_note.strip() or note.strip() or "Admin withdrawal rejected.")
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.resolved_at = datetime.utcnow()
    db.commit()
    return _admin_redirect("Admin withdrawal rejected.")


@router.post("/admin/{withdrawal_id}/send")
def send_admin_withdrawal(withdrawal_id: str, db: Session = Depends(get_db), user=Depends(require_admin)):
    withdrawal = _admin_withdrawal_or_404(withdrawal_id, db)
    if str(withdrawal.status).lower() != "approved":
        return _admin_redirect("Only an approved admin withdrawal can be sent to M-Pesa.", True)
    _revenue, _withdrawn, _pending, available = _admin_financials(db)
    if withdrawal.amount > available + withdrawal.amount:
        return _admin_redirect("The requested payout is no longer covered by the available BeatHub balance.", True)
    if not (settings.PAYSTACK_SECRET_KEY or "").strip():
        return _admin_redirect("PAYSTACK_SECRET_KEY is not configured. Add your Paystack secret key in Render before sending money.", True)
    try:
        result = initiate_mpesa_transfer(
            Decimal(str(withdrawal.amount)),
            withdrawal.phone_number,
            reason="BeatHub platform earnings withdrawal",
            name="BeatHub Admin",
        )
    except PaystackTransferError as exc:
        return _admin_redirect(str(exc), True)
    withdrawal.status = "processing"
    withdrawal.payout_reference = result["reference"][:100]
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = f"Paystack M-Pesa transfer initiated. Transfer status: {result['status']}."
    db.commit()
    return _admin_redirect(f"M-Pesa transfer initiated. Reference: {result['reference']}")


@router.post("/admin/{withdrawal_id}/verify")
def verify_admin_withdrawal(withdrawal_id: str, db: Session = Depends(get_db), user=Depends(require_admin)):
    withdrawal = _admin_withdrawal_or_404(withdrawal_id, db)
    reference = (withdrawal.payout_reference or "").strip()
    if not reference:
        return _admin_redirect("This withdrawal has no Paystack transfer reference to verify.", True)
    try:
        result = verify_transfer(reference)
    except PaystackTransferError as exc:
        return _admin_redirect(str(exc), True)
    status = result["status"]
    if status == "success":
        withdrawal.status = "paid"
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = "Paystack confirmed the M-Pesa transfer as successful."
    elif status in {"failed", "reversed", "rejected"}:
        withdrawal.status = "rejected"
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = f"Paystack transfer status: {status}. {result.get('failures') or ''}".strip()
    else:
        withdrawal.status = "processing"
        withdrawal.admin_note = f"Paystack transfer status checked: {status or 'pending'}."
    withdrawal.updated_at = datetime.utcnow()
    db.commit()
    return _admin_redirect(f"Paystack transfer status: {status or 'pending'}.")


# ---------------------------------------------------------------------------
# Route injection
# ---------------------------------------------------------------------------
# main.py imports payout_admin before it includes admin.router. We therefore
# move these canonical creator + admin withdrawal routes into the legacy admin
# router at import time, while preserving every unrelated admin route.
try:
    from app.routers import admin as _legacy_admin

    _creator_and_admin_routes = list(router.routes)
    _legacy_admin.router.routes = [
        *[
            route
            for route in _legacy_admin.router.routes
            if not (
                str(getattr(route, "path", "")).startswith("/admin/withdrawals")
                or str(getattr(route, "path", "")) == "/admin/withdraw"
                or str(getattr(route, "path", "")).startswith("/admin/withdraw/")
            )
        ],
        *_creator_and_admin_routes,
    ]
    router.routes = []
except Exception:
    # Never prevent application startup merely because a compatibility import
    # is unavailable in a test/import context.
    pass
