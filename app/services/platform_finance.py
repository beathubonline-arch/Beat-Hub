"""BeatHub platform financial ledger and balance calculations.

The platform ledger is separate from creator earnings and from Paystack's
provider balance. Positive entries are platform credits. Negative entries are
confirmed platform debits, including successful M-Pesa transfer fees.
"""

from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.ledger import AdminWithdrawal, PlatformLedgerEntry
from app.models.order import Order, OrderStatus

ZERO = Decimal("0.00")
RESERVED_STATUSES = ("pending", "approved", "processing")


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def estimate_mpesa_transfer_fee(amount: Decimal) -> Decimal:
    """Current Paystack Kenya M-Pesa customer-transfer fee schedule."""
    value = _decimal(amount)
    if value <= Decimal("1500.00"):
        return Decimal("20.00")
    if value <= Decimal("20000.00"):
        return Decimal("40.00")
    if value <= Decimal("150000.00"):
        return Decimal("60.00")
    return Decimal("0.00")


def lock_platform_withdrawal_reservation(db: Session) -> None:
    """Serialize platform withdrawal reservations on PostgreSQL.

    The available balance is derived from ledger credits/debits plus pending
    reservations. Without serialization, two concurrent admin submissions can
    both observe the same available balance and reserve/spend the same money.
    PostgreSQL transaction-level advisory locks make the check + reservation
    atomic without introducing a fake singleton accounting row. Other database
    engines retain the existing behavior for local development.
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(74182391)"))


def get_platform_financials(db: Session):
    """Return gross sales, platform commission and creator earnings."""
    completed = Order.status == OrderStatus.COMPLETED
    gross = db.query(func.coalesce(func.sum(Order.gross_amount), 0)).filter(completed).scalar()
    commission = db.query(func.coalesce(func.sum(Order.commission_amount), 0)).filter(completed).scalar()
    creator = db.query(func.coalesce(func.sum(Order.net_amount), 0)).filter(completed).scalar()
    return _decimal(gross), _decimal(commission), _decimal(creator)


def get_platform_ledger_totals(db: Session):
    credits = db.query(func.coalesce(func.sum(PlatformLedgerEntry.amount), 0)).filter(PlatformLedgerEntry.amount > 0).scalar()
    debits = db.query(func.coalesce(func.sum(-PlatformLedgerEntry.amount), 0)).filter(PlatformLedgerEntry.amount < 0).scalar()
    return _decimal(credits), _decimal(debits), _decimal(_decimal(credits) - _decimal(debits))


def get_admin_withdrawal_financials(db: Session):
    """Return platform revenue, confirmed debits, reservations and available ledger balance.

    Reservations include the expected Paystack M-Pesa transfer fee. This keeps
    the fee from being spent by a concurrent withdrawal while the payout is
    still processing. On success, the actual provider fee is recorded in the
    immutable platform ledger; on failure/reversal, the reservation disappears
    when the withdrawal leaves a reserved status.
    """
    _gross, order_commission, _creator = get_platform_financials(db)
    ledger_credits, ledger_debits, ledger_net = get_platform_ledger_totals(db)
    platform_revenue = ledger_credits if ledger_credits > ZERO else order_commission

    reserved_rows = (
        db.query(AdminWithdrawal.amount)
        .filter(AdminWithdrawal.status.in_(RESERVED_STATUSES))
        .all()
    )
    reserved = ZERO
    for (amount,) in reserved_rows:
        payout_amount = _decimal(amount)
        reserved += payout_amount + estimate_mpesa_transfer_fee(payout_amount)
    reserved = _decimal(reserved)

    available = platform_revenue - ledger_debits - reserved
    if available < ZERO:
        available = ZERO

    return {
        "platform_revenue": _decimal(platform_revenue),
        "ledger_credits": _decimal(ledger_credits),
        "ledger_debits": _decimal(ledger_debits),
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
            provider="paystack",
            description=f"BeatHub commission from order {order.order_number}",
        )
    )
    return True


def record_platform_withdrawal(
    db: Session,
    withdrawal: AdminWithdrawal,
    provider_fee: Decimal = ZERO,
) -> bool:
    """Create exactly one debit after Paystack confirms a platform withdrawal."""
    existing = (
        db.query(PlatformLedgerEntry)
        .filter(PlatformLedgerEntry.admin_withdrawal_id == withdrawal.id)
        .filter(PlatformLedgerEntry.entry_type == "platform_withdrawal")
        .first()
    )
    if existing:
        return False

    fee = _decimal(provider_fee)
    if fee < ZERO:
        raise ValueError("Provider fee cannot be negative.")

    total_debit = _decimal(withdrawal.amount) + fee
    db.add(
        PlatformLedgerEntry(
            amount=-total_debit,
            entry_type="platform_withdrawal",
            admin_withdrawal_id=withdrawal.id,
            provider="paystack",
            provider_reference=withdrawal.payout_reference,
            description=(
                f"BeatHub platform withdrawal {withdrawal.id}; "
                f"M-Pesa payout KSh {_decimal(withdrawal.amount):.2f}; fee KSh {fee:.2f}"
            ),
        )
    )
    return True
