"""Paystack settlement snapshots used for finance reconciliation."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PaystackSettlement(Base):
    """Immutable-ish local snapshot of a Paystack settlement record.

    A settlement snapshot is provider evidence, not a BeatHub balance credit.
    Reconciliation must never turn a settlement import into a second ledger
    credit.
    """

    __tablename__ = "paystack_settlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    paystack_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    settlement_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    transactions = relationship("PaystackSettlementTransaction", back_populates="settlement", cascade="all, delete-orphan")


class PaystackSettlementTransaction(Base):
    """Provider settlement transaction matched to BeatHub payment evidence."""

    __tablename__ = "paystack_settlement_transactions"
    __table_args__ = (UniqueConstraint("provider_transaction_id", name="uq_paystack_settlement_tx_provider_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    settlement_id: Mapped[str] = mapped_column(String(36), ForeignKey("paystack_settlements.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_transaction_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    reference: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_transaction_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("payment_transactions.id", ondelete="SET NULL"), nullable=True, index=True)
    reconciliation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unmatched", index=True)
    mismatch_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    settlement = relationship("PaystackSettlement", back_populates="transactions")
    payment_transaction = relationship("PaymentTransaction")
