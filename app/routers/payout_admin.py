from datetime import datetime

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ledger import WithdrawalRequest
from app.services.payout_policy import is_payout_window
from app.utils.deps import require_admin


router = APIRouter(prefix="/admin/withdrawals", tags=["admin-payouts"])


def _redirect(message: str, error: bool = False):
    from urllib.parse import quote

    key = "error" if error else "success"
    return RedirectResponse(
        url=f"/admin/withdrawals?{key}={quote(message)}",
        status_code=303,
    )


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

    withdrawal = (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.id == withdrawal_id)
        .first()
    )

    if not withdrawal:
        return _redirect("Withdrawal request not found.", error=True)

    if withdrawal.status != "approved":
        return _redirect(
            "Only approved creator withdrawals can be moved to processing.",
            error=True,
        )

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

    withdrawal = (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.id == withdrawal_id)
        .first()
    )

    if not withdrawal:
        return _redirect("Withdrawal request not found.", error=True)

    if withdrawal.status not in ("approved", "processing"):
        return _redirect(
            "Only approved or processing creator withdrawals can be marked paid.",
            error=True,
        )

    withdrawal.status = "paid"
    withdrawal.payout_reference = reference[:100]
    withdrawal.updated_at = datetime.utcnow()
    withdrawal.resolved_at = datetime.utcnow()
    withdrawal.admin_note = "M-Pesa payout completed and reference recorded."
    db.commit()

    return _redirect("Creator withdrawal marked as paid.")
