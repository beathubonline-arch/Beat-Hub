"""BeatHub platform financial ledger and balance calculations.

The platform ledger is separate from creator earnings and from Paystack's
provider balance. Positive entries are platform credits (for example,
commission earned on a confirmed sale). Negative entries are platform debits
(for example, a successfully completed admin withdrawal).

Pending/processing admin withdrawals reserve funds but do not become a
financial debit until Paystack confirms success.
"""

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ledger import AdminWithdrawal, PlatformLedgerEntry
from app.models.order import Order, OrderStatus

ZERO = Decimal("0.00")
RESERVED_STATUSES = ("pending", "approved", "processing")


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def get_platform_financials(db: Session):
    """Return gross sales, platform commission and creator earnings.

    Completed orders remain the reporting source for historical sales totals.
    The platform ledger is the authoritative source for platform cash credits.
    """
    completed = Order.status == OrderStatus.COMPLETED

    gross = db.query(func.coalesce(func.sum(Order.gross_amount), 0)).filter(completed).scalar()
    commission = db.query(func.coalesce(func.sum(Order.commission_amount), 0)).filter(completed).scalar()
    creator = db.query(func.coalesce(func.sum(Order.net_amount), 0)).filter(completed).scalar()

    return _decimal(gross), _decimal(commission), _decimal(creator)


def get_platform_ledger_totals(db: Session):
    """Return total platform credits, total confirmed debits and net ledger."""
    credits = (
        db.query(func.coalesce(func.sum(PlatformLedgerEntry.amount), 0))
        .filter(PlatformLedgerEntry.amount > 0)
        .scalar()
    )
    debits = (
        db.query(func.coalesce(func.sum(-PlatformLedgerEntry.amount), 0))
        .filter(PlatformLedgerEntry.amount < 0)
        .scalar()
    )
    return _decimal(credits), _decimal(debits), _decimal(_decimal(credits) - _decimal(debits))


def get_admin_withdrawal_financials(db: Session):
    """Return platform revenue, confirmed debits, reservations and available ledger balance."""
    _gross, order_commission, _creator = get_platform_financials(db)
    ledger_credits, ledger_debits, ledger_net = get_platform_ledger_totals(db)

    # Fresh production databases use the ledger. During a safe migration from
    # trial data, fall back to completed-order commission if no ledger credits
    # exist yet, so the dashboard never silently displays zero.
    platform_revenue = ledger_credits if ledger_credits > ZERO else order_commission

    paid_debits = ledger_debits
    reserved_raw = (
        db.query(func.coalesce(func.sum(AdminWithdrawal.amount), 0))
        .filter(AdminWithdrawal.status.in_(RESERVED_STATUSES))
        .scalar()
    )
    reserved = _decimal(reserved_raw)

    available = platform_revenue - paid_debits - reserved
    if available < ZERO:
        available = ZERO

    return {
        "platform_revenue": _decimal(platform_revenue),
        "ledger_credits": _decimal(ledger_credits),
        "ledger_debits": _decimal(paid_debits),
        "reserved": reserved,
        "available": _decimal(available),
        "ledger_net": _decimal(ledger_net),
        "order_commission": order_commission,
    }


def record_platform_commission(db: Session, order: Order) -> bool:
    """Create exactly one platform commission credit for a completed order."""
    if order.status != OrderStatus.COMPLETED:
        return False

    existing = (
        db.query(PlatformLedgerEntry)
        .filter(PlatformLedgerEntry.order_id == order.id)
        .filter(PlatformLedgerEntry.entry_type == "platform_commission")
        .first()
    )
    if existing:
        return False

    db.add(
        PlatformLedgerEntry(
            amount=_decimal(order.commission_amount),
            entry_type="platform_commission",
            order_id=order.id,
            description=f"BeatHub commission from order {order.order_number}",
        )
    )
    return True


def record_platform_withdrawal(db: Session, withdrawal: AdminWithdrawal) -> bool:
    """Create exactly one debit after Paystack confirms a platform withdrawal."""
    existing = (
        db.query(PlatformLedgerEntry)
        .filter(PlatformLedgerEntry.admin_withdrawal_id == withdrawal.id)
        .filter(PlatformLedgerEntry.entry_type == "platform_withdrawal")
        .first()
    )
    if existing:
        return False

    db.add(
        PlatformLedgerEntry(
            amount=-_decimal(withdrawal.amount),
            entry_type="platform_withdrawal",
            admin_withdrawal_id=withdrawal.id,
            provider="paystack",
            provider_reference=withdrawal.payout_reference,
            description=f"BeatHub platform withdrawal {withdrawal.id}",
        )
    )
    return True
