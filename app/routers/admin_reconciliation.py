"""Admin-only Paystack settlement reconciliation endpoints."""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.paystack_reconciliation import build_reconciliation, sync_paystack_settlements
from app.utils.deps import require_admin

router = APIRouter(prefix="/admin/finance/reconciliation", tags=["admin-finance"])


@router.get("")
@router.get("/")
def reconciliation_report(
    currency: str = "KES",
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Return the latest locally stored settlement reconciliation."""
    currency = (currency or "KES").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(status_code=400, detail="Invalid currency code.")
    return build_reconciliation(db, currency)


@router.post("/sync")
async def reconciliation_sync(
    currency: str = "KES",
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Synchronize Paystack settlements, then return the reconciliation report."""
    currency = (currency or "KES").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(status_code=400, detail="Invalid currency code.")
    try:
        sync_result = await sync_paystack_settlements(db)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Paystack settlement API returned HTTP {exc.response.status_code}.") from exc
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"sync": sync_result, "reconciliation": build_reconciliation(db, currency)}
