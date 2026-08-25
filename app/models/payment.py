"""
BeatHub payment models.

Stores payment transactions associated with BeatHub orders.

IMPORTANT:
- PaymentTransaction represents the payment attempt.
- Order represents the purchase.
- License represents ownership.
- A payment must NOT grant ownership until the payment provider confirms success.
- callback_processed is persisted because production PostgreSQL already contains
  that non-null column and callback/webhook processing must be idempotent.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class PaymentTransaction(Base):
    """One payment attempt associated with one BeatHub order."""

    __tablename__ = "payment_transactions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    order_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    merchant_request_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )

    checkout_request_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True, index=True
    )

    mpesa_receipt_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True, index=True
    )

    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # PostgreSQL production deployments previously had a native enum with
    # incompatible labels. Keep this as a normal VARCHAR-backed SQLAlchemy
    # enum and explicitly persist the enum VALUES (lowercase), not member
    # names (PENDING/COMPLETED/FAILED).
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(
            PaymentStatus,
            name="paymentstatus",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=30,
        ),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )

    result_code: Mapped[int | None] = mapped_column(nullable=True)

    result_description: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    callback_processed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    order = relationship("Order", back_populates="payment_transaction")

    def __repr__(self) -> str:
        return (
            "<PaymentTransaction "
            f"{self.checkout_request_id} "
            f"{self.status}>"
        )
