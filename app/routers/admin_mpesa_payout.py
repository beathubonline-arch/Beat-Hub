"""Admin BeatHub platform withdrawals to M-Pesa via Paystack."""

import hashlib
import hmac
import logging
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.ledger import AdminWithdrawal
from app.services.paystack_transfers import PaystackTransferError, create_mpesa_transfer
from app.utils.deps import require_admin


router = APIRouter(prefix="/admin", tags=["admin-mpesa-payout"])
logger = logging.getLogger("beathub.admin_mpesa_payout")


def _redirect(message: str, error: bool = False):
    key = "error" if error else "success"
    return RedirectResponse(
        url=f"/admin/withdraw?{key}={quote(message)}",
        status_code=303,
    )


@router.post("/withdraw/{withdrawal_id}/paid")
async def initiate_admin_mpesa_payout(
    withdrawal_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Initiate the actual admin M-Pesa payout.

    The old route only changed the database status to paid. This route instead
    sends the funds through Paystack and only records `paid` when Paystack
    returns a conclusive success (normally test mode); live transfers remain
    `processing` until the transfer.success webhook arrives.
    """
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

    if current_status not in {"pending", "approved", "processing"}:
        return _redirect(
            f"This withdrawal cannot be paid because its status is {current_status}.",
            error=True,
        )

    try:
        amount = Decimal(str(withdrawal.amount or 0))
        if amount <= 0:
            raise PaystackTransferError("Withdrawal amount must be greater than zero.")

        admin_name = (
            getattr(user, "username", None)
            or getattr(user, "email", None)
            or "BeatHub Admin"
        )

        result = await create_mpesa_transfer(
            amount=amount,
            phone_number=str(withdrawal.phone_number or ""),
            name=str(admin_name),
        )

        reference = str(result["reference"])
        transfer_status = str(result.get("status") or "pending").lower()

        withdrawal.payout_reference = reference[:100]
        withdrawal.updated_at = datetime.utcnow()

        if transfer_status == "success":
            withdrawal.status = "paid"
            withdrawal.resolved_at = datetime.utcnow()
            withdrawal.admin_note = "Paystack M-Pesa transfer completed successfully."
            message = f"KSh {amount:.2f} was sent through Paystack to M-Pesa. Reference: {reference}."
        else:
            withdrawal.status = "processing"
            withdrawal.admin_note = "Paystack M-Pesa transfer initiated; waiting for transfer.success webhook."
            message = f"M-Pesa payout initiated for KSh {amount:.2f}. Paystack reference: {reference}."

        db.commit()
        return _redirect(message)

    except PaystackTransferError as exc:
        db.rollback()
        logger.warning("Admin M-Pesa payout failed for %s: %s", withdrawal_id, exc)
        return _redirect(str(exc), error=True)
    except Exception:
        db.rollback()
        logger.exception("Unexpected admin M-Pesa payout error for %s", withdrawal_id)
        return _redirect("The M-Pesa payout could not be initiated. No withdrawal was marked paid.", error=True)


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
        # Unknown references are acknowledged so Paystack does not repeatedly
        # retry an event that does not belong to BeatHub.
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
