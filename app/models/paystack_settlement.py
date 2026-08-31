"""Paystack settlement snapshots used for finance reconciliation."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

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
