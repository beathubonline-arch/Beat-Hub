"""Admin-only Paystack settlement reconciliation controls."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.paystack_settlement import PaystackSettlementError, reconciliation_summary, reconcile_settlements
from app.utils.deps import require_admin

router = APIRouter(prefix="/admin/paystack", tags=["admin-paystack-reconciliation"])


@router.get("/reconciliation")
def get_reconciliation_summary(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    return {"status": True, **reconciliation_summary(db)}


@router.post("/reconciliation/sync")
async def sync_reconciliation(
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    if status and status not in {"success", "processing", "pending", "failed"}:
        raise HTTPException(status_code=400, detail="Invalid Paystack settlement status filter.")
    try:
        result = await reconcile_settlements(db, from_date=from_date, to_date=to_date, status=status)
    except PaystackSettlementError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": True, "message": "Paystack settlement reconciliation completed.", **result}
