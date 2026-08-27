"""Admin BeatHub platform withdrawals to M-Pesa via Paystack."""

import hashlib
import hmac
import logging
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import AdminWithdrawal
from app.routers.admin import get_admin_withdrawal_financials
from app.services.paystack_transfers import (
    PaystackTransferError,
    PaystackTransferNetworkError,
    create_mpesa_transfer,
)
from app.utils.deps import require_admin


router = APIRouter(prefix="/admin", tags=["admin-mpesa-payout"])
logger = logging.getLogger("beathub.admin_mpesa_payout")


def _redirect(message: str, error: bool = False):
    key = "error" if error else "success"
    return RedirectResponse(
        url=f"/admin/withdraw?{key}={quote(message)}",
        status_code=303,
    )


def _new_reference() -> str:
    return f"bh_admin_{uuid.uuid4().hex}"


@router.post("/withdraw")
async def confirm_and_send_admin_mpesa(
    amount: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(""),
    admin_note: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Confirm the admin withdrawal form and actually send the money to M-Pesa."""
    try:
        amount_value = Decimal((amount or "").strip())
    except (InvalidOperation, ValueError):
        return _redirect("Enter a valid withdrawal amount.", error=True)

    if not amount_value.is_finite() or amount_value < Decimal("1.00"):
        return _redirect("The M-Pesa payout must be at least KSh 1.00.", error=True)

    (
        _platform_revenue,
        _already_withdrawn,
        _pending_admin,
        available,
        _balance,
    ) = get_admin_withdrawal_financials(db)

    if amount_value > available:
        return _redirect(
            f"Insufficient BeatHub platform balance. Available to withdraw: KSh {available:.2f}.",
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
        phone_number=(phone_number or "").strip(),
        status="processing",
        payout_reference=reference,
        admin_note="Payout initiated; waiting for Paystack confirmation.",
    )
    db.add(withdrawal)
    db.commit()

    try:
        admin_name = (
            getattr(user, "username", None)
            or getattr(user, "email", None)
            or "BeatHub Admin"
        )

        result = await create_mpesa_transfer(
            amount=amount_value,
            phone_number=str(withdrawal.phone_number or ""),
            name=str(admin_name),
            reference=reference,
        )

        transfer_reference = str(result.get("reference") or reference)
        transfer_status = str(result.get("status") or "pending").lower()

        withdrawal.payout_reference = transfer_reference[:100]
        withdrawal.updated_at = datetime.utcnow()

        if transfer_status == "success":
            withdrawal.status = "paid"
            withdrawal.resolved_at = datetime.utcnow()
            withdrawal.admin_note = final_note[:500]
            message = (
                f"KSh {amount_value:.2f} was sent to M-Pesa successfully. "
                f"Paystack reference: {transfer_reference}."
            )
        else:
            withdrawal.status = "processing"
            withdrawal.admin_note = (
                f"{final_note[:350]} Paystack transfer is processing; "
                "waiting for transfer.success webhook."
            )
            message = (
                f"M-Pesa payout initiated for KSh {amount_value:.2f}. "
                f"Paystack reference: {transfer_reference}."
            )

        db.commit()
        return _redirect(message)

    except PaystackTransferNetworkError as exc:
        db.rollback()
        logger.warning("Ambiguous admin M-Pesa payout response for %s: %s", withdrawal.id, exc)
        failed = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal.id).first()
        if failed:
            failed.status = "processing"
            failed.updated_at = datetime.utcnow()
            failed.admin_note = "Paystack response was ambiguous. Check Paystack before retrying; BeatHub will not send the same payout twice automatically."
            db.commit()
        return _redirect(
            "Paystack did not return a definite response. The payout remains processing so BeatHub will not send the same money twice.",
            error=True,
        )
    except PaystackTransferError as exc:
        db.rollback()
        failed = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal.id).first()
        if failed:
            failed.status = "rejected"
            failed.updated_at = datetime.utcnow()
            failed.resolved_at = datetime.utcnow()
            failed.admin_note = f"Paystack payout failed: {str(exc)[:430]}"
            db.commit()
        logger.warning("Admin M-Pesa payout failed for %s: %s", withdrawal.id, exc)
        return _redirect(str(exc), error=True)
    except Exception:
        db.rollback()
        failed = db.query(AdminWithdrawal).filter(AdminWithdrawal.id == withdrawal.id).first()
        if failed:
            failed.status = "processing"
            failed.updated_at = datetime.utcnow()
            failed.admin_note = "Paystack payout request had an ambiguous server error. Check Paystack before retrying."
            db.commit()
        logger.exception("Unexpected admin M-Pesa payout error for %s", withdrawal.id)
        return _redirect(
            "The payout response was ambiguous. The withdrawal remains processing so the same money cannot be sent twice. Check Paystack before retrying.",
            error=True,
        )


@router.post("/withdraw/{withdrawal_id}/paid")
async def initiate_admin_mpesa_payout(
    withdrawal_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Initiate the actual admin M-Pesa payout for legacy pending records."""
    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(AdminWithdrawal.id == withdrawal_id)
        .with_for_update()
        .first()
    )

    if not withdrawal:
        raise HTTPException(status_code=404, detail="Admin withdrawal not found.")

    current_status = str(getattr(withdrawal.status, "value", withdrawal.status)).lower()

    if current_status == "paid":
        return _redirect("This withdrawal has already been paid. A second payout was not sent.", error=True)

    if current_status == "processing" and withdrawal.payout_reference:
        return _redirect("This M-Pesa payout has already been initiated and is awaiting Paystack confirmation.", error=True)

    if current_status not in {"pending", "approved"}:
        return _redirect(
            f"This withdrawal cannot be paid because its status is {current_status}.",
            error=True,
        )

    try:
        amount = Decimal(str(withdrawal.amount or 0))
        reference = _new_reference()
        withdrawal.status = "processing"
        withdrawal.payout_reference = reference
        withdrawal.updated_at = datetime.utcnow()
        db.commit()

        admin_name = (
            getattr(user, "username", None)
            or getattr(user, "email", None)
            or "BeatHub Admin"
        )

        result = await create_mpesa_transfer(
            amount=amount,
            phone_number=str(withdrawal.phone_number or ""),
            name=str(admin_name),
            reference=reference,
        )

        transfer_reference = str(result.get("reference") or reference)
        transfer_status = str(result.get("status") or "pending").lower()
        withdrawal.payout_reference = transfer_reference[:100]
        withdrawal.updated_at = datetime.utcnow()

        if transfer_status == "success":
            withdrawal.status = "paid"
            withdrawal.resolved_at = datetime.utcnow()
            withdrawal.admin_note = "Paystack M-Pesa transfer completed successfully."
            message = f"KSh {amount:.2f} was sent through Paystack to M-Pesa. Reference: {transfer_reference}."
        else:
            withdrawal.status = "processing"
            withdrawal.admin_note = "Paystack M-Pesa transfer initiated; waiting for transfer.success webhook."
            message = f"M-Pesa payout initiated for KSh {amount:.2f}. Paystack reference: {transfer_reference}."

        db.commit()
        return _redirect(message)

    except PaystackTransferNetworkError:
        db.rollback()
        return _redirect(
            "Paystack returned no definite payout response. The withdrawal remains processing; do not retry until Paystack is checked.",
            error=True,
        )
    except PaystackTransferError as exc:
        db.rollback()
        logger.warning("Admin M-Pesa payout failed for %s: %s", withdrawal_id, exc)
        return _redirect(str(exc), error=True)
    except Exception:
        db.rollback()
        logger.exception("Unexpected admin M-Pesa payout error for %s", withdrawal_id)
        return _redirect("The M-Pesa payout could not be initiated. The record remains protected from duplicate payout.", error=True)


@router.post("/withdraw/{withdrawal_id}/approve")
def approve_admin_withdrawal_for_payout(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Approve an admin withdrawal without pretending the money was paid."""
    withdrawal = (
        db.query(AdminWithdrawal)
        .filter(AdminWithdrawal.id == withdrawal_id)
        .first()
    )

    if not withdrawal:
        raise HTTPException(status_code=404, detail="Admin withdrawal not found.")

    current_status = str(getattr(withdrawal.status, "value", withdrawal.status)).lower()
    if current_status != "pending":
        return _redirect("Only pending admin withdrawals can be approved.", error=True)

    withdrawal.status = "approved"
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.admin_note = "Admin withdrawal approved; ready for M-Pesa payout."
    db.commit()
    return _redirect("Admin withdrawal approved. Confirm the payout to send it to M-Pesa.")


@router.post("/paystack/transfer-webhook")
async def paystack_transfer_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive Paystack transfer.success/failed/reversed events."""
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    secret = settings.PAYSTACK_SECRET_KEY or ""
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

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
        withdrawal.payout_reference = reference[:100]
        withdrawal.updated_at = datetime.utcnow()
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = "Paystack confirmed the M-Pesa transfer as successful."
    elif event == "transfer.reversed":
        withdrawal.status = "rejected"
        withdrawal.payout_reference = reference[:100]
        withdrawal.updated_at = datetime.utcnow()
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = "Paystack reversed the M-Pesa transfer; funds were returned to the Paystack balance."
    else:
        withdrawal.status = "rejected"
        withdrawal.payout_reference = reference[:100]
        withdrawal.updated_at = datetime.utcnow()
        withdrawal.resolved_at = datetime.utcnow()
        withdrawal.admin_note = "Paystack reported the M-Pesa transfer as failed."

    db.commit()
    return {"status": True}
