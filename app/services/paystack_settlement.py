"""Paystack settlement ingestion and BeatHub payment reconciliation."""

import json
import logging
from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.payment import PaymentTransaction
from app.models.paystack_settlement import PaystackSettlement, PaystackSettlementTransaction

logger = logging.getLogger("beathub.paystack.settlement")


class PaystackSettlementError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackSettlementError("PAYSTACK_SECRET_KEY is not configured.")
    return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}


def _money(value) -> Decimal:
    """Convert Paystack integer subunits to major currency units."""
    return (Decimal(str(value or 0)) / Decimal("100")).quantize(Decimal("0.01"))


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    response = await client.get(
        f"{settings.PAYSTACK_BASE_URL.rstrip('/')}/{path.lstrip('/')}",
        headers=_headers(),
        params=params,
    )
    try:
        payload = response.json()
    except Exception as exc:
        raise PaystackSettlementError(f"Paystack returned a non-JSON response ({response.status_code}).") from exc
    if response.status_code >= 400 or not payload.get("status"):
        raise PaystackSettlementError(payload.get("message") or f"Paystack API request failed ({response.status_code}).")
    return payload


async def _list_all(client: httpx.AsyncClient, path: str, base_params: dict | None = None) -> list[dict]:
    results: list[dict] = []
    page = 1
    while True:
        params = dict(base_params or {})
        params.update({"perPage": 100, "page": page})
        payload = await _get(client, path, params)
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise PaystackSettlementError(f"Unexpected Paystack response for {path}.")
        results.extend(data)
        if len(data) < 100:
            return results
        page += 1
        if page > 1000:
            raise PaystackSettlementError("Paystack pagination exceeded the safety limit.")


async def reconcile_settlements(db: Session, *, from_date: str | None = None, to_date: str | None = None, status: str | None = None) -> dict:
    """Import provider settlement evidence and match it to existing payments.

    Reconciliation is audit-only. It never finalizes orders, credits wallets,
    creates earnings, or triggers withdrawals.
    """
    params = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    if status:
        params["status"] = status

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            settlements = await _list_all(client, "/settlement", params)
            totals = {"settlements": 0, "transactions": 0, "matched": 0, "unmatched": 0, "amount_mismatch": 0, "currency_mismatch": 0, "non_success": 0}

            for item in settlements:
                provider_id = str(item.get("id") or "").strip()
                if not provider_id:
                    continue
                settlement = db.query(PaystackSettlement).filter(PaystackSettlement.paystack_id == provider_id).one_or_none()
                if settlement is None:
                    settlement = PaystackSettlement(paystack_id=provider_id)
                    db.add(settlement)
                settlement.status = str(item.get("status") or "unknown")
                settlement.currency = str(item.get("currency") or "").upper()[:3]
                settlement.total_amount = _money(item.get("total_amount"))
                settlement.settlement_date = _parse_datetime(item.get("settlement_date"))
                settlement.raw_payload = json.dumps(item, separators=(",", ":"), default=str)
                settlement.last_seen_at = datetime.utcnow()
                db.flush()
                totals["settlements"] += 1

                transactions = await _list_all(client, f"/settlement/{provider_id}/transactions")
                for item_tx in transactions:
                    provider_tx_id = str(item_tx.get("id") or "").strip()
                    reference = str(item_tx.get("reference") or "").strip()
                    if not provider_tx_id or not reference:
                        continue
                    record = db.query(PaystackSettlementTransaction).filter(PaystackSettlementTransaction.provider_transaction_id == provider_tx_id).one_or_none()
                    if record is None:
                        record = PaystackSettlementTransaction(provider_transaction_id=provider_tx_id, settlement_id=settlement.id)
                        db.add(record)
                    record.settlement_id = settlement.id
                    record.reference = reference
                    record.status = str(item_tx.get("status") or "unknown")
                    record.amount = _money(item_tx.get("amount"))
                    record.currency = str(item_tx.get("currency") or "").upper()[:3]
                    record.paid_at = _parse_datetime(item_tx.get("paid_at"))
                    record.payment_transaction_id = None
                    record.reconciliation_status = "unmatched"
                    record.mismatch_reason = None

                    if record.status.lower() != "success":
                        record.reconciliation_status = "non_success"
                        record.mismatch_reason = "Paystack settlement transaction is not marked successful."
                        totals["non_success"] += 1
                    else:
                        payment = db.query(PaymentTransaction).filter(PaymentTransaction.checkout_request_id == reference).one_or_none()
                        if payment is None:
                            record.mismatch_reason = "No BeatHub payment transaction has this Paystack reference."
                            totals["unmatched"] += 1
                        else:
                            record.payment_transaction_id = payment.id
                            expected_currency = str(payment.currency or "").upper()
                            actual_currency = record.currency
                            expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"))
                            if expected_currency != actual_currency:
                                record.reconciliation_status = "currency_mismatch"
                                record.mismatch_reason = f"BeatHub expected {expected_currency}; Paystack settled {actual_currency}."
                                totals["currency_mismatch"] += 1
                            elif expected_amount != record.amount:
                                record.reconciliation_status = "amount_mismatch"
                                record.mismatch_reason = f"BeatHub recorded {expected_amount} {expected_currency}; Paystack settled {record.amount} {actual_currency}."
                                totals["amount_mismatch"] += 1
                            else:
                                record.reconciliation_status = "matched"
                                totals["matched"] += 1
                    totals["transactions"] += 1
                db.commit()
            return totals
    except Exception:
        db.rollback()
        logger.exception("Paystack settlement reconciliation failed")
        raise


def reconciliation_summary(db: Session) -> dict:
    rows = db.query(PaystackSettlementTransaction.reconciliation_status).all()
    counts: dict[str, int] = {}
    for (status,) in rows:
        key = str(status or "unknown")
        counts[key] = counts.get(key, 0) + 1
    latest = db.query(PaystackSettlement).order_by(PaystackSettlement.settlement_date.desc().nullslast()).first()
    return {
        "transaction_counts": counts,
        "total_settlement_transactions": len(rows),
        "latest_settlement_date": latest.settlement_date.isoformat() if latest and latest.settlement_date else None,
    }
