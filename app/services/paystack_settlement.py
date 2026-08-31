"""Paystack settlement ingestion and BeatHub payment reconciliation."""

from datetime import datetime
from decimal import Decimal
import logging

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.payment import PaymentStatus, PaymentTransaction
from app.models.paystack_settlement import PaystackSettlement, PaystackSettlementTransaction

logger = logging.getLogger("beathub.paystack.settlement")


class PaystackSettlementError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackSettlementError("PAYSTACK_SECRET_KEY is not configured.")
    return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}


def _money(value) -> Decimal:
    """Paystack monetary API values are returned in the currency subunit."""
    return (Decimal(str(value or 0)) / Decimal("100")).quantize(Decimal("0.01"))


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
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
            break
        page += 1
        if page > 1000:
            raise PaystackSettlementError("Paystack pagination exceeded the safety limit.")
    return results


async def reconcile_settlements(
    db: Session,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    status: str | None = None,
) -> dict:
    """Pull Paystack settlements and their transactions into an immutable audit trail.

    This never credits a creator wallet and never changes an order. Payment
    fulfillment remains webhook/verification driven. Reconciliation only
    answers: what did Paystack include in a settlement, and which BeatHub
    payment does that transaction correspond to?
    """
    params: dict[str, str] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    if status:
        params["status"] = status

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            settlements = await _list_all(client, "/settlement", params)
            settlement_count = 0
            transaction_count = 0
            matched = 0
            unmatched = 0
            amount_mismatch = 0
            currency_mismatch = 0
            non_success = 0

            for item in settlements:
                provider_id = str(item.get("id"))
                if not provider_id or provider_id == "None":
                    continue
                settlement = db.query(PaystackSettlement).filter(
                    PaystackSettlement.provider_settlement_id == provider_id
                ).one_or_none()
                if settlement is None:
                    settlement = PaystackSettlement(provider_settlement_id=provider_id)
                    db.add(settlement)

                settlement.status = str(item.get("status") or "unknown")
                settlement.currency = str(item.get("currency") or "").upper()[:3]
                settlement.total_amount = _money(item.get("total_amount"))
                settlement.effective_amount = _money(item.get("effective_amount"))
                settlement.total_fees = _money(item.get("total_fees"))
                settlement.total_processed = _money(item.get("total_processed"))
                settlement.settlement_date = _parse_datetime(item.get("settlement_date"))
                db.flush()
                settlement_count += 1

                txns = await _list_all(client, f"/settlement/{provider_id}/transactions")
                for item_tx in txns:
                    provider_tx_id = str(item_tx.get("id"))
                    reference = str(item_tx.get("reference") or "").strip()
                    if not provider_tx_id or not reference:
                        continue

                    record = db.query(PaystackSettlementTransaction).filter(
                        PaystackSettlementTransaction.provider_transaction_id == provider_tx_id
                    ).one_or_none()
                    if record is None:
                        record = PaystackSettlementTransaction(
                            settlement_id=settlement.id,
                            provider_transaction_id=provider_tx_id,
                            reference=reference,
                            status=str(item_tx.get("status") or "unknown"),
                            amount=_money(item_tx.get("amount")),
                            currency=str(item_tx.get("currency") or "").upper()[:3],
                        )
                        db.add(record)
                    else:
                        record.settlement_id = settlement.id
                        record.reference = reference
                        record.status = str(item_tx.get("status") or "unknown")
                        record.amount = _money(item_tx.get("amount"))
                        record.currency = str(item_tx.get("currency") or "").upper()[:3]

                    record.paid_at = _parse_datetime(item_tx.get("paid_at"))
                    record.reconciliation_status = "unmatched"
                    record.mismatch_reason = None
                    record.payment_transaction_id = None

                    if str(item_tx.get("status") or "").lower() != "success":
                        record.reconciliation_status = "non_success"
                        record.mismatch_reason = "Paystack settlement transaction is not marked successful."
                        non_success += 1
                    else:
                        payment = db.query(PaymentTransaction).filter(
                            PaymentTransaction.checkout_request_id == reference
                        ).one_or_none()
                        if payment is None:
                            record.reconciliation_status = "unmatched"
                            record.mismatch_reason = "No BeatHub payment transaction has this Paystack reference."
                            unmatched += 1
                        else:
                            record.payment_transaction_id = payment.id
                            expected_currency = str(payment.currency or "").upper()
                            actual_currency = str(item_tx.get("currency") or "").upper()
                            actual_amount = _money(item_tx.get("amount"))
                            expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"))
                            if expected_currency != actual_currency:
                                record.reconciliation_status = "currency_mismatch"
                                record.mismatch_reason = f"BeatHub expected {expected_currency}; Paystack settled {actual_currency}."
                                currency_mismatch += 1
                            elif expected_amount != actual_amount:
                                record.reconciliation_status = "amount_mismatch"
                                record.mismatch_reason = f"BeatHub recorded {expected_amount} {expected_currency}; Paystack settled {actual_amount} {actual_currency}."
                                amount_mismatch += 1
                            else:
                                record.reconciliation_status = "matched"
                                matched += 1
                    transaction_count += 1

                db.commit()

            return {
                "settlements": settlement_count,
                "transactions": transaction_count,
                "matched": matched,
                "unmatched": unmatched,
                "amount_mismatch": amount_mismatch,
                "currency_mismatch": currency_mismatch,
                "non_success": non_success,
            }
    except (httpx.HTTPError, PaystackSettlementError):
        db.rollback()
        logger.exception("Paystack settlement reconciliation failed")
        raise
    except Exception:
        db.rollback()
        logger.exception("Unexpected Paystack settlement reconciliation failure")
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
