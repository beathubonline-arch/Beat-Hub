"""Canonical BeatHub platform withdrawals to M-Pesa via Paystack.

This router is registered before the legacy admin router. It owns the live
/admin/withdraw GET/POST flow so the dashboard uses the platform ledger and
Paystack confirmation rather than a manual 'mark paid' action.
"""

import hashlib
import hmac
import logging
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import AdminWithdrawal
from app.services.paystack_transfers import (
    PaystackTransferError,
    PaystackTransferNetworkError,
    create_mpesa_transfer,
)
from app.services.platform_finance import (
    estimate_mpesa_transfer_fee,
    get_admin_withdrawal_financials,
    record_platform_withdrawal,
)
from app.utils.deps import require_admin


router = APIRouter(prefix="/admin", tags=["admin-mpesa-payout"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("beathub.admin_mpesa_payout")


def _redirect(message: str, error: bool = False):
    key = "error" if error else "success"
    return RedirectResponse(
        url=f"/admin/withdraw?{key}={quote(message)}",
        status_code=303,
    )


def _new_reference() -> str:
    return f"bh_admin_{uuid.uuid4().hex}"


def _status(value) -> str:
    return str(getattr(value, "value", value)).lower()


def _valid_phone(phone: str) -> bool:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("254"):
        return len(digits) == 12 and digits[3] == "7"
    if digits.startswith("0"):
        return len(digits) == 10 and digits[1] == "7"
    if digits.startswith("7"):
        return len(digits) == 9
    return False


@router.get("/withdraw")
def admin_withdraw_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Canonical platform-finance page."""
    financials = get_admin_withdrawal_financials(db)
    withdrawals = (
        db.query(AdminWithdrawal)
        .order_by(AdminWithdrawal.created_at.desc())
        .all()
    )

    # Paystack transfer fees are charged against the provider balance. Show a
    # conservative minimum fee so the admin cannot request the entire ledger
    # balance and then discover the provider cannot cover the transfer fee.
    minimum_fee = estimate_mpesa_transfer_fee(Decimal("1.00"))

    return templates.TemplateResponse(
        request,
        "admin/withdraw.html",
        {
            "request": request,
            "current_user": user,
            "current_year": datetime.utcnow().year,
            "platform_revenue": financials["platform_revenue"],
            "already_withdrawn": financials["ledger_debits"],
            "pending_admin": financials["reserved"],
            "available_balance": financials["available"],
            "ledger_credits": financials["ledger_credits"],
            "ledger_debits": financials["ledger_debits"],
            "withdrawal_reserved": financials["reserved"],
            "transfer_minimum_fee": minimum_fee,
            "withdrawals": withdrawals,
            "admin_withdrawals": withdrawals,
            "balance": type("Balance", (), {
                "commission_total": financials["platform_revenue"],
                "withdrawn_total": financials["ledger_debits"],
                "pending_total": financials["reserved"],
                "available_balance": financials["available"],
            })(),
        },
    )


@router.post("/withdraw")
async def confirm_and_send_admin_mpesa(
    amount: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(""),
    admin_note: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Validate, reserve and initiate a real BeatHub platform M-Pesa payout."""
    try:
        amount_value = Decimal((amount or "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return _redirect("Enter a valid withdrawal amount.", error=True)

    if not amount_value.is_finite() or amount_value < Decimal("1.00"):
        return _redirect("The M-Pesa payout must be at least KSh 1.00.", error=True)

    if amount_value > Decimal("150000.00"):
        return _redirect("A single Kenyan M-Pesa customer transfer cannot exceed KSh 150,000.", error=True)

    phone = (phone_number or "").strip()
    if not _valid_phone(phone):
        return _redirect("Enter a valid Kenyan M-Pesa number.", error=True)

    financials = get_admin_withdrawal_financials(db)
    available = financials["available"]
    transfer_fee = estimate_mpesa_transfer_fee(amount_value)

    if amount_value + transfer_fee > available:
        return _redirect(
            f"Insufficient available BeatHub platform funds. KSh {amount_value:.2f} plus an estimated KSh {transfer_fee:.2f} transfer fee exceeds the available KSh {available:.2f}.",
            error=True,
        )

    # Protect against accidental browser double-submit of the same request.
    recent_duplicate = (
        db.query(AdminWithdrawal)
        .filter(AdminWithdrawal.phone_number == phone)
        .filter(AdminWithdrawal.amount == amount_value)
        .filter(AdminWithdrawal.status.in_(["pending", "approved", "processing"]))
        .order_by(AdminWithdrawal.created_at.desc())
        .first()
    )
    if recent_duplicate and recent_duplicate.created_at:
        age_seconds = (datetime.utcnow() - recent_duplicate.created_at).total_seconds()
        if age_seconds <= 60:
            return _redirect(
                "A matching platform withdrawal was already submitted in the last minute. It remains protected from duplicate payout.",
                error=True,
            )

    reference = _new_reference()
    final_note = (
        (admin_note or "").strip()
        or (note or "").strip()
        or "BeatHub platform earnings withdrawal to M-Pesa."
    )

    withdrawal = AdminWithdrawal(
        amount=amount_value,
        phone_number=phone,
        status="processing",
        payout_reference=reference,
        admin_note="Payout initiated; waiting for Paystack confirmation.",
    )
    db.add(withdrawal)
    db.commit()

    try:
        admin_name = getattr(user, "username", None) or getattr(user, "email", None) or "BeatHub Admin"
        result = await create_mpesa_transfer(
            amount=amount_value,
            phone_number=phone,
            name=str(admin_name),
            reference=reference,
        )

        transfer_reference = str(result.get("reference") or reference)
        transfer_status = str(result.get("status") or "pending").lower()
        provider_fee = Decimal(str(result.get("fee") or transfer_fee))

        withdrawal.payout_reference = transfer_reference[:100]
        withdrawal.updated_at = datetime.utcnow()

        if transfer_status == "success":
            withdrawal.status = "paid"
            withdrawal.resolved_at = datetime.utcnow()
            withdrawal.admin_note = final_note[:500]
            record_platform_withdrawal(db, withdrawal, provider_fee)
            message = f"KSh {amount_value:.2f} was sent to M-Pesa successfully. Paystack reference: {transfer_reference}."
        else:
            withdrawal.status = "processing"
            withdrawal.admin_note = f"{final_note[:350]} Paystack transfer is processing; waiting for transfer.success webhook."
            message = f"M-Pesa payout initiated for KSh {amount_value:.2f}. Paystack reference: {transfer_reference}."

        db.commit()
        return _redirect(message)

    except PaystackTransferNetworkError as exc:
        db.rollback()
        logger.warning("Ambiguous admin M-Pesa payout response for %s: %s", withdrawal.id, exc)
        existing = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal.id).first()
        if existing:
            existing.status = "processing"
            existing.updated_at = datetime.utcnow()
            existing.admin_note = "Paystack response was ambiguous. Check Paystack before retrying; BeatHub will not send the same payout twice automatically."
            db.commit()
        return _redirect("Paystack returned an ambiguous response. The payout remains processing and is protected from duplicate sending.", error=True)
    except PaystackTransferError as exc:
        db.rollback()
        existing = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal.id).first()
        if existing:
            existing.status = "rejected"
            existing.updated_at = datetime.utcnow()
            existing.resolved_at = datetime.utcnow()
            existing.admin_note = f"Paystack payout failed: {str(exc)[:430]}"
            db.commit()
        logger.warning("Admin M-Pesa payout failed for %s: %s", withdrawal.id, exc)
        return _redirect(str(exc), error=True)
    except Exception:
        db.rollback()
        existing = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal.id).first()
        if existing:
            existing.status = "processing"
            existing.updated_at = datetime.utcnow()
            existing.admin_note = "Payout response was ambiguous. Check Paystack before retrying."
            db.commit()
        logger.exception("Unexpected admin M-Pesa payout error for %s", withdrawal.id)
        return _redirect("The payout response was ambiguous. The withdrawal remains processing so the same money cannot be sent twice.", error=True)


@router.post("/withdraw/{withdrawal_id}/approve")
def approve_admin_withdrawal_for_payout(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Approve a legacy/pending platform withdrawal without marking it paid."""
    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(AdminWithdrawal.id == withdrawal_id)
        .first()
    )
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Admin withdrawal not found.")

    if _status(withdrawal.status) != "pending":
        return _redirect("Only pending admin withdrawals can be approved.", error=True)

    withdrawal.status = "approved"
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = "Admin withdrawal approved; ready for M-Pesa payout."
    db.commit()
    return _redirect("Admin withdrawal approved. The payout must be initiated through the protected M-Pesa flow.")


@router.post("/withdraw/{withdrawal_id}/paid")
async def initiate_legacy_admin_mpesa_payout(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Legacy confirmation endpoint: initiate payout, never fabricate 'paid'."""
    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(AdminWithdrawal.id == withdrawal_id)
        .with_for_update()
        .first()
    )
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Admin withdrawal not found.")

    current = _status(withdrawal.status)
    if current == "paid":
        return _redirect("This withdrawal is already paid. No second payout was sent.", error=True)
    if current == "processing" and withdrawal.payout_reference:
        return _redirect("This payout has already been initiated and is awaiting Paystack confirmation. No second payout was sent.", error=True)
    if current not in {"pending", "approved"}:
        return _redirect(f"This withdrawal cannot be paid because its status is {current}.", error=True)

    amount = Decimal(str(withdrawal.amount or 0)).quantize(Decimal("0.01"))
    if amount <= 0 or amount > Decimal("150000.00"):
        return _redirect("The withdrawal amount is outside the Kenyan M-Pesa transfer limits.", error=True)
    if not _valid_phone(withdrawal.phone_number):
        return _redirect("The withdrawal has an invalid Kenyan M-Pesa number.", error=True)

    reference = _new_reference()
    withdrawal.status = "processing"
    withdrawal.payout_reference = reference
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = "Payout initiated; waiting for Paystack confirmation."
    db.commit()

    try:
        admin_name = getattr(user, "username", None) or getattr(user, "email", None) or "BeatHub Admin"
        result = await create_mpesa_transfer(
            amount=amount,
            phone_number=str(withdrawal.phone_number),
            name=str(admin_name),
            reference=reference,
        )
        transfer_reference = str(result.get("reference") or reference)
        transfer_status = str(result.get("status") or "pending").lower()
        provider_fee = Decimal(str(result.get("fee") or estimate_mpesa_transfer_fee(amount)))
        withdrawal.payout_reference = transfer_reference[:100]
        withdrawal.updated_at = datetime.utcnow()

        if transfer_status == "success":
            withdrawal.status = "paid"
            withdrawal.resolved_at = datetime.utcnow()
            withdrawal.admin_note = "Paystack confirmed the M-Pesa transfer successfully."
            record_platform_withdrawal(db, withdrawal, provider_fee)
            message = f"KSh {amount:.2f} was sent through Paystack to M-Pesa. Reference: {transfer_reference}."
        else:
            withdrawal.status = "processing"
            withdrawal.admin_note = "Paystack M-Pesa transfer initiated; waiting for transfer.success webhook."
            message = f"M-Pesa payout initiated for KSh {amount:.2f}. Paystack reference: {transfer_reference}."
        db.commit()
        return _redirect(message)
    except PaystackTransferNetworkError:
        db.rollback()
        return _redirect("Paystack returned an ambiguous response. The withdrawal remains processing; do not retry until Paystack is checked.", error=True)
    except PaystackTransferError as exc:
        db.rollback()
        existing = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal_id).first()
        if existing:
            existing.status = "rejected"
            existing.resolved_at = datetime.utcnow()
            existing.updated_at = datetime.utcnow()
            existing.admin_note = f"Paystack payout failed: {str(exc)[:430]}"
            db.commit()
        return _redirect(str(exc), error=True)


@router.post("/paystack/transfer-webhook")
async def paystack_transfer_webhook(request: Request, db: Session = Depends(get_db)):
    """Verify and process Paystack transfer.success/failed/reversed events."""
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    secret = settings.PAYSTACK_SECRET_KEY or ""
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid Paystack signature.")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.") from exc

    event = str(payload.get("event") or "").lower()
    if event not in {"transfer.success", "transfer.failed", "transfer.reversed"}:
        return {"status": True}

    data = payload.get("data") or {}
    reference = str(data.get("reference") or "").strip()
    if not reference:
        return {"status": True}

    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(AdminWithdrawal.payout_reference == reference)
        .with_for_update()
        .first()
    )
    if not withdrawal:
        return {"status": True}

    if event == "transfer.success":
        withdrawal.status = "paid"
        withdrawal.updated_at = datetime.utcnow()
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = "Paystack confirmed the M-Pesa transfer as successful."
        fee = Decimal(str(data.get("fee") or estimate_mpesa_transfer_fee(Decimal(str(withdrawal.amount)))))
        record_platform_withdrawal(db, withdrawal, fee)
    elif event == "transfer.reversed":
        withdrawal.status = "rejected"
        withdrawal.updated_at = datetime.utcnow()
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = "Paystack reversed the M-Pesa transfer; the reserved platform funds are released."
    else:
        withdrawal.status = "rejected"
        withdrawal.updated_at = datetime.utcnow()
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = "Paystack reported the M-Pesa transfer as failed; the reserved platform funds are released."

    db.commit()
    return {"status": True}
